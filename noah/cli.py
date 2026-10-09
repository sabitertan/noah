from __future__ import annotations

import argparse
import asyncio
import json

from .engines import make_engine
from .extractors import make_extractor
from .orchestrator import Orchestrator
from .registry import load_experts
from .trace import TraceStore


def main() -> None:
    p = argparse.ArgumentParser(prog="noah")
    p.add_argument("--experts", default="experts")
    sub = p.add_subparsers(dest="cmd", required=True)
    ask = sub.add_parser("ask")
    ask.add_argument("message")
    ask.add_argument("--engine", default="keyword")
    ask.add_argument("--extractor", default="rule")
    ask.add_argument("--mode", default="route", choices=["route", "fanout"])
    ask.add_argument("--threshold", type=float, default=0.25)
    ask.add_argument("--base-url", default="http://localhost:11434/v1")
    ask.add_argument("--model", default="functiongemma")
    sub.add_parser("experts")
    sub.add_parser("trace")
    a = p.parse_args()

    experts = load_experts(a.experts)
    if a.cmd == "experts":
        for e in experts.values():
            print(f"{e.name:10} {e.license:8} {e.description}")
    elif a.cmd == "trace":
        for r in TraceStore().read():
            print(json.dumps(r))
    else:
        kw = {"base_url": a.base_url, "model": a.model} if a.extractor == "openai_compat" else {}
        orch = Orchestrator(experts, make_engine(a.engine), make_extractor(a.extractor, **kw), TraceStore(),
                            mode=a.mode, threshold=a.threshold)
        for r in asyncio.run(orch.ask(a.message)):
            print(f"[{r.expert} via {r.extractor}, {r.latency_ms:.0f} ms] args={r.args}\n{r.output}")


if __name__ == "__main__":
    main()
