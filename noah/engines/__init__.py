from __future__ import annotations

from .base import DecisionEngine
from .keyword import KeywordEngine


def make_engine(name: str, **kwargs) -> DecisionEngine:
    if name == "keyword":
        return KeywordEngine()
    if name == "embedding":
        from .embedding import EmbeddingEngine

        return EmbeddingEngine(**kwargs)
    if name == "llm_tools":
        from .llm_tools import LLMToolsEngine

        return LLMToolsEngine(**kwargs)
    raise ValueError(f"unknown decision engine: {name}")
