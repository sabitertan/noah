from __future__ import annotations

from typing import Any

from ..types import Expert


class RuleExtractor:
    """Baseline: use the expert's own `extract_rule(message)` function."""

    name = "rule"

    def extract(self, message: str, expert: Expert) -> dict[str, Any]:
        if expert.extract_rule is None:
            return {}
        return expert.extract_rule(message)
