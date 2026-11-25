"""DeepSeek teacher: an OpenAI-compatible client with the three call shapes the distillation
pipeline uses, each returning a usage record so the run's cost can be reported.

Thinking mode is the API default, so the non-thinking calls switch it off explicitly.
"""

import json
import math
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

ROOT = Path(__file__).resolve().parents[2]

GENERATOR = "deepseek-flash"  # DeepSeek-V4.1-Flash
VERIFIER = "deepseek-v4-pro"  # larger model, so the re-label is not the generator checking itself
# US$ per 1M tokens at peak rates, read 2026-11-02 (off-peak is half):
# (input cache hit, input cache miss, output)
PRICES = {"deepseek-flash": (0.006, 0.30, 1.20), "deepseek-v4-pro": (0.044, 1.32, 3.96)}
NO_THINKING = {"thinking": {"type": "disabled"}}


def usage_record(model: str, usage) -> dict:
    hit = getattr(usage, "prompt_cache_hit_tokens", 0) or 0
    hit_price, miss_price, out_price = PRICES[model]
    cost = (hit * hit_price + (usage.prompt_tokens - hit) * miss_price + usage.completion_tokens * out_price) / 1e6
    return {"model": model, "prompt_tokens": usage.prompt_tokens, "cache_hit_tokens": hit,
            "completion_tokens": usage.completion_tokens, "usd_peak": cost}


def parse_json_object(content: str) -> dict:
    """The JSON object in a reply, tolerating a code fence or text around it."""
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if not match:
        raise ValueError(f"no JSON object in reply: {content[:200]!r}")
    return json.loads(match.group(0))


class Teacher:
    def __init__(self):
        load_dotenv(ROOT / ".env")
        self.client = OpenAI(api_key=os.environ["DEEPSEEK_API_KEY"],
                             base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                             max_retries=5, timeout=600)

    @staticmethod
    def _messages(system: str, user: str) -> list[dict]:
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def generate_json(self, system: str, user: str, model: str = GENERATOR, temperature: float = 1.0,
                      max_tokens: int = 8000) -> tuple[dict, dict]:
        """Non-thinking call in JSON output mode."""
        resp = self.client.chat.completions.create(
            model=model, messages=self._messages(system, user), temperature=temperature, max_tokens=max_tokens,
            response_format={"type": "json_object"}, extra_body=NO_THINKING)
        choice = resp.choices[0]
        usage = usage_record(model, resp.usage) | {"finish_reason": choice.finish_reason}
        return parse_json_object(choice.message.content), usage

    def p_yes(self, system: str, user: str, model: str = VERIFIER) -> tuple[float, dict]:
        """P(answer is "yes") from the first token's top-20 log-probabilities, renormalised over
        yes/no (case and leading space folded). NaN if neither word is among the candidates."""
        resp = self.client.chat.completions.create(
            model=model, messages=self._messages(system, user), max_tokens=1, temperature=0,
            logprobs=True, top_logprobs=20, extra_body=NO_THINKING)
        mass = {"yes": 0.0, "no": 0.0}
        for alt in resp.choices[0].logprobs.content[0].top_logprobs:
            word = alt.token.strip().lower()
            if word in mass:
                mass[word] += math.exp(alt.logprob)
        total = mass["yes"] + mass["no"]
        usage = usage_record(model, resp.usage) | {"yes_no_mass": round(total, 4)}
        return (mass["yes"] / total if total > 0 else math.nan), usage

    def think(self, system: str, user: str, model: str = VERIFIER, max_tokens: int = 16000) -> tuple[dict, str, dict]:
        """Thinking-mode call; returns the parsed JSON answer and the model's reasoning text."""
        resp = self.client.chat.completions.create(
            model=model, messages=self._messages(system, user), max_tokens=max_tokens,
            extra_body={"thinking": {"type": "enabled"}, "reasoning_effort": "high"})
        message = resp.choices[0].message
        reasoning = getattr(message, "reasoning_content", None) or (message.model_extra or {}).get("reasoning_content", "")
        return parse_json_object(message.content), reasoning or "", usage_record(model, resp.usage)
