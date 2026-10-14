from __future__ import annotations

import os
from typing import Any, cast

from ..types import Decision, Expert
from .llm_choice import QUESTION, expert_options

CHECKPOINTS = ["auto", "english", "multilingual"]  # Laya's Router picks english/multilingual per message on "auto"


class LayaEngine:
    """Laya (convaiinnovations/laya): a non-generative decision model returning calibrated per-option probabilities.

    One `choice` question with the experts (plus 'none') as options; ~70 ms per decision once loaded.
    Optional dependency (torch + transformers): pip install -e ".[laya]".
    """

    name = "laya"

    def __init__(self, model: str = ""):
        os.environ.setdefault("USE_TF", "0")  # a TensorFlow install can deadlock transformers model construction
        try:
            from laya import Router  # pyright: ignore[reportAttributeAccessIssue, reportUnknownVariableType]
        except ImportError as e:
            raise RuntimeError('the laya engine needs the optional dependency: pip install -e ".[laya]"') from e
        router = cast(Any, Router())
        self._router: Any = router
        self.checkpoint: str | None = None if model in ("", "auto") else model
        if self.checkpoint:
            router.preload([self.checkpoint])

    def decide(self, message: str, experts: dict[str, Expert]) -> list[Decision]:
        # descriptions only: options share a fixed token budget, and adding examples dropped routing from 24/28 to 8/28
        question = {"expert": {"type": "choice", "instructions": QUESTION, "criteria": dict(expert_options(experts))}}
        kw = {"model": self.checkpoint} if self.checkpoint else {}
        probs = self._router.predict(message, question, **kw)["answers"]["expert"]["probabilities"]
        out = [Decision(n, round(float(probs.get(n, 0.0)), 4)) for n in experts]
        return sorted(out, key=lambda d: d.score, reverse=True)
