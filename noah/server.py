from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .engines import make_engine
from .extractors import make_extractor
from .orchestrator import Orchestrator
from .registry import load_experts
from .trace import TraceStore

WEB = Path(__file__).parent / "web"
OLLAMA = os.environ.get("NOAH_LLM_URL", "http://localhost:11434")

app = FastAPI(title="noah")
app.mount("/static", StaticFiles(directory=WEB / "static"), name="static")
PROFILES = Path(os.environ.get("NOAH_PROFILES", "data/profiles.json"))
DEFAULT_PROFILES = [
    # bge-small cosine scores: in-domain >= 0.65, off-topic <= 0.61 on our test set, so 0.63 declines chit-chat.
    {"name": "Default", "engine": "embedding", "mode": "route", "extractor": "openai_compat",
     "model": "qwen2.5:0.5b", "threshold": 0.63},
    # Example LLM orchestrators: the model picks the expert AND its arguments by tool calling.
    # Pull first:  ollama pull hf.co/MaziyarPanahi/Nemotron-Orchestrator-8B-GGUF:Q4_K_M   (5 GB, slow on small GPUs)
    {"name": "Nemotron orchestrator", "engine": "llm_tools", "mode": "fanout", "extractor": "rule", "model": "",
     "engine_model": "hf.co/MaziyarPanahi/Nemotron-Orchestrator-8B-GGUF:Q4_K_M", "threshold": 0.25},
    # Pull first:  ollama pull hf.co/ggml-org/functiongemma-270m-it-GGUF:Q8_0   (300 MB; needs fine-tuning to be reliable)
    {"name": "FunctionGemma router", "engine": "llm_tools", "mode": "route", "extractor": "rule", "model": "",
     "engine_model": "hf.co/ggml-org/functiongemma-270m-it-GGUF:Q8_0", "threshold": 0.25},
]
experts = load_experts(os.environ.get("NOAH_EXPERTS", "experts"))
trace = TraceStore()
_engines: dict[str, object] = {}  # engines can be expensive to load (embedding), so cache them


class AskRequest(BaseModel):
    message: str
    engine: str = "keyword"
    engine_model: str = ""
    mode: str = "route"
    extractor: str = "rule"
    model: str = ""
    threshold: float = 0.25


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(WEB / "static" / "favicon.svg", media_type="image/svg+xml")


@app.get("/api/config")
def config():
    try:
        models = [m["name"] for m in httpx.get(f"{OLLAMA}/api/tags", timeout=2).json()["models"]]
    except Exception:
        models = []
    return {
        "engines": ["keyword", "embedding", "llm_tools"],
        "modes": ["route", "fanout"],
        "extractors": ["rule", "openai_compat"],
        "models": models,
        "experts": [{"name": e.name, "description": e.description, "license": e.license,
                     "parameters": e.parameters, "examples": e.examples,
                     "kind": e.kind, "model": e.model.get("id", "")} for e in experts.values()],
    }


@app.post("/api/ask")
async def ask(req: AskRequest):
    key = f"{req.engine}:{req.engine_model}"
    if key not in _engines:
        kw = {"base_url": f"{OLLAMA}/v1", "model": req.engine_model} if req.engine == "llm_tools" else {}
        _engines[key] = make_engine(req.engine, **kw)
    kw = {"base_url": f"{OLLAMA}/v1", "model": req.model} if req.extractor == "openai_compat" else {}
    orch = Orchestrator(experts, _engines[key], make_extractor(req.extractor, **kw), trace,
                        mode=req.mode, threshold=req.threshold, llm_url=f"{OLLAMA}/v1")
    results, decisions = await orch.ask_traced(req.message)
    return {"results": [asdict(r) for r in results], "decisions": [asdict(d) for d in decisions]}


@app.get("/api/traces")
def traces(limit: int = 50):
    return trace.read()[-limit:][::-1]


@app.get("/api/profiles")
def get_profiles():
    if PROFILES.exists():
        return json.loads(PROFILES.read_text())
    return DEFAULT_PROFILES


@app.put("/api/profiles")
def put_profiles(profiles: list[dict]):
    PROFILES.parent.mkdir(parents=True, exist_ok=True)
    PROFILES.write_text(json.dumps(profiles, indent=2))
    return profiles
