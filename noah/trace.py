from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path


class TraceStore:
    """Append-only JSONL log of every routing decision and expert result."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or os.environ.get("NOAH_TRACES", "traces/traces.jsonl"))
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, record: dict) -> None:
        record = {"ts": time.time(), **record}
        try:
            with self.path.open("a") as f:
                f.write(json.dumps(record, default=str) + "\n")
        except OSError as e:  # a read-only or unmapped volume must not fail the user's request
            logging.getLogger("noah").warning("could not write trace to %s: %s", self.path, e)

    def read(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line]
