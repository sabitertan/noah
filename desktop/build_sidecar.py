"""Freeze the Noah server into desktop/sidecar/dist/noah-server with PyInstaller (run inside the project venv).

Usage:  python desktop/build_sidecar.py
Output: desktop/sidecar/dist/noah-server/   (about 170 MB; electron-builder ships it as resources/noah-server)
PyInstaller does not cross-compile: build on each OS you target (see .github/workflows/desktop.yml).
"""
import os
import pathlib

import PyInstaller.__main__

root = pathlib.Path(__file__).resolve().parent.parent
out = root / "desktop" / "sidecar"
PyInstaller.__main__.run([
    str(out / "noah_server.py"), "--name", "noah-server", "--noconfirm", "--onedir",
    "--distpath", str(out / "dist"), "--workpath", str(out / "build"), "--specpath", str(out / "build"),
    "--paths", str(root),
    "--add-data", f"{root / 'noah' / 'web'}{os.pathsep}noah/web",
    "--collect-all", "agent_framework", "--collect-all", "agent_framework_orchestrations", "--collect-all", "fastembed",
    "--collect-submodules", "uvicorn", "--collect-submodules", "onnxruntime", "--hidden-import", "noah.server",
])
