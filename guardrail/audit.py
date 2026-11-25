"""Audit log: one JSON line per guard decision (FR06).

Each record says what was checked, the verdict, which layer decided and why, the layer scores
and the added latency. The checked text itself is not stored: a SHA-256 lets an investigator
match a record to a known text, and the short preview has email addresses, phone numbers and
secret-like strings masked, so the log never becomes a second copy of customer PII or keys.
"""

import hashlib
import json
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PREVIEW_CHARS = 120
_REDACTIONS = [
    (re.compile(r"\b(sk|pk|api|key|token)[-_][A-Za-z0-9_\-]{12,}\b", re.IGNORECASE), "[secret]"),
    (re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+"), "[email]"),
    (re.compile(r"\+?\d[\d\s\-()]{7,}\d"), "[phone]"),
]


def redact(text: str) -> str:
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def fingerprint(text: str) -> dict[str, Any]:
    flat = " ".join(text.split())
    return {"sha256": hashlib.sha256(text.encode()).hexdigest()[:16], "chars": len(text),
            "preview": redact(flat[:PREVIEW_CHARS])}


class AuditLog:
    """Appends records to a JSONL file; path None keeps them in memory only (tests, ablations)."""

    def __init__(self, path: Path | None):
        self.path = path
        self.records: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, **record: Any) -> dict[str, Any]:
        record = {"time": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), **record}
        with self._lock:
            self.records.append(record)
            if self.path:
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        return record


class Timer:
    def __enter__(self):
        self.start = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.ms = round((time.perf_counter() - self.start) * 1000, 2)
