"""Generate frontend/src/lib/themes.json: the UI palettes, taken from real VS Code color themes.

Each theme's colors come from its own theme file: VS Code's built-in Dark/Light Modern from the vscode repo, the rest from
the extension packages on the Visual Studio Marketplace (the most installed ones, as listed on vscodethemes.com). Workbench
colors map onto the app's CSS variables (assets/app.css); editor colors and a few token colors feed the Monaco editor.

    python scripts/vscode_themes.py [cache_dir]
"""
import colorsys
import json
import pathlib
import re
import sys
import tempfile
import urllib.request
import zipfile

OUT = pathlib.Path(__file__).resolve().parents[1] / "frontend" / "src" / "lib" / "themes.json"
VSCODE_RAW = "https://raw.githubusercontent.com/microsoft/vscode/main/extensions/theme-defaults/themes/"
GALLERY = "https://marketplace.visualstudio.com/_apis/public/gallery/publishers/{}/vsextensions/{}/latest/vspackage"

# (id, name, source, theme label in the extension). source "vscode" = a built-in theme file name.
THEMES = [
    ("dark-modern", "Dark Modern", "vscode", "dark_modern.json"),
    ("vs-dark", "Visual Studio Dark", "ms-vscode.cpptools-themes", "Dark (Visual Studio - C/C++)"),
    ("github-dark", "GitHub Dark", "GitHub.github-vscode-theme", "GitHub Dark Default"),
    ("one-dark-pro", "One Dark Pro", "zhuangtongfa.Material-theme", "One Dark Pro"),
    ("dracula", "Dracula", "dracula-theme.theme-dracula", "Dracula Theme"),
    ("atom-one-dark", "Atom One Dark", "akamud.vscode-theme-onedark", "Atom One Dark"),
    ("material", "Material Theme", "Equinusocio.vsc-material-theme", "Material Theme"),
    ("ayu-dark", "Ayu Dark", "teabyii.ayu", "Ayu Dark"),
    ("monokai-pro", "Monokai Pro", "monokai.theme-monokai-pro-vscode", "Monokai Pro"),
    ("light-modern", "Light Modern", "vscode", "light_modern.json"),
    ("vs-light", "Visual Studio Light", "ms-vscode.cpptools-themes", "Light (Visual Studio - C/C++)"),
    ("powershell-ise", "PowerShell ISE", "ms-vscode.PowerShell", "PowerShell ISE"),
    ("github-light", "GitHub Light", "GitHub.github-vscode-theme", "GitHub Light Default"),
    ("material-lighter", "Material Lighter", "Equinusocio.vsc-material-theme", "Material Theme Lighter"),
    ("ayu-light", "Ayu Light", "teabyii.ayu", "Ayu Light"),
    ("monokai-pro-light", "Monokai Pro Light", "monokai.theme-monokai-pro-vscode", "Monokai Pro Light"),
    ("winter-light", "Winter is Coming Light", "johnpapa.winteriscoming", "Winter is Coming (Light)"),
    ("night-owl-light", "Night Owl Light", "sdras.night-owl", "Night Owl Light"),
]

# app variable -> VS Code color keys, tried in order; the first one that is readable where the app uses it wins
UI = {
    "bg": ["editor.background"],
    "fg": ["editor.foreground", "foreground"],
    "panel": ["sideBar.background", "activityBar.background", "editor.background"],
    "raised": ["list.hoverBackground", "list.inactiveSelectionBackground", "editorWidget.background", "input.background"],
    "border": ["sideBar.border", "editorGroup.border", "panel.border", "input.border", "widget.border", "contrastBorder"],
    "accent": ["button.background", "activityBarBadge.background", "focusBorder", "textLink.foreground", "progressBar.background",
               "activityBar.activeBorder", "tab.activeBorderTop", "editorCursor.foreground"],
    "accent-ink": ["button.foreground", "activityBarBadge.foreground"],
    "muted": ["descriptionForeground", "editorLineNumber.foreground", "tab.inactiveForeground"],
    "ok": ["terminal.ansiGreen", "gitDecoration.addedResourceForeground", "terminal.ansiBrightGreen"],
    "warn": ["editorWarning.foreground", "list.warningForeground", "terminal.ansiYellow", "gitDecoration.modifiedResourceForeground"],
    "err": ["errorForeground", "editorError.foreground", "list.errorForeground", "terminal.ansiRed"],
    "bar": ["scrollbarSlider.background", "input.background"],
}
HUES = {"ok": (75, 175), "warn": (20, 65), "err": (330, 20)}  # status colors must still read as green / amber / red
EDITOR_KEYS = ["editor.background", "editor.foreground", "editor.lineHighlightBackground", "editor.selectionBackground",
               "editorCursor.foreground", "editorLineNumber.foreground", "editorLineNumber.activeForeground",
               "editorGutter.background", "editorWhitespace.foreground", "editorIndentGuide.background1"]
