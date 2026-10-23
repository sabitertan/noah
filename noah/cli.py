from __future__ import annotations

import argparse
import asyncio
import json

from . import builder
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
    ask.add_argument("--engine-model", default="", help="llm_tools/llm_choice: Ollama model; laya: checkpoint")
    ask.add_argument("--extractor", default="rule")
    ask.add_argument("--mode", default="route", choices=["route", "fanout"])
    ask.add_argument("--threshold", type=float, default=0.25)
    ask.add_argument("--base-url", default="http://localhost:11434/v1")
    ask.add_argument("--model", default="functiongemma")
    bld = sub.add_parser("build", help="build a single-page prototype and write it to an HTML file")
    bld.add_argument("message")
    bld.add_argument("--planner", default="", help="planner model (default: the html expert's model)")
    bld.add_argument("--model", default="", help="model for every builder expert (default: each expert's own)")
    bld.add_argument("--base-url", default="http://localhost:11434/v1")
    bld.add_argument("-o", "--out", default="prototype.html")
    sub.add_parser("experts")
    sub.add_parser("trace")
    a = p.parse_args()

    experts = load_experts(a.experts)
    if a.cmd == "experts":
        for e in experts.values():
            print(f"{e.name:10} {e.license:8} {e.description}")
    elif a.cmd == "build":
        html, plan, steps = builder.build(a.message, experts, a.base_url, a.planner, a.model)
        print(f"{plan['title']}: {plan['summary']}")
        for s in steps:
            print(f"  [{s['expert']} -> {s['part']}, {s['latency_ms']:.0f} ms] {s['error'] or str(len(s['code'])) + ' chars'}")
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"wrote {a.out}")
        if plan["small"]:
            print(f"Built with small models ({', '.join(plan['small'])}). For bigger apps try --planner/--model qwen2.5-coder:14b or larger,
"
                  "a hosted open model (hf:..., or:...), or your own Claude (an:claude-opus-5-5) or OpenAI (oa:...) API key.")
    elif a.cmd == "trace":
        for r in TraceStore().read():
            print(json.dumps(r))
    else:
        if a.engine in ("llm_tools", "llm_choice"):
            ekw = {"base_url": a.base_url, "model": a.engine_model}
        else:
            ekw = {"model": a.engine_model} if a.engine == "laya" else {}
        kw = {"base_url": a.base_url, "model": a.model} if a.extractor == "openai_compat" else {}
        orch = Orchestrator(experts, make_engine(a.engine, **ekw), make_extractor(a.extractor, **kw), TraceStore(),
                            mode=a.mode, threshold=a.threshold)
        for r in asyncio.run(orch.ask(a.message)):
            print(f"[{r.expert} via {r.extractor}, {r.latency_ms:.0f} ms] args={r.args}\n{r.output}")


if __name__ == "__main__":
    main()
