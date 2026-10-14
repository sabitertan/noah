from __future__ import annotations

from typing import Any, cast
import asyncio
import time
from dataclasses import asdict

from agent_framework import Executor, WorkflowBuilder, WorkflowContext, handler
from typing_extensions import Never

from .engines.base import DecisionEngine
from .generate import answer
from .extractors import ArgExtractor, validate_args
from .trace import TraceStore
from .types import Decision, Expert, ExpertResult, Routed

NO_MATCH = "no_match"


class Router(Executor):
    """Runs the decision engine and picks which experts get the message."""

    def __init__(self, engine: DecisionEngine, experts: dict[str, Expert], mode: str, threshold: float,
                 fanout_margin: float, seen: dict[str, Any]):
        super().__init__(id="router")
        self.seen = seen
        self.engine, self.experts = engine, experts
        self.mode, self.threshold, self.fanout_margin = mode, threshold, fanout_margin

    @handler
    async def route(self, message: str, ctx: WorkflowContext[Routed]) -> None:
        decisions = await asyncio.to_thread(self.engine.decide, message, self.experts)  # LLM engines block for seconds
        self.seen["decisions"] = decisions
        ranked = [d for d in decisions if d.score >= self.threshold]
        if self.mode == "fanout":
            selected = [d.expert for d in ranked if d.score >= ranked[0].score * self.fanout_margin] if ranked else []
        else:
            selected = [ranked[0].expert] if ranked else []
        await ctx.send_message(Routed(message=message, decisions=decisions, selected=selected))


class ExpertNode(Executor):
    """Extracts arguments with the configured extractor, then runs the expert's tool."""

    def __init__(self, expert: Expert, extractor: ArgExtractor, llm_url: str):
        super().__init__(id=expert.name)
        self.expert, self.extractor, self.llm_url = expert, extractor, llm_url

    @handler
    async def run_expert(self, routed: Routed, ctx: WorkflowContext[Never, ExpertResult]) -> None:
        start, error, out = time.perf_counter(), None, ""
        args: dict[str, Any] = {}
        attachments: list[dict[str, Any]] = []
        extractor = self.extractor.name
        try:
            if self.expert.kind == "generate":
                # the expert's own small model answers directly; no argument extraction needed
                model = self.expert.model["id"]
                extractor = f"generate:{model}"
                out = await asyncio.to_thread(answer, self.expert, routed.message, self.llm_url)
            else:
                supplied = next((d.args for d in routed.decisions if d.expert == self.expert.name and d.args), None)
                if supplied:  # an LLM orchestrator already produced the arguments: skip the extractor model call
                    args, extractor = supplied, "orchestrator"
                else:
                    args = await asyncio.to_thread(self.extractor.extract, routed.message, self.expert)
                validate_args(args, self.expert.parameters)
                if self.expert.run is None:
                    raise RuntimeError(f"expert {self.expert.name} has no run()")
                result: Any = await asyncio.to_thread(self.expert.run, args)
                if isinstance(result, dict):  # rich result: {"text": ..., "files": [...]}
                    rich = cast(dict[str, Any], result)
                    out, attachments = rich.get("text", ""), rich.get("files", [])
                else:
                    out = result
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            out = f"Expert '{self.expert.name}' failed: {error}"
        await ctx.yield_output(ExpertResult(self.expert.name, args, out, extractor,
                                            (time.perf_counter() - start) * 1000, error, attachments))


class NoMatch(Executor):
    def __init__(self):
        super().__init__(id=NO_MATCH)

    @handler
    async def nothing(self, routed: Routed, ctx: WorkflowContext[Never, ExpertResult]) -> None:
        await ctx.yield_output(ExpertResult(NO_MATCH, {}, "No expert is confident enough to handle that.", "-", 0.0))


class Orchestrator:
    """Slot 2: builds a MAF workflow (router -> selected experts) around the chosen slots."""

    def __init__(self, experts: dict[str, Expert], engine: DecisionEngine, extractor: ArgExtractor,
                 trace: TraceStore | None = None, mode: str = "route", threshold: float = 0.25,
                 fanout_margin: float = 0.8, llm_url: str = "http://localhost:11434/v1"):
        assert mode in ("route", "fanout")
        self.experts, self.engine, self.extractor = experts, engine, extractor
        self.trace, self.mode, self.threshold, self.fanout_margin = trace, mode, threshold, fanout_margin
        self.llm_url = llm_url

    def _build(self, seen: dict[str, Any]):
        router = Router(self.engine, self.experts, self.mode, self.threshold, self.fanout_margin, seen)
        nodes = [ExpertNode(e, self.extractor, self.llm_url) for e in self.experts.values()]
        no_match = NoMatch()

        def select(routed: Routed, target_ids: list[str]) -> list[str]:
            return routed.selected or [NO_MATCH]

        return (WorkflowBuilder(start_executor=router)
                .add_multi_selection_edge_group(router, [*nodes, no_match], selection_func=select)
                .build())

    @staticmethod
    def _trace_results(outputs: list[ExpertResult]) -> list[dict[str, Any]]:
        """Traces keep routing facts, not file contents: drop previews from attachments."""
        rows = [asdict(o) for o in outputs]
        for r in rows:
            for a in r.get("attachments", []):
                a.pop("preview", None)
        return rows

    async def ask(self, message: str) -> list[ExpertResult]:
        return (await self.ask_traced(message))[0]

    async def ask_traced(self, message: str) -> tuple[list[ExpertResult], list[Decision]]:
        start = time.perf_counter()
        seen: dict[str, Any] = {}
        result = await self._build(seen).run(message)
        outputs: list[ExpertResult] = list(result.get_outputs())
        decisions: list[Decision] = seen.get("decisions", [])
        if self.trace:
            self.trace.write({
                "message": message, "engine": self.engine.name, "mode": self.mode,
                "decisions": [asdict(d) for d in decisions], "results": self._trace_results(outputs),
                "total_ms": (time.perf_counter() - start) * 1000,
            })
        return outputs, decisions
