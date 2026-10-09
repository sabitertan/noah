from __future__ import annotations

import numpy as np

from ..types import Decision, Expert


class EmbeddingEngine:
    """Cosine similarity between the message and each expert's description/examples (fastembed)."""

    name = "embedding"

    def __init__(self, model: str = "BAAI/bge-small-en-v1.5"):
        from fastembed import TextEmbedding

        self._model = TextEmbedding(model_name=model)
        self._cache: dict[str, np.ndarray] = {}

    def _embed(self, texts: list[str]) -> np.ndarray:
        vecs = np.array(list(self._model.embed(texts)))
        return vecs / np.linalg.norm(vecs, axis=1, keepdims=True)

    def _expert_vectors(self, expert: Expert) -> np.ndarray:
        key = expert.card_text
        if key not in self._cache:
            self._cache[key] = self._embed([expert.description, *expert.examples])
        return self._cache[key]

    def decide(self, message: str, experts: dict[str, Expert]) -> list[Decision]:
        q = self._embed([message])[0]
        out = [Decision(n, float((self._expert_vectors(e) @ q).max())) for n, e in experts.items()]
        return sorted(out, key=lambda d: d.score, reverse=True)
