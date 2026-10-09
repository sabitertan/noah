"""Score LLM tool-calling orchestrators on routing: python scripts/bench_orchestrators.py <ollama-model> [...]"""
import sys
import time

from noah.engines.llm_tools import LLMToolsEngine
from noah.registry import load_experts
from tests.test_routing import LABELLED

NEGATIVES = ["tell me a joke", "who won the world cup in 2010", "what's the capital of Australia", "hello there"]
experts = load_experts("experts")
cases = [(m, w) for m, w in LABELLED] + [(m, None) for m in NEGATIVES]

for model in sys.argv[1:]:
    eng = LLMToolsEngine(model=model)
    ok, wrong, times = 0, [], []
    for msg, want in cases:
        t = time.time()
        try:
            d = eng.decide(msg, experts)
            got = d[0].expert if d[0].score > 0 else None
            args = d[0].args if got else None
        except Exception as e:
            got, args = f"ERR {type(e).__name__}", None
        times.append(time.time() - t)
        if got == want:
            ok += 1
        else:
            wrong.append((msg, want, got, args))
    print(f"\n{model}: {ok}/{len(cases)} correct, median {sorted(times)[len(times)//2]:.1f}s, max {max(times):.1f}s", flush=True)
    for msg, want, got, args in wrong:
        print(f"   miss: {msg!r}: wanted {want}, got {got} {args or ''}", flush=True)
