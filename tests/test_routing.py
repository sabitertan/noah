import asyncio

import pytest

from noah.engines import make_engine
from noah.extractors import make_extractor
from noah.orchestrator import Orchestrator
from noah.registry import load_experts

# (message, expected expert or None for "no confident match")
LABELLED = [
    ("what's the weather in Madrid", "weather"),
    ("is it going to rain in Oslo", "weather"),
    ("how cold is it in Moscow right now", "weather"),
    ("temperature in Cairo", "weather"),
    ("what is 17 * 23", "calc"),
    ("calculate 20% of 350", "calc"),
    ("how much is 144 divided by 12", "calc"),
    ("compute 3 plus 4 times 2", "calc"),
    ("find my invoice", "files"),
    ("search my drive for taxes", "files"),
    ("do I have a budget spreadsheet", "files"),
    ("list my documents", "files"),
    ("open my notes", "files"),
    ("show me the expenses csv", "files"),
    ("read the quarterly report pdf", "files"),
    ("write a C# class for a bank account", "csharp"),
    ("how do I use Dictionary in C#", "csharp"),
    ("my .NET app throws a NullReferenceException", "csharp"),
    ("create an ASP.NET controller with LINQ", "csharp"),
    ("write a Python function to sort a dict by value", "python"),
    ("how do I install a package with pip", "python"),
    ("explain list comprehensions in python", "python"),
    ("my pandas dataframe raises a KeyError", "python"),
    ("write an HTML page with a navigation bar and footer", "html"),
    ("how do I build an HTML form with a dropdown", "html"),
    ("what is the difference between section and article tags", "html"),
    ("add aria labels to my markup for accessibility", "html"),
    ("center a div with flexbox", "css"),
    ("write CSS for a responsive grid", "css"),
    ("how do I animate a button on hover with css", "css"),
    ("explain css specificity and the cascade", "css"),
]


@pytest.fixture(scope="module")
def experts():
    return load_experts("experts")


@pytest.mark.parametrize("engine_name", ["keyword", "embedding"])
def test_routing_accuracy(experts, engine_name):
    engine = make_engine(engine_name)
    hits = [engine.decide(m, experts)[0].expert == want for m, want in LABELLED]
    acc = sum(hits) / len(hits)
    print(f"\n{engine_name} routing accuracy: {acc:.0%} ({sum(hits)}/{len(hits)})")
    assert acc >= 0.75
    # the language experts must never be confused with each other
    for m, want in LABELLED:
        if want in ("csharp", "python", "html", "css"):
            assert engine.decide(m, experts)[0].expert == want, m


def test_engine_swap_changes_nothing_else(experts):
    """Same orchestrator + extractor code runs with either engine."""
    for name in ("keyword", "embedding"):
        orch = Orchestrator(experts, make_engine(name), make_extractor("rule"), threshold=0.0)
        out = asyncio.run(orch.ask("what is 6 * 7"))
        assert out[0].expert == "calc" and "42" in out[0].output


def test_no_match_below_threshold(experts):
    orch = Orchestrator(experts, make_engine("keyword"), make_extractor("rule"), threshold=0.25)
    assert asyncio.run(orch.ask("xyzzy plugh"))[0].expert == "no_match"


def test_fanout_runs_multiple_experts(experts):
    orch = Orchestrator(experts, make_engine("keyword"), make_extractor("rule"), mode="fanout",
                        threshold=0.1, fanout_margin=0.2)
    names = {r.expert for r in asyncio.run(orch.ask("calculate 2 + 2 and find my budget file"))}
    assert {"calc", "files"} <= names


def test_bad_args_reported_not_raised(experts):
    orch = Orchestrator(experts, make_engine("keyword"), make_extractor("rule"), threshold=0.0)
    out = asyncio.run(orch.ask("weather"))  # no city -> rule extractor returns {} -> validation error
    assert out[0].error and "city" in out[0].error


OFF_TOPIC = ["tell me a joke", "who won the world cup in 2010", "what's the capital of Australia", "hello there"]


def test_embedding_declines_off_topic_at_default_threshold(experts):
    """The default profile's threshold (0.63) must separate chit-chat from every labelled in-domain message."""
    engine = make_engine("embedding")
    assert all(engine.decide(m, experts)[0].score >= 0.63 for m, _ in LABELLED)
    assert all(engine.decide(m, experts)[0].score < 0.63 for m in OFF_TOPIC)