# Monaco (Monarch) token -> TextMate scopes to look for, most specific first
TOKENS = {
    "comment": ["comment"],
    "string": ["string"],
    "keyword": ["keyword", "storage.type", "keyword.control"],
    "number": ["constant.numeric", "constant"],
    "type": ["entity.name.type", "support.type", "storage.type"],
    "string.key.json": ["support.type.property-name.json", "support.type.property-name"],
    "variable": ["variable"],
    "function": ["entity.name.function", "support.function"],
}


def jsonc(text: str):
    """Theme files are JSON with comments and trailing commas."""
    text = re.sub(r'("(?:\\.|[^"\\])*")|//[^\n]*|/\*.*?\*/', lambda m: m.group(1) or "", text, flags=re.S)
    return json.loads(re.sub(r",(\s*[}\]])", r"\1", text))


def fetch(url: str, dest: pathlib.Path) -> pathlib.Path:
    if not dest.exists():
        req = urllib.request.Request(url, headers={"User-Agent": "noah-theme-gen", "Accept-Encoding": "identity"})
        dest.write_bytes(urllib.request.urlopen(req, timeout=300).read())
    return dest


def load_vscode(name: str, cache: pathlib.Path) -> dict:
    t = jsonc(fetch(VSCODE_RAW + name, cache / name).read_text(encoding="utf-8"))
    if inc := t.get("include"):
        base = load_vscode(inc.lstrip("./"), cache)
        t["colors"] = {**base.get("colors", {}), **t.get("colors", {})}
        t["tokenColors"] = base.get("tokenColors", []) + t.get("tokenColors", [])
    return t


def load_extension(ext: str, label: str, cache: pathlib.Path) -> tuple[dict, str]:
    pub, name = ext.split(".", 1)
    z = zipfile.ZipFile(fetch(GALLERY.format(pub, name), cache / f"{ext}.vsix"))
    entry = next(t for t in jsonc(z.read("extension/package.json").decode("utf-8-sig"))["contributes"]["themes"] if t["label"] == label)

    def read(path: str) -> dict:
        t = jsonc(z.read(path).decode("utf-8-sig"))
        if inc := t.get("include"):
            base = read(str(pathlib.PurePosixPath(path).parent / inc).replace("/./", "/"))
            t["colors"] = {**base.get("colors", {}), **t.get("colors", {})}
            t["tokenColors"] = base.get("tokenColors", []) + (t.get("tokenColors") or [])
        return t

    return read("extension/" + entry["path"].lstrip("./")), entry["uiTheme"]


def rgba(c):
    c = c.lstrip("#") if isinstance(c, str) else ""
    if not re.fullmatch(r"[0-9a-fA-F]{3,8}", c) or len(c) in (5, 7):
        return None
    if len(c) in (3, 4):
        c = "".join(ch * 2 for ch in c)
    a = int(c[6:8], 16) / 255 if len(c) == 8 else 1.0
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), a


def over(c, under):
    """Flatten a translucent color onto the opaque one below it."""
    r, g, b, a = c
    return tuple(round(x * a + y * (1 - a)) for x, y in zip((r, g, b), under[:3])) + (1.0,)


def mix(a, b, t):  # t of a, 1-t of b
    return tuple(round(x * t + y * (1 - t)) for x, y in zip(a[:3], b[:3])) + (1.0,)


def hexs(c) -> str:
    return "#%02x%02x%02x" % c[:3]


def lum(c) -> float:
    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(c[0]) + 0.7152 * ch(c[1]) + 0.0722 * ch(c[2])


def contrast(a, b) -> float:
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def hue_sat(c) -> tuple[float, float]:
    r, g, b = (x / 255 for x in c[:3])
    mx, mn = max(r, g, b), min(r, g, b)
    if mx == mn:
        return 0.0, 0.0
    d = mx - mn
    h = (g - b) / d % 6 if mx == r else (b - r) / d + 2 if mx == g else (r - g) / d + 4
    return h * 60, d / (1 - abs(mx + mn - 1)) if mx + mn not in (0, 2) else 0.0


