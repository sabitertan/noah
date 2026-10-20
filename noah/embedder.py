"""One sentence-embedding model for everything that compares texts: the embedding decision engine and MCP experts (which pick
their tool by how close a message is to each tool's description and examples). Loaded once, on first use; texts embedded
once and kept, so hundreds of MCP tool examples cost one pass per process, not one per message.
"""
from __future__ import annotations

import importlib.util
import threading
from typing import Any

import numpy as np

MODEL = "BAAI/bge-small-en-v1.5"
AVAILABLE = importlib.util.find_spec("fastembed") is not None   # not in the browser build (native ONNX runtime)

_models: dict[str, Any] = {}
_vectors: dict[tuple[str, str], np.ndarray] = {}
_lock = threading.Lock()   # experts run in worker threads; fastembed models are not meant to load twice


def _model(name: str) -> Any:
    with _lock:
        if name not in _models:
            from fastembed import TextEmbedding

            _models[name] = TextEmbedding(model_name=name)
        return _models[name]


def embed(texts: list[str], model: str = MODEL) -> np.ndarray:
    """Unit vectors, one row per text (so a dot product is the cosine similarity)."""
    missing = list(dict.fromkeys(t for t in texts if (model, t) not in _vectors))
    if missing:
        vecs = np.array(list(_model(model).embed(missing)))
        vecs = vecs / np.linalg.norm(vecs, axis=1, keepdims=True)
        for t, v in zip(missing, vecs):
            _vectors[(model, t)] = v
    return np.array([_vectors[(model, t)] for t in texts]) if texts else np.zeros((0, 0))
