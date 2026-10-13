import re

from noah import filesvc

_STOP = {"find", "my", "the", "file", "files", "document", "documents", "search", "for", "do", "i", "have", "a",
         "about", "list", "in", "drive", "folder", "of", "any", "open", "show", "me", "read", "view", "display",
         "please", "can", "you", "where", "did", "put", "that", "what", "is", "inside", "contents", "content"}


def extract_rule(message: str) -> dict:
    words = [w for w in re.findall(r"[A-Za-z0-9_.-]+", message) if w.lower().strip(".") not in _STOP]
    return {"query": " ".join(words)}


def run(args: dict) -> dict:
    """Find files, and open the best match right away when it can be previewed."""
    hits = filesvc.search(args.get("query", ""))
    if not hits:
        return {"text": "I couldn't find a matching file in your files folder.", "files": []}
    top_score, top_path, top_by_name = hits[0]
    clear_winner = len(hits) == 1 or (top_by_name and top_score > hits[1][0])
    if clear_winner:  # keep only other files whose *name* matches; content-only or extension-only hits are noise
        hits = [hits[0]] + [h for h in hits[1:] if h[2]]
    files = [filesvc.describe(p) for _, p, _ in hits]
    if clear_winner and files[0]["previewable"]:
        pv = filesvc.preview(top_path)
        files[0]["preview"] = pv
        if "error" in pv:
            text = f"I found {files[0]['name']}, but I couldn't read it. You can download it" + (
                " or open it with another app." if files[0]["openable"] else ".")
        else:
            text = f"Opened {files[0]['name']}."
        if len(files) > 1:
            text += f" {len(files) - 1} other file(s) also matched."
    elif clear_winner:
        f = files[0]
        what = f["ext"] or "this kind of"
        text = (f"I found {f['name']}, but I can't preview {what} files here. "
                "You can download it" + (" or open it with another app." if f["openable"] else "."))
    else:
        text = f"I found {len(files)} files. Pick one to open:"
    return {"text": text, "files": files}
