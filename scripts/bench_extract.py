"""Compare argument extraction: rule baseline vs tiny models (no fallback, so failures show)."""
import sys
import time
from typing import Any

from noah.extractors import ArgExtractor
from noah.extractors.openai_compat import OpenAICompatExtractor
from noah.extractors.rule import RuleExtractor
from noah.registry import load_experts
from noah.types import Expert

# (expert, message, expected args). First half is easy phrasing, second half defeats simple rules.
CASES: list[tuple[str, str, dict[str, str]]] = [
    ("weather", "what's the weather in Madrid", {"city": "Madrid"}),
    ("weather", "temperature in Cairo", {"city": "Cairo"}),
    ("weather", "how cold is it in New York right now", {"city": "New York"}),
    ("weather", "Tokyo weather please", {"city": "Tokyo"}),
    ("weather", "will I need an umbrella in Seattle tomorrow?", {"city": "Seattle"}),
    ("weather", "I'm flying to Lisbon, how hot is it there?", {"city": "Lisbon"}),
    ("calc", "what is 17 * 23", {"expression": "17*23"}),
    ("calc", "calculate 20% of 350", {"expression": "20/100*350"}),
    ("calc", "how much is 144 divided by 12", {"expression": "144/12"}),
    ("calc", "I bought 3 books at 12 dollars each, what's the total?", {"expression": "3*12"}),
    ("calc", "square root of nothing, just add forty two and eight", {"expression": "42+8"}),
    ("files", "find my invoice", {"query": "invoice"}),
    ("files", "search my drive for taxes", {"query": "taxes"}),
    ("files", "do I have anything about the 2026 budget?", {"query": "budget"}),
    ("files", "where did I put that tax document", {"query": "tax"}),
]


def norm(v: Any) -> str:
    return str(v).lower().replace(" ", "").replace("(", "").replace(")", "")


def same(got: dict[str, Any], want: dict[str, str]) -> bool:
    if not got:
        return False
    for k, w in want.items():
        g = got.get(k)
        if g is None:
            return False
        if k == "expression":
            if norm(g) != norm(w) and norm(w) not in norm(g):
                return False
        elif norm(w) not in norm(g):
            return False
    return True


def run(extractor: ArgExtractor, experts: dict[str, Expert]) -> tuple[int, float, list[tuple[str, dict[str, Any]]]]:
    ok, ms = 0, 0.0
    fails: list[tuple[str, dict[str, Any]]] = []
    for name, msg, want in CASES:
        t = time.perf_counter()
        try:
            got: dict[str, Any] = extractor.extract(msg, experts[name])
        except Exception as e:
            got = {"_error": type(e).__name__}
        ms += (time.perf_counter() - t) * 1000
        if same(got, want):
            ok += 1
        else:
            fails.append((msg, got))
    return ok, ms / len(CASES), fails


if __name__ == "__main__":
    experts = load_experts("experts")
    contenders: list[tuple[str, ArgExtractor]] = [("rule", RuleExtractor())]
    contenders += [(m, OpenAICompatExtractor(model=m, fallback=False, timeout=120)) for m in sys.argv[1:] if not m.startswith("-")]
    for label, ex in contenders:
        ex_name = label
        ok, avg, fails = run(ex, experts)
        print(f"\n{ex_name}: {ok}/{len(CASES)} correct, {avg:.0f} ms avg")
        for msg, got in fails:
            print(f"   miss: {msg!r} -> {got}")