def in_hue(c, rng) -> bool:
    h, s = hue_sat(c)
    lo, hi = rng
    return s > 0.3 and (lo <= h <= hi if lo < hi else h >= lo or h <= hi)


def readable(c, bg, dark: bool, target: float = 3.0):
    """Keep a theme's accent hue and saturation but move its lightness until it reads as text and icons on the background."""
    h, light, sat = colorsys.rgb_to_hls(*(x / 255 for x in c[:3]))
    for step in range(21):
        shaded = tuple(round(x * 255) for x in colorsys.hls_to_rgb(h, min(1, max(0, light + (0.03 if dark else -0.03) * step)), sat)) + (1.0,)
        if contrast(shaded, bg) >= target:
            return shaded
    return shaded


def palette(colors: dict, dark: bool) -> dict:
    def pick(var, under, ok=lambda c: True):
        for k in UI[var]:
            c = rgba(colors.get(k))
            if c and c[3] > 0.05 and ok(c := over(c, under)):
                return c
        return None

    bg = pick("bg", rgba("#000000" if dark else "#ffffff")) or rgba("#1f1f1f" if dark else "#ffffff")
    fg = pick("fg", bg, lambda c: contrast(c, bg) >= 4.5) or (rgba("#cccccc") if dark else rgba("#3b3b3b"))
    panel = pick("panel", bg)
    raised = pick("raised", panel, lambda c: 1.08 <= contrast(c, panel) <= 2) or mix(fg, panel, 0.08)
    border = pick("border", bg, lambda c: 1.15 <= contrast(c, bg) <= 3) or mix(fg, bg, 0.2)
    accent = pick("accent", bg, lambda c: hue_sat(c)[1] > 0.35)
    accent = readable(accent, bg, dark) if accent else rgba("#0078d4" if dark else "#005fb8")
    ink = pick("accent-ink", accent, lambda c: contrast(c, accent) >= 3.5)
    ink = ink or (rgba("#ffffff") if contrast(rgba("#ffffff"), accent) >= 3.5 else rgba("#000000"))
    muted = pick("muted", bg, lambda c: contrast(c, bg) >= 3 and contrast(c, fg) >= 1.4 and hue_sat(c)[1] < 0.5) or mix(fg, bg, 0.65)
    out = {"bg": bg, "fg": fg, "panel": panel, "raised": raised, "border": border, "accent": accent, "accent-ink": ink, "muted": muted}
    for k, default in (("ok", "#3fb950" if dark else "#1a7f37"), ("warn", "#cca700" if dark else "#bf8803"), ("err", "#f85149" if dark else "#e51400")):
        out[k] = pick(k, bg, lambda c: in_hue(c, HUES[k]) and contrast(c, bg) >= 2.8) or rgba(default)
    out["bar"] = pick("bar", panel, lambda c: contrast(c, panel) >= 1.12 and contrast(c, bg) >= 1.12) or mix(fg, panel, 0.15)
    return {k: hexs(v) for k, v in out.items()}


def token_rules(token_colors: list) -> list:
    best: dict[str, tuple[int, str]] = {}
    for tc in token_colors or []:
        fg = (tc.get("settings") or {}).get("foreground")
        if not rgba(fg):
            continue
        scopes = tc.get("scope") or []
        scopes = [s.strip() for s in (scopes.split(",") if isinstance(scopes, str) else scopes)]
        for token, wanted in TOKENS.items():
            for rank, w in enumerate(wanted):
                if w in scopes and rank <= best.get(token, (99, ""))[0]:
                    best[token] = (rank, fg)
    return [{"token": t, "foreground": c.lstrip("#")[:6]} for t, (_, c) in best.items()]


def main():
    cache = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(tempfile.gettempdir()) / "noah-vscode-themes"
    cache.mkdir(parents=True, exist_ok=True)
    out = []
    for tid, name, source, label in THEMES:
        if source == "vscode":
            t, ui = load_vscode(label, cache), ("vs" if "light" in label else "vs-dark")
        else:
            t, ui = load_extension(source, label, cache)
        colors = {k: v for k, v in (t.get("colors") or {}).items() if isinstance(v, str)}
        dark = ui != "vs" and ui != "hc-light"
        out.append({"id": tid, "name": name, "dark": dark, "source": source if source != "vscode" else "VS Code",
                    "vars": palette(colors, dark),
                    "editor": {k: colors[k] for k in EDITOR_KEYS if rgba(colors.get(k))},
                    "rules": token_rules(t.get("tokenColors"))})
        print(f"{name:24} {'dark ' if dark else 'light'} {out[-1]['vars']}")
    OUT.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
