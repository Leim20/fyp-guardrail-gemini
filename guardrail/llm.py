"""LLM backends: any OpenAI-compatible chat API with function calling.

The client lives in the engine because the guard's own judge (guardrail/judge.py) needs one. An
application may reuse it for its agent (agent/llm.py re-exports it), but the engine never
imports the application.

DeepSeek and a local Ollama server both speak this API, so one class covers both; only the
base URL, key and model change. Every backend can be wrapped in record/replay:

    live    call the API every time
    cache   reuse a recorded reply for an identical request, otherwise call and record it
    replay  only use recordings; a request that was never recorded is an error

A recording is keyed by a hash of (model, messages, tools), so a rerun of the same scenario
with the same prompts replays the same replies: evaluation reruns are free and reproducible.
"""

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from dotenv import load_dotenv
from openai import OpenAI

ROOT = Path(__file__).resolve().parent.parent
RECORDINGS_DIR = ROOT / "data" / "recordings"
RecordMode = Literal["live", "cache", "replay"]


@dataclass
class AssistantTurn:
    """The LLM's reply: text, tool calls, or both. Tool arguments stay as the raw JSON string
    the model produced, so malformed arguments reach the guard exactly as generated."""
    content: str | None
    tool_calls: list[dict[str, str]] = field(default_factory=list)  # {"id", "name", "arguments"}
    usage: dict[str, int] = field(default_factory=dict)

    def as_message(self) -> dict[str, Any]:
        message: dict[str, Any] = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            message["tool_calls"] = [{"id": c["id"], "type": "function",
                                      "function": {"name": c["name"], "arguments": c["arguments"]}}
                                     for c in self.tool_calls]
        return message


class ChatBackend(Protocol):
    name: str
    model: str

    def complete(self, messages: list[dict], tools: list[dict]) -> AssistantTurn: ...


class OpenAICompatible:
    def __init__(self, name: str, model: str, base_url: str, api_key: str, extra_body: dict | None = None,
                 temperature: float = 0.0):
        self.name, self.model, self.temperature = name, model, temperature
        self.extra_body = extra_body or {}
        self.client = OpenAI(api_key=api_key, base_url=base_url, max_retries=3, timeout=120)

    def complete(self, messages: list[dict], tools: list[dict]) -> AssistantTurn:
        resp = self.client.chat.completions.create(
            model=self.model, messages=messages, tools=tools or None, temperature=self.temperature,
            extra_body=self.extra_body)
        message = resp.choices[0].message
        calls = [{"id": c.id, "name": c.function.name, "arguments": c.function.arguments}
                 for c in message.tool_calls or []]
        usage = {"prompt_tokens": resp.usage.prompt_tokens, "completion_tokens": resp.usage.completion_tokens}
        return AssistantTurn(message.content, calls, usage)


def deepseek() -> OpenAICompatible:
    load_dotenv(ROOT / ".env")
    # Non-thinking mode: an agent turn should be fast, and thinking ignores temperature.
    return OpenAICompatible("deepseek", os.environ.get("DEEPSEEK_MODEL") or "deepseek-flash",
                            os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                            os.environ["DEEPSEEK_API_KEY"], extra_body={"thinking": {"type": "disabled"}})


def ollama() -> OpenAICompatible:
    load_dotenv(ROOT / ".env")
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    return OpenAICompatible("ollama", os.environ["OLLAMA_MODEL"], f"{host}/v1", "ollama")


BACKENDS = {"deepseek": deepseek, "ollama": ollama}


class Recorded:
    """Record/replay wrapper around any backend."""

    def __init__(self, backend: ChatBackend, mode: RecordMode = "cache", directory: Path = RECORDINGS_DIR):
        self.backend, self.mode = backend, mode
        self.name, self.model = backend.name, backend.model
        self.directory = directory / backend.name

    def key(self, messages: list[dict], tools: list[dict]) -> str:
        blob = json.dumps({"model": self.model, "messages": messages, "tools": tools}, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode()).hexdigest()[:24]

    def complete(self, messages: list[dict], tools: list[dict]) -> AssistantTurn:
        if self.mode == "live":
            return self.backend.complete(messages, tools)
        path = self.directory / f"{self.key(messages, tools)}.json"
        if path.exists():
            return AssistantTurn(**json.loads(path.read_text())["reply"])
        if self.mode == "replay":
            raise LookupError(f"no recording for this request ({path.name}); rerun with mode 'cache'")
        turn = self.backend.complete(messages, tools)
        self.directory.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"model": self.model, "messages": messages,
                                    "reply": turn.__dict__}, indent=1, ensure_ascii=False))
        return turn


def load_backend(name: str, mode: RecordMode = "cache") -> ChatBackend:
    return Recorded(BACKENDS[name](), mode)
