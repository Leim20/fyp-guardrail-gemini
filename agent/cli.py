"""Chat with the NovaMart agent in the terminal as a logged-in customer, and watch every
checkpoint, tool call and guard verdict as it happens.

Usage:
    uv run python -m agent.cli                                   # customer C1001, DeepSeek, no guard
    uv run python -m agent.cli --doc forum_refund_tips.md        # search_kb always returns this document
    uv run python -m agent.cli --backend ollama --mode live
    uv run python -m agent.cli --guard full --doc forum_sms_tracking.md   # with the hybrid guardrail
"""

import argparse
import json
import uuid

from agent.llm import BACKENDS, load_backend
from agent.loop import Conversation
from agent.tools import ToolEnvironment
from guardrail.guard import VARIANTS, load_guard
from guardrail.interface import NoGuard, ToolCall

# "none" is the unguarded baseline; the rest are the hybrid guard and its ablation variants.
GUARDS = {"none": NoGuard} | {name: (lambda name=name: load_guard(name)) for name in VARIANTS}
DIM, BOLD, RED, GREEN, YELLOW, CYAN, RESET = "\033[2m", "\033[1m", "\033[31m", "\033[32m", "\033[33m", "\033[36m", "\033[0m"
VERDICT_COLOUR = {"ALLOW": GREEN, "SANITIZE": YELLOW, "ASK_CONFIRM": YELLOW, "BLOCK": RED}


def show(event: dict) -> None:
    verdict = str(event.get("verdict", ""))
    tag = f"{VERDICT_COLOUR.get(verdict, '')}{verdict}{RESET}" if verdict else ""
    reasons = f" {DIM}{', '.join(event.get('reasons') or [])}{RESET}" if event.get("reasons") else ""
    kind = event["type"]
    if kind == "input_check":
        trust = f" ({event['trust']})" if event.get("trust") else ""
        print(f"  {DIM}input  {event['source']}{trust}{RESET} {tag}{reasons}")
    elif kind == "tool_call":
        print(f"  {CYAN}tool   {event['tool']}({json.dumps(event['args'], ensure_ascii=False)}){RESET} {tag}{reasons}")
    elif kind == "confirmation":
        print(f"  {DIM}confirm {event['tool']}: {'approved' if event['approved'] else 'declined'}{RESET}")
    elif kind == "tool_result":
        preview = event["text"].replace("\n", " ")
        print(f"  {DIM}result {preview[:160]}{'...' if len(preview) > 160 else ''}{RESET}")
    elif kind == "output_check":
        print(f"  {DIM}output{RESET} {tag}{reasons}")


def ask_customer(call: ToolCall) -> bool:
    answer = input(f"  {YELLOW}The assistant wants to run {call.tool}({json.dumps(call.args, ensure_ascii=False)}). "
                   f"Approve? [y/N]{RESET} ")
    return answer.strip().lower() == "y"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--customer", default="C1001")
    parser.add_argument("--backend", choices=list(BACKENDS), default="deepseek")
    parser.add_argument("--guard", choices=list(GUARDS), default="none")
    parser.add_argument("--mode", choices=["live", "cache", "replay"], default="live")
    parser.add_argument("--doc", action="append", help="KB document that search_kb returns (repeatable)")
    args = parser.parse_args()

    env = ToolEnvironment(args.customer, pinned_docs=args.doc)
    convo = Conversation(load_backend(args.backend, args.mode), env, f"cli-{uuid.uuid4().hex[:8]}",
                         guard=GUARDS[args.guard](), confirm=ask_customer, on_event=show)
    name = env.db.customers[args.customer].name
    print(f"{BOLD}NovaMart support{RESET} - logged in as {name} ({args.customer}), backend {args.backend}, "
          f"guard {args.guard}. Empty line to quit.\n")
    while (message := input(f"{BOLD}you>{RESET} ").strip()):
        reply = convo.send(message)
        print(f"{BOLD}nova>{RESET} {reply}\n")
    print(f"{DIM}tokens: {convo.usage}; side effects: {[(e.tool, e.args) for e in env.effects]}{RESET}")


if __name__ == "__main__":
    main()
