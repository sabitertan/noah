"""File access for the files expert and the /api/files endpoints: confinement, search, previews.

Everything is confined to one root folder (NOAH_FILES_DIR, default data/files). Paths are passed around as
root-relative POSIX strings so the browser never sees or sends absolute paths.
"""
from __future__ import annotations

from typing import Any
import csv
import io
import os
import re
import subprocess
import sys
from pathlib import Path

TEXT_EXT = {".txt", ".log", ".json", ".yaml", ".yml"}
MARKDOWN_EXT = {".md", ".markdown"}
CSV_EXT = {".csv", ".tsv"}
PDF_EXT = {".pdf"}
# Safe to hand to the OS "open with default app": documents, images, media. Never executables or scripts.
OPENABLE_EXT = TEXT_EXT | MARKDOWN_EXT | CSV_EXT | PDF_EXT | {
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods", ".odp", ".rtf",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".mp3", ".wav", ".mp4", ".mkv", ".mov"}

TEXT_BYTES = 100_000       # read at most this much of a text file for a preview
CSV_BYTES = 200_000
CSV_ROWS, CSV_COLS = 50, 20
PDF_PAGES, PDF_CHARS, PDF_MAX_BYTES = 5, 20_000, 25_000_000
LIST_LIMIT = 8


class FileAccessError(Exception):
    """Path is outside the files folder, missing, or not a regular file."""


def files_root() -> Path:
    return Path(os.environ.get("NOAH_FILES_DIR", "data/files")).resolve()


def safe_path(rel: str) -> Path:
    """Resolve a root-relative path; reject absolute paths, `..` and symlinks that leave the root."""
    root = files_root()
    if not rel or rel.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", rel):
        raise FileAccessError("path must be relative to the files folder")
    p = (root / rel).resolve()
    if p != root and root not in p.parents:
        raise FileAccessError("path is outside the files folder")
    if not p.is_file():
        raise FileAccessError("no such file")
    return p


def kind_of(p: Path) -> str:
    ext = p.suffix.lower()
    if ext in MARKDOWN_EXT:
        return "markdown"
    if ext in TEXT_EXT:
        return "text"
    if ext in CSV_EXT:
        return "csv"
    if ext in PDF_EXT:
        return "pdf"
    return "binary"


def describe(p: Path) -> dict[str, Any]:
    kind = kind_of(p)
    return {"path": p.relative_to(files_root()).as_posix(), "name": p.name, "ext": p.suffix.lower(), "kind": kind,
            "size": p.stat().st_size, "previewable": kind != "binary", "openable": p.suffix.lower() in OPENABLE_EXT}


def _read_text(p: Path, limit: int) -> tuple[str | None, bool]:
    with p.open("rb") as f:
        raw = f.read(limit + 1)
    if b"\x00" in raw[:4096]:
        return None, False  # looks binary
    return raw[:limit].decode("utf-8", errors="replace"), len(raw) > limit


def _preview_csv(p: Path) -> dict[str, Any]:
    text, truncated = _read_text(p, CSV_BYTES)
    if text is None:
        return {"kind": "csv", "error": "This file does not look like text."}
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel_tab if p.suffix.lower() == ".tsv" else csv.excel
    rows: list[list[str]] = []
    for i, row in enumerate(csv.reader(io.StringIO(text), dialect)):
        if i >= CSV_ROWS + 1:  # header + CSV_ROWS
            truncated = True
            break
        rows.append([c[:200] for c in row[:CSV_COLS]])
    return {"kind": "csv", "rows": rows, "truncated": truncated,
            "note": f"Showing the first {CSV_ROWS} rows." if truncated else ""}


def _preview_pdf(p: Path) -> dict[str, Any]:
    if p.stat().st_size > PDF_MAX_BYTES:
        return {"kind": "pdf", "error": "This PDF is too large to preview here."}
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(p))
        if reader.is_encrypted and not reader.decrypt(""):
            return {"kind": "pdf", "error": "This PDF is password-protected."}
        pages = len(reader.pages)
        parts: list[str] = []
        for i in range(min(pages, PDF_PAGES)):
            parts.append(f"--- page {i + 1} of {pages} ---\n{(reader.pages[i].extract_text() or '').strip()}")
        text = "\n\n".join(parts)
    except Exception as e:  # corrupt, empty or unsupported file
        return {"kind": "pdf", "error": f"Could not read this PDF ({type(e).__name__})."}
    if not re.sub(r"--- page \d+ of \d+ ---|\s", "", text):
        return {"kind": "pdf", "error": "No selectable text found (it may be a scanned document)."}
    return {"kind": "pdf", "text": text[:PDF_CHARS], "truncated": len(text) > PDF_CHARS or pages > PDF_PAGES,
            "note": f"Showing the first {min(pages, PDF_PAGES)} of {pages} pages." if pages > PDF_PAGES else ""}


def preview(p: Path) -> dict[str, Any]:
    kind = kind_of(p)
    if kind in ("text", "markdown"):
        text, truncated = _read_text(p, TEXT_BYTES)
        if text is None:
            return {"kind": "binary", "error": "This file does not look like text."}
        return {"kind": kind, "text": text, "truncated": truncated, "note": "Showing the beginning of the file." if truncated else ""}
    if kind == "csv":
        return _preview_csv(p)
    if kind == "pdf":
        return _preview_pdf(p)
    return {"kind": "binary", "error": f"I can't preview {p.suffix.lower() or 'this'} files here."}


def _tokens(s: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", s.lower()) if len(t) > 1]


def search(query: str, limit: int = LIST_LIMIT) -> list[tuple[float, Path, bool]]:
    """Rank files by name (strong) and, for small text files, by content (weak). Empty query lists everything."""
    root = files_root()
    if not root.exists():
        return []
    q = _tokens(query)
    out: list[tuple[float, Path, bool]] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or any(part.startswith(".") for part in p.relative_to(root).parts):
            continue
        if not q:
            out.append((0.0, p, False))
            continue
        stem_tokens = _tokens(p.stem)
        name_hits = sum(1 for t in q if any(t == n or (len(t) > 3 and (t in n or n in t)) for n in stem_tokens))
        ext_hit = any(t == p.suffix.lower().lstrip(".") for t in q)  # "pdf" in the query: weak signal only
        content_hits = 0
        if kind_of(p) in ("text", "markdown", "csv") and p.stat().st_size <= TEXT_BYTES * 5:
            text, _ = _read_text(p, TEXT_BYTES)
            if text:
                low = text.lower()
                content_hits = sum(1 for t in q if t in low)
        score = name_hits * 2.0 + (0.5 if ext_hit else 0.0) + content_hits * 0.5
        if score > 0:
            out.append((score, p, name_hits > 0))
    out.sort(key=lambda r: (-r[0], r[1].name))
    return out[:limit]


def open_with_default_app(p: Path) -> None:
    """Hand the file to the OS default application. Only for allow-listed, non-executable types."""
    if p.suffix.lower() not in OPENABLE_EXT:
        raise FileAccessError("this file type can't be opened from Noah; download it instead")
    if sys.platform == "win32":
        os.startfile(str(p))  # noqa: S606 - allow-listed extension, path confined to the files folder
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(p)])
    else:
        subprocess.Popen(["xdg-open", str(p)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
