"""Score orchestrators on routing: python scripts/bench_orchestrators.py [--threshold 0.5] <spec> [...]

A spec is an Ollama model (an llm_tools orchestrator) or <engine>:<model>, e.g.
  llm_choice:hf.co/DreamBlooms/Tev1-0.8B-experimental-GGUF:Q8_0   laya:auto   laya:english   embedding:
"""
import argparse
import time
from typing import Any

from noah.engines import make_engine
from noah.engines.base import DecisionEngine
from noah.registry import load_experts
from tests.test_routing import LABELLED

NEGATIVES = ["tell me a joke", "who won the world cup in 2010", "what's the capital of Australia", "hello there"]
OLLAMA_ENGINES = ("llm_tools", "llm_choice")
experts = load_experts("experts")
cases: list[tuple[str, str | None]] = [(m, w) for m, w in LABELLED]
cases += [(m, None) for m in NEGATIVES]


def engine_for(spec: str) -> DecisionEngine:
    name, _, model = spec.partition(":") if spec.split(":", 1)[0] in (*OLLAMA_ENGINES, "laya", "embedding", "keyword") \
        else ("llm_tools", "", spec)
    kw: dict[str, Any] = {"model": model} if name in (*OLLAMA_ENGINES, "laya") else {}
    return make_engine(name, **kw)


ap = argparse.ArgumentParser()
ap.add_argument("--threshold", type=float, default=0.5, help="top score below this counts as declining")
ap.add_argument("specs", nargs="+")
a = ap.parse_args()

for spec in a.specs:
    eng = engine_for(spec)
    eng.decide("warm up", experts)  # first call loads the model; don't count it
    hit, declined = 0, 0
    wrong: list[tuple[str, str | None, str | None, float, dict[str, Any] | None]] = []
    times: list[float] = []
    for msg, want in cases:
        t = time.time()
        try:
            d = eng.decide(msg, experts)
            got: str | None = d[0].expert if d[0].score >= a.threshold else None
            args, score = (d[0].args if got else None), d[0].score
        except Exception as e:
            got, args, score = f"ERR {type(e).__name__}", None, 0.0
        times.append(time.time() - t)
        if got == want:
            hit += want is not None
            declined += want is None
        else:
            wrong.append((msg, want, got, score, args))
    print(f"\n{spec}: routes {hit}/{len(LABELLED)}, declines {declined}/{len(NEGATIVES)}, "
          f"median {sorted(times)[len(times)//2] * 1000:.0f} ms, max {max(times) * 1000:.0f} ms", flush=True)
    for msg, want, got, score, args in wrong:
        print(f"   miss: {msg!r}: wanted {want}, got {got} (top {score:.2f}) {args or ''}", flush=True)
