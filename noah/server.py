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
DEFAULT_PROFILES = [{"name": "Default", "engine": "embedding", "mode": "route", "extractor": "openai_compat",
                     "model": "qwen2.5:0.5b", "threshold": 0.25}]
experts = load_experts(os.environ.get("NOAH_EXPERTS", "experts"))
trace = TraceStore()
_engines: dict[str, object] = {}  # engines can be expensive to load (embedding), so cache them


class AskRequest(BaseModel):
    message: str
    engine: str = "keyword"
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
        "engines": ["keyword", "embedding"],
        "modes": ["route", "fanout"],
        "extractors": ["rule", "openai_compat"],
        "models": models,
        "experts": [{"name": e.name, "description": e.description, "license": e.license,
                     "parameters": e.parameters, "examples": e.examples,
                     "kind": e.kind, "model": e.model.get("id", "")} for e in experts.values()],
    }


@app.post("/api/ask")
async def ask(req: AskRequest):
    if req.engine not in _engines:
        _engines[req.engine] = make_engine(req.engine)
    kw = {"base_url": f"{OLLAMA}/v1", "model": req.model} if req.extractor == "openai_compat" else {}
    orch = Orchestrator(experts, _engines[req.engine], make_extractor(req.extractor, **kw), trace,
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
