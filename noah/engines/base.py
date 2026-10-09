from __future__ import annotations

from typing import Protocol

from ..types import Decision, Expert


class DecisionEngine(Protocol):
    """Slot 1: scores every expert for a message, highest first."""

    name: str

    def decide(self, message: str, experts: dict[str, Expert]) -> list[Decision]: ...
