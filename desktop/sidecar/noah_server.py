"""Entry point for the packaged server (PyInstaller). Usage: noah-server --port 8765 [--host 127.0.0.1]"""
import argparse
import os
import sys

import uvicorn


def main() -> None:
    if sys.argv[1:2] == ["--check-experts"]:  # the expert editor's load check: a child process, since frozen there is no `python -m`
        from noah.expert_check import main as check

        check(sys.argv[2:])
        return
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    # experts ship next to the executable unless the shell points elsewhere
    here = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
    os.environ.setdefault("NOAH_EXPERTS", os.path.join(here, "experts"))
    from noah.server import app  # imported late so env vars above are seen

    uvicorn.run(app, host=a.host, port=a.port, log_level="info")


if __name__ == "__main__":
    main()
