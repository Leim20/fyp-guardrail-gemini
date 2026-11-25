"""LLM backends for the agent. The OpenAI-compatible client and its record/replay wrapper are
shared with the guard's judge, so they live in guardrail/llm.py; this module re-exports them
under the names the agent has always used."""

from guardrail.llm import (BACKENDS, RECORDINGS_DIR, AssistantTurn, ChatBackend, OpenAICompatible, Recorded,
                           RecordMode, deepseek, load_backend, ollama)

__all__ = ["BACKENDS", "RECORDINGS_DIR", "AssistantTurn", "ChatBackend", "OpenAICompatible", "Recorded",
           "RecordMode", "deepseek", "load_backend", "ollama"]
