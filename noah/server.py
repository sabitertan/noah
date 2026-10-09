from __future__ import annotations

import hmac
import importlib.util
import json
import os
from dataclasses import asdict
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .engines import make_engine
from .engines.laya import CHECKPOINTS as LAYA_CHECKPOINTS
from .extractors import make_extractor
from .orchestrator import Orchestrator
from .registry import load_experts
from .trace import TraceStore

WEB = Path(__file__).parent / "web"
OLLAMA = os.environ.get("NOAH_LLM_URL", "http://localhost:11434")

app = FastAPI(title="noah")

# Desktop mode: the Electron shell sets a per-launch secret. Without it any web page the user visits could call
# http://127.0.0.1:<port>/api (CSRF / DNS rebinding), so we also pin the Host header to loopback names.
LAUNCH_TOKEN = os.environ.get("NOAH_LAUNCH_TOKEN", "")


def _pid_alive(pid: int) -> bool:
    """Existence check without side effects. On Windows os.kill(pid, 0) would TERMINATE the process, so use the API."""
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    return True


def _exit_with_parent(pid: int) -> None:
    """Desktop mode: if the Electron shell dies (even by crash), don't leave an orphaned server behind."""
    import threading
    import time

    def watch():
        while _pid_alive(pid):
            time.sleep(2)
        os._exit(0)

    threading.Thread(target=watch, daemon=True).start()


if os.environ.get("NOAH_PARENT_PID", "").isdigit():
    _exit_with_parent(int(os.environ["NOAH_PARENT_PID"]))


@app.middleware("http")
async def launch_guard(request: Request, call_next):
    if LAUNCH_TOKEN:
        host = request.headers.get("host", "").rsplit(":", 1)[0].strip("[]").lower()
        supplied = request.cookies.get("noah_token", "") or request.headers.get("authorization", "").removeprefix("Bearer ")
        if host not in ("127.0.0.1", "localhost", "::1") or not hmac.compare_digest(supplied, LAUNCH_TOKEN):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
    return await call_next(request)

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
    # Decision models: pick one expert (or 'none') from lettered options; the extractor then fills the arguments.
    # Pull first:  ollama pull hf.co/DreamBlooms/Tev1-0.8B-experimental-GGUF:Q8_0   (800 MB)
    {"name": "Tev1 decision", "engine": "llm_choice", "mode": "route", "extractor": "openai_compat",
     "model": "qwen2.5:0.5b", "engine_model": "hf.co/DreamBlooms/Tev1-0.8B-experimental-GGUF:Q8_0", "threshold": 0.5},
]
HAS_LAYA = importlib.util.find_spec("laya") is not None  # optional extra: pip install -e ".[laya]"
if HAS_LAYA:
    # Zero-shot Laya puts most mass on "none", so a low threshold; bench: english 20/28 routed, 4/4 declined at 0.1.
    DEFAULT_PROFILES.append({"name": "Laya decision", "engine": "laya", "mode": "route", "extractor": "openai_compat",
                             "model": "qwen2.5:0.5b", "engine_model": "english", "threshold": 0.1})
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
        "engines": ["keyword", "embedding", "llm_tools", "llm_choice", *(["laya"] if HAS_LAYA else [])],
        "engine_models": {"laya": LAYA_CHECKPOINTS},  # engines whose models are not Ollama models
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
        if req.engine in ("llm_tools", "llm_choice"):
            kw = {"base_url": f"{OLLAMA}/v1", "model": req.engine_model}
        else:
            kw = {"model": req.engine_model} if req.engine == "laya" else {}
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
