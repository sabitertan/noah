from __future__ import annotations

import math
import re
from collections import Counter

from ..types import Decision, Expert

_STOP = {"the", "a", "an", "of", "in", "for", "to", "is", "what", "me", "my", "and", "on", "at", "it", "i", "you"}


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _STOP]


class KeywordEngine:
    """Dependency-free baseline: TF-IDF overlap between message and expert card."""

    name = "keyword"

    def decide(self, message: str, experts: dict[str, Expert]) -> list[Decision]:
        docs = {n: Counter(_tokens(e.card_text)) for n, e in experts.items()}
        df: Counter[str] = Counter()
        for c in docs.values():
            df.update(c.keys())
        total = len(docs)
        query = set(_tokens(message))
        raw = {}
        for n, c in docs.items():
            norm = math.sqrt(sum(c.values())) or 1.0
            raw[n] = sum((1 + math.log(c[t])) * math.log(1 + total / df[t]) for t in query if t in c) / norm
        peak = max(raw.values(), default=0.0) or 1.0
        out = [Decision(n, s / peak if peak else 0.0) for n, s in raw.items()]
        return sorted(out, key=lambda d: d.score, reverse=True)
