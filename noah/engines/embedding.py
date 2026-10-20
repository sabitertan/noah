from __future__ import annotations

from .. import embedder
from ..types import Decision, Expert


class EmbeddingEngine:
    """Cosine similarity between the message and each expert's routing texts (description, examples and, for an MCP
    expert, every tool's description and examples): an expert scores as its closest text. Uses fastembed."""

    name = "embedding"

    def __init__(self, model: str = embedder.MODEL):
        self.model = model
        embedder.embed(["warm up"], model)   # load the model now, not on the first message

    def decide(self, message: str, experts: dict[str, Expert]) -> list[Decision]:
        q = embedder.embed([message], self.model)[0]
        out = [Decision(n, float((embedder.embed(e.routing_texts, self.model) @ q).max())) for n, e in experts.items()]
        return sorted(out, key=lambda d: d.score, reverse=True)
