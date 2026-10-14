from __future__ import annotations

from typing import Any

from .base import DecisionEngine
from .keyword import KeywordEngine


def make_engine(name: str, **kwargs: Any) -> DecisionEngine:
    if name == "keyword":
        return KeywordEngine()
    if name == "embedding":
        from .embedding import EmbeddingEngine

        return EmbeddingEngine(**kwargs)
    if name == "llm_tools":
        from .llm_tools import LLMToolsEngine

        return LLMToolsEngine(**kwargs)
    if name == "llm_choice":
        from .llm_choice import LLMChoiceEngine

        return LLMChoiceEngine(**kwargs)
    if name == "laya":
        from .laya import LayaEngine

        return LayaEngine(**kwargs)
    raise ValueError(f"unknown decision engine: {name}")
