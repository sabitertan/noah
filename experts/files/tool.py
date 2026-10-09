import os
import re
from pathlib import Path

_STOP = {"find", "my", "the", "file", "files", "document", "documents", "search", "for", "do", "i", "have", "a",
         "about", "list", "in", "drive", "spreadsheet", "folder", "of", "any"}


def extract_rule(message: str) -> dict:
    words = [w for w in re.findall(r"[A-Za-z0-9_.-]+", message) if w.lower() not in _STOP]
    return {"query": " ".join(words)}


def run(args: dict) -> str:
    root = Path(os.environ.get("NOAH_FILES_DIR", "data/files"))
    if not root.exists():
        return f"Files folder {root} does not exist."
    q = args["query"].lower().split()
    hits = [p.name for p in sorted(root.rglob("*")) if p.is_file() and (not q or any(w in p.name.lower() for w in q))]
    return "Matches: " + ", ".join(hits) if hits else "No matching files."
