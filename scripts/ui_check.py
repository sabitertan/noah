"""End-to-end UI check: drives the real web UI in Chromium against a Noah server wired to a local stub that plays
Ollama, Hugging Face, OpenRouter and an Azure Foundry resource. No network, keys or models needed (the embedding
model is the one Noah already uses). Everything runs on temporary data, so your own profiles, keys and traces are untouched.

    pip install -e ".[dev]" && playwright install chromium     # or CHROMIUM=/path/to/chromium
    (cd frontend && npm run build)                              # the server serves noah/web/dist
    python scripts/ui_check.py [--shots DIR]

Exit code 0 when every check passes. Needs a POSIX shell for the fake `az` (the Azure CLI sign-in check is skipped on Windows).
"""
import argparse
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
HF_TOKEN, OR_KEY, AZ_KEY, ENTRA = "hf_good_token_123", "sk-or-v1-good_key_1234", "azure-good-key-123456", "entra-token-from-cli"


# ------------------------------------------------------------------ the stub
class Stub(BaseHTTPRequestHandler):
    log: list = []
    tags = ["qwen2.5:0.5b", "qwen2.5-coder:1.5b"]
    router = {"data": [
        {"id": "Qwen/Qwen2.5-7B-Instruct", "providers": [{"provider": "a", "status": "live", "supports_tools": True, "supports_structured_output": True,
                                                          "pricing": {"input": 0.2, "output": 0.4}, "context_length": 32768}]},
        {"id": "meta-llama/Llama-3.2-1B-Instruct", "providers": [{"provider": "a", "status": "live", "is_free": True}]}]}
    openrouter = {"data": [
        {"id": "meta-llama/llama-3.3-70b-instruct:free", "name": "Meta: Llama 3.3 70B Instruct (free)", "context_length": 65536,
         "pricing": {"prompt": "0", "completion": "0"}, "architecture": {"output_modalities": ["text"]}, "supported_parameters": ["tools", "structured_outputs"]},
        {"id": "openai/gpt-4o-mini", "name": "OpenAI: GPT-4o-mini", "context_length": 128000, "pricing": {"prompt": "0.00000015", "completion": "0.0000006"},
         "architecture": {"output_modalities": ["text"]}, "supported_parameters": ["tools", "response_format", "logprobs"]}]}
    azure_ok = (f"Bearer {AZ_KEY}", f"Bearer {ENTRA}")
    azure_deployments = ("my-gpt",)

    def log_message(self, *a):
        pass

    def reply(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(raw)))
        self.end_headers(); self.wfile.write(raw)

    def do_GET(self):
        p, auth = self.path.split("?")[0], self.headers.get("Authorization")
        routes = {
            "/__log": lambda: self.reply(200, Stub.log),
            "/ollama/api/tags": lambda: self.reply(200, {"models": [{"name": n} for n in Stub.tags]}),
            "/router/v1/models": lambda: self.reply(200, Stub.router),
            "/or/api/v1/models": lambda: self.reply(200, Stub.openrouter),
            "/or/api/v1/key": lambda: self.reply(200, {"data": {"label": "my-laptop-key"}}) if auth == f"Bearer {OR_KEY}" else self.reply(401, {}),
            "/hub/api/whoami-v2": lambda: self.reply(200, {"name": "ada"}) if auth == f"Bearer {HF_TOKEN}" else self.reply(401, {}),
            "/hub/api/models": lambda: self.reply(200, [{"id": "unsloth/Qwen3-4B-GGUF", "downloads": 1234567, "likes": 321}]),
            "/openai/v1/models": lambda: self.reply(200, {"data": []}) if auth in Stub.azure_ok else self.reply(401, {}),
        }
        if p.startswith("/hub/api/models/"):
            return self.reply(200, {"siblings": [{"rfilename": "Qwen3-4B-Q4_K_M.gguf", "size": 2500000000}, {"rfilename": "Qwen3-4B-Q8_0.gguf", "size": 4300000000}]})
        routes.get(p, lambda: self.reply(404, {}))()

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        p, auth = self.path, self.headers.get("Authorization")
        if p == "/ollama/api/pull":
            Stub.log.append({"pull": body.get("model")})
            self.send_response(200); self.send_header("Content-Type", "application/x-ndjson"); self.end_headers()
            for i in range(1, 6):
                self.wfile.write((json.dumps({"status": "pulling abc", "total": 100, "completed": i * 20}) + "\n").encode()); self.wfile.flush(); time.sleep(0.25)
            Stub.tags.append(body["model"]); self.wfile.write(b'{"status":"success"}\n'); return
        if p.endswith("/chat/completions"):
            Stub.log.append({"path": p, "auth": auth, "model": body.get("model"), "has_schema": "response_format" in body})
            ok = {"/router": f"Bearer {HF_TOKEN}", "/or/": f"Bearer {OR_KEY}"}
            for prefix, want in ok.items():
                if p.startswith(prefix) and auth != want:
                    return self.reply(401, {})
            if p == "/openai/v1/chat/completions":
                if auth not in Stub.azure_ok:
                    return self.reply(401, {})
                if body.get("model") not in Stub.azure_deployments:
                    return self.reply(404, {"error": {"code": "DeploymentNotFound"}})
            text = json.dumps({"expression": "15% of 240"}) if "response_format" in body else "ok"
            return self.reply(200, {"choices": [{"message": {"content": text}}]})
        self.reply(404, {})


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ------------------------------------------------------------------ the checks
failures = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


def run_checks(pg, base, stub_url, data: Path, has_az_cli: bool, shots: Path | None):
    log = lambda: json.load(urllib.request.urlopen(f"{stub_url}/__log"))
    calls = lambda path, **match: [e for e in log() if e.get("path") == path and all(e.get(k) == v for k, v in match.items())]
    tab = lambda name: (pg.click(f"[data-tab={name}]"), pg.wait_for_selector(f"[data-tab={name}].active"))
    nav = lambda v: pg.click(f"[data-view={v}]")
    shot = lambda n: pg.screenshot(path=str(shots / n)) if shots else None
    answers = lambda: pg.locator(".a .body").count()

    def ask(text):
        n = answers()
        pg.fill("#msg", text); pg.press("#msg", "Enter")
        pg.wait_for_function(f"document.querySelectorAll('.a .body').length > {n}", timeout=60000)
        return pg.locator(".a .body").last.inner_text()

    # ---- chat and routing ----
    pg.goto(base); pg.wait_for_selector(".chip")
    check("starts on #/chat", pg.evaluate("location.hash") == "#/chat" and pg.inner_text("#title") == "New Chat")
    check("example chips from enabled experts", pg.locator(".chip").count() >= 5)
    check("chat answers through the routed expert", "36" in ask("calculate 15% of 240"))
    check("chat gets its own URL", pg.evaluate("location.hash").startswith("#/chat/"))
    chat_hash = pg.evaluate("location.hash")
    pg.reload(); pg.wait_for_selector(".msg")
    check("refresh keeps the chat", pg.evaluate("location.hash") == chat_hash and pg.locator(".msg").count() == 1)
    for view, title in [("orch", "Orchestrators"), ("experts", "Experts"), ("models", "Models"), ("traces", "Traces")]:
        nav(view); pg.wait_for_function(f"document.getElementById('title').innerText === '{title}'")
        pg.reload(); pg.wait_for_selector(".sidebar"); pg.wait_for_function(f"document.getElementById('title').innerText === '{title}'")
        check(f"refresh stays on {view}", pg.evaluate("location.hash") == f"#/{view}")
    pg.goto(base + "#/nonsense"); pg.reload(); pg.wait_for_selector("#v-chat.active")
    check("unknown route falls back to the chat page", pg.evaluate("location.hash").startswith("#/chat"))
    pg.goto(base + "#/chat/does-not-exist"); pg.reload(); pg.wait_for_selector(".chip")
    check("unknown chat id falls back to a new chat", pg.evaluate("location.hash") == "#/chat")
    pg.goto(base + chat_hash); pg.reload(); pg.wait_for_selector(".msg")
    check("deep link to a chat", pg.locator(".msg").count() == 1)

    # ---- orchestrators: cards and editor cannot disagree ----
    nav("orch"); pg.wait_for_selector("#f-name")
    cards = pg.locator("#orchPanel .cards .card[data-edit]")
    n = cards.count()
    for i in range(n):
        cards.nth(i).click()
        check(f"card {i} matches the editor", pg.input_value("#f-name") in cards.nth(i).inner_text() and pg.input_value("#f-engine") in cards.nth(i).inner_text())
    cards.nth(0).click(); pg.fill("#f-name", "Renamed live"); pg.select_option("#f-engine", "keyword")
    check("card shows the draft live, tagged unsaved", "Renamed live" in cards.nth(0).inner_text() and "unsaved" in cards.nth(0).inner_text() and "keyword" in cards.nth(0).inner_text())
    pg.click("#f-save"); pg.wait_for_timeout(400)
    check("save clears the unsaved tag", "unsaved" not in cards.nth(0).inner_text() and "Renamed live" in cards.nth(0).inner_text())
    cards.nth(0).click(); pg.fill("#f-name", "discard me"); cards.nth(1).click()
    check("switching cards drops the draft", "discard me" not in pg.inner_text("#orchPanel .cards"))
    pg.click("[data-new]"); pg.wait_for_timeout(400)
    check("new orchestrator is added and selected", cards.count() == n + 1 and "sel" in cards.last.get_attribute("class"))
    pg.fill("#f-name", "Renamed live"); pg.click("#f-save"); pg.wait_for_timeout(300)
    check("duplicate name refused", "already an orchestrator" in pg.inner_text("#orchMsg"))
    before = pg.locator(".item[data-view=orch] .meta").inner_text()
    pg.locator("#orchPanel .card[data-edit] .sw").nth(1).click(); pg.wait_for_timeout(400)
    check("sidebar counts enabled orchestrators", pg.locator(".item[data-view=orch] .meta").inner_text() != before)
    pg.locator("#orchPanel .card[data-edit] .sw").nth(1).click(); pg.wait_for_timeout(400)
    cards.last.click(); pg.click("#f-del"); pg.wait_for_timeout(400)
    check("delete works", cards.count() == n)

    # ---- experts ----
    nav("experts"); pg.wait_for_timeout(300)
    total = pg.locator(".item[data-view=experts] .meta").inner_text()
    pg.locator("#expertsPanel .sw").nth(0).click(); pg.locator("#expertsPanel .sw").nth(1).click(); pg.wait_for_timeout(500)
    check("sidebar shows enabled/total experts", pg.locator(".item[data-view=experts] .meta").inner_text() == f"{int(total) - 2}/{total}")
    pg.locator("#expertsPanel .sw").nth(0).click(); pg.locator("#expertsPanel .sw").nth(1).click(); pg.wait_for_timeout(500)

    # New Chat must work from any page, even while a chat is open (a watcher once wrote the old route back over the new one)
    nav("experts"); pg.wait_for_selector("[data-new-expert]"); pg.click("#newChat"); pg.wait_for_selector(".chip")
    check("New Chat from another page opens an empty chat", pg.evaluate("location.hash") == "#/chat" and pg.locator(".msg").count() == 0)
    pg.click(".chat-item"); pg.wait_for_selector(".msg"); nav("models"); pg.click(".chat-item"); pg.wait_for_selector(".msg")
    check("opening a chat from another page works", pg.evaluate("location.hash").startswith("#/chat/"))

    # ---- creating and editing experts ----
    nav("experts"); pg.wait_for_selector("[data-new-expert]")
    count = int(pg.locator(".item[data-view=experts] .meta").inner_text())
    pg.click("[data-new-expert]"); pg.wait_for_selector("#e-name")
    check("new expert opens an editor with its own URL", pg.evaluate("location.hash") == "#/experts/new")
    pg.click("#e-save"); pg.wait_for_selector("#e-error")
    check("saving an empty expert explains what is missing", pg.inner_text("#e-error") != "")
    pg.fill("#e-name", "Bad Name"); pg.fill("#e-description", "Answers in pirate speak."); pg.fill("#e-examples", "talk like a pirate\nsay it like a pirate would")
    pg.fill("#e-system", "You are a pirate. Answer briefly in pirate speak.")
    pg.click("#e-save"); pg.wait_for_function("(document.getElementById('e-error') || {innerText: ''}).innerText.toLowerCase().includes('name')")
    check("a bad name is refused and nothing is created", "lowercase" in pg.inner_text("#e-error").lower() and pg.evaluate("location.hash") == "#/experts/new")
    pg.fill("#e-name", "pirate")
    check("model list is grouped by where models run", "Local (Ollama)" in pg.locator("#e-model optgroup").evaluate_all("els => els.map(e => e.label)"))
    pg.fill("#e-try-message", "ahoy there"); pg.click("#e-try"); pg.wait_for_selector("#e-try-out")
    check("Try it runs the draft", pg.inner_text("#e-try-out .body") == "ok")
    check("Try it saved nothing", not (data / "experts" / "pirate").exists())
    pg.click("#e-addshot"); pg.locator(".shot textarea").nth(0).fill("hi"); pg.locator(".shot textarea").nth(1).fill("Arr!"); pg.fill("#e-strip", "```\\w*")
    pg.click("#e-save"); pg.wait_for_selector("[data-expert=pirate]")
    check("saved: back on the list with a new card", pg.evaluate("location.hash") == "#/experts" and pg.locator("[data-expert=pirate]").count() == 1)
    check("sidebar count includes the new expert", pg.locator(".item[data-view=experts] .meta").inner_text() == str(count + 1))
    saved = (data / "experts" / "pirate" / "expert.yaml").read_text()
    check("written as a generate expert, never a tool", "kind: generate" in saved and "tool" not in saved and not (data / "experts" / "pirate" / "tool.py").exists())
    check("shots and filters are saved", "Arr!" in saved and "\\w*" in saved)
    pg.click("#newChat"); pg.wait_for_selector(".chip")
    ask("talk like a pirate")
    check("a message is routed to the new expert without a restart", pg.locator(".a .badge").last.inner_text() == "pirate")
    nav("experts"); pg.click("[data-expert=pirate] h3 .grow"); pg.wait_for_selector("#e-description")
    check("selecting a card opens its editor", pg.evaluate("location.hash") == "#/experts/pirate" and pg.input_value("#e-name") == "pirate" and pg.locator("#e-name").is_disabled())
    check("editor shows the saved prompt", pg.input_value("#e-system").startswith("You are a pirate"))
    pg.fill("#e-description", "Pirate talk, edited."); pg.click("#e-save"); pg.wait_for_selector("[data-expert=pirate]")
    pg.goto(base + "#/experts/pirate"); pg.reload(); pg.wait_for_selector("#e-description")
    check("refresh stays in the editor and shows the saved edit", pg.input_value("#e-description") == "Pirate talk, edited.")
    pg.click("#e-cancel"); pg.wait_for_selector("[data-expert=pirate]"); check("cancel returns to the list", pg.evaluate("location.hash") == "#/experts")
    # a tool expert: routing text only, its code is never offered or written
    pg.click("[data-expert=calc] h3 .grow"); pg.wait_for_selector("#e-toolnote")
    check("tool expert shows no model, prompt or delete", all(pg.locator(sel).count() == 0 for sel in ("#e-model", "#e-system", "#e-strip", "#e-delete")))
    pg.fill("#e-examples", pg.input_value("#e-examples") + "\nadd 2 and 2"); pg.click("#e-save"); pg.wait_for_selector("[data-expert=calc]")
    check("tool expert routing edited", "add 2 and 2" in (data / "experts" / "calc" / "expert.yaml").read_text())
    check("tool code untouched", (data / "experts" / "calc" / "tool.py").read_bytes() == (ROOT / "experts" / "calc" / "tool.py").read_bytes())
    pg.click("[data-expert=pirate] h3 .grow"); pg.wait_for_selector("#e-delete"); pg.click("#e-delete"); pg.click("#e-delete-yes"); pg.wait_for_selector("[data-new-expert]"); pg.wait_for_function("!document.querySelector('[data-expert=pirate]')")
    check("delete removes the expert", not (data / "experts" / "pirate" / "expert.yaml").exists() and pg.locator(".item[data-view=experts] .meta").inner_text() == str(count),
          f"sidebar {pg.locator('.item[data-view=experts] .meta').inner_text()!r}, expected {count}")
    check("the experts list is back to what it was", pg.locator("#expertsPanel .sw").count() == count, f"{pg.locator('#expertsPanel .sw').count()} cards, expected {count}")

    # ---- models: tabs, hosted providers, downloads ----
    nav("models"); pg.wait_for_selector("#modelsTable tbody tr")
    rows = pg.locator("#modelsTable tbody tr")
    check("local models are labelled Local", rows.count() == 2 and all("Local · Ollama" in t for t in rows.all_inner_texts()))

    tab("hf"); pg.wait_for_selector("#hfHosted tbody tr")
    check("tab is in the URL", pg.evaluate("location.hash") == "#/models/hf")
    check("hosted + GGUF results with capabilities", pg.locator("#hfHosted tbody tr").count() == 2 and pg.locator("#hfGguf tbody tr").count() == 1 and "tool calls" in pg.inner_text("#hfHosted"))
    pg.fill("#hf-search", "llama"); pg.wait_for_timeout(900)
    check("search filters", pg.locator("#hfHosted tbody tr").count() == 1)
    pg.fill("#hf-search", ""); pg.wait_for_function("document.querySelectorAll('#hfHosted tbody tr').length === 2")
    pg.fill("#hf-token", "hf_wrong_token_value"); pg.click("#hfPanel form .btn.primary"); pg.wait_for_selector("#hfPanel .fnote.bad")
    check("bad Hugging Face token refused", "rejected" in pg.inner_text("#hfPanel .hfbox"))
    pg.fill("#hf-token", HF_TOKEN); pg.click("#hfPanel form .btn.primary"); pg.wait_for_selector("#hfPanel .where.hf:has-text('Token set')")
    check("token accepted and never shown", "Connected as ada" in pg.inner_text("#hfPanel .hfbox") and HF_TOKEN not in pg.content())
    check("token file is private", os.stat(data / "hf_token").st_mode & 0o777 == 0o600 or sys.platform == "win32")
    pg.locator("#hfHosted tbody tr", has_text="Qwen/Qwen2.5-7B-Instruct").locator("button").click()
    pg.wait_for_selector("#modelsTable tr[data-model='hf:Qwen/Qwen2.5-7B-Instruct']", state="attached"); tab("local")
    row = pg.locator("#modelsTable tr[data-model='hf:Qwen/Qwen2.5-7B-Instruct']")
    check("hosted row says Hugging Face", "Hugging Face · hosted" in row.inner_text())
    row.locator("button:has-text('Test')").click(); pg.wait_for_selector("#modelsTable tr[data-model='hf:Qwen/Qwen2.5-7B-Instruct'] .fnote:has-text('works')")
    call = calls("/router/v1/chat/completions")[-1]
    check("Hugging Face call: its own bearer token, prefix stripped", call["auth"] == f"Bearer {HF_TOKEN}" and call["model"] == "Qwen/Qwen2.5-7B-Instruct", str(call))

    tab("hf"); pg.locator("#hfGguf button:has-text('Download…')").click(); pg.wait_for_selector("#hfGguf select")
    check("quantisations with sizes", "Q4_K_M · 2.5 GB" in pg.inner_text("#hfGguf select") and pg.input_value("#hfGguf select") == "Q4_K_M")
    pg.click("#hfGguf .btn.primary:has-text('Download 2.5 GB')"); pg.wait_for_selector(".progress", timeout=5000)
    pg.wait_for_selector("#modelsTable tr[data-model='hf.co/unsloth/Qwen3-4B-GGUF:Q4_K_M']", state="attached", timeout=15000)
    check("download asked Ollama for the right name", {"pull": "hf.co/unsloth/Qwen3-4B-GGUF:Q4_K_M"} in log())
    check("finished download is marked", "Already downloaded" in pg.inner_text("#hfGguf"))

    tab("or"); pg.wait_for_selector("#orHosted tbody tr")
    check("OpenRouter results: capabilities and price per million", pg.locator("#orHosted tbody tr").count() == 2 and "log-probs" in pg.inner_text("#orHosted") and "$0.15/M tokens" in pg.inner_text("#orHosted"))
    pg.fill("#or-token", "sk-or-v1-wrong_key_value"); pg.click("#orPanel form .btn.primary"); pg.wait_for_selector("#orPanel .fnote.bad")
    pg.fill("#or-token", OR_KEY); pg.click("#orPanel form .btn.primary"); pg.wait_for_selector("#orPanel .where.or:has-text('Key set')")
    check("OpenRouter key accepted and never shown", "Connected as my-laptop-key" in pg.inner_text("#orPanel .hfbox") and OR_KEY not in pg.content())
    pg.locator("#orHosted tbody tr", has_text="gpt-4o-mini").locator("button").click()
    pg.wait_for_selector("#modelsTable tr[data-model='or:openai/gpt-4o-mini']", state="attached"); tab("local")
    pg.locator("#modelsTable tr[data-model='or:openai/gpt-4o-mini'] button:has-text('Test')").click()
    pg.wait_for_selector("#modelsTable tr[data-model='or:openai/gpt-4o-mini'] .fnote:has-text('works')")
    call = calls("/or/api/v1/chat/completions")[-1]
    check("OpenRouter call: its own key, never the Hugging Face token", call["auth"] == f"Bearer {OR_KEY}" and call["model"] == "openai/gpt-4o-mini", str(call))

    tab("az"); pg.wait_for_selector("#azPanel #az-endpoint")
    pg.fill("#az-endpoint", "https://evil.example.com"); pg.fill("#az-token", AZ_KEY); pg.click("#azPanel form .btn.primary"); pg.wait_for_selector("#azPanel .fnote.bad")
    check("non-Azure endpoint refused, nothing sent to it", "not an Azure endpoint" in pg.inner_text("#azPanel .hfbox"))
    pg.fill("#az-endpoint", stub_url); pg.fill("#az-token", "azure-wrong-key-12345"); pg.click("#azPanel form .btn.primary")
    pg.wait_for_selector("#azPanel .fnote.bad:has-text('rejected')")
    pg.fill("#az-token", AZ_KEY); pg.click("#azPanel form .btn.primary"); pg.wait_for_selector("#azPanel .where.az:has-text('Connected')")
    check("Azure key stored privately and never shown", AZ_KEY not in pg.content() and (os.stat(data / "az_key").st_mode & 0o777 == 0o600 or sys.platform == "win32"))
    form = "#azPanel form:has(#az-manual) .btn"
    pg.fill("#az-manual", "has space"); pg.click(form); pg.wait_for_selector("#azPanel .fnote.bad")
    check("bad deployment name refused", "deployment name" in pg.inner_text("#azPanel").lower())
    for name in ("my-gpt", "missing-dep"):
        pg.fill("#az-manual", name); pg.click(form); pg.wait_for_selector(f"#azDeployments tr[data-model='az:{name}']")
    pg.locator("#azDeployments tr[data-model='az:my-gpt'] button:has-text('Test')").click(); pg.wait_for_selector("#azDeployments tr[data-model='az:my-gpt'] .fnote:has-text('works')")
    pg.locator("#azDeployments tr[data-model='az:missing-dep'] button:has-text('Test')").click(); pg.wait_for_selector("#azDeployments tr[data-model='az:missing-dep'] .fnote.bad")
    check("missing deployment explained", "No deployment named missing-dep" in pg.inner_text("#azDeployments"))
    check("Azure call: its own key and the deployment name", calls("/openai/v1/chat/completions", model="my-gpt")[-1]["auth"] == f"Bearer {AZ_KEY}")
    tab("local")
    check("Azure row says Azure Foundry", "Azure Foundry · hosted" in pg.locator("#modelsTable tr[data-model='az:my-gpt']").inner_text())
    shot("models.png")

    # ---- a hosted model used as an orchestrator's argument extractor, per provider ----
    for prefix, group, model, key in [("hf:", "Hugging Face", "hf:Qwen/Qwen2.5-7B-Instruct", HF_TOKEN), ("or:", "OpenRouter", "or:openai/gpt-4o-mini", OR_KEY), ("az:", "Azure Foundry", "az:my-gpt", AZ_KEY)]:
        nav("orch"); pg.wait_for_selector(".form"); pg.click("[data-new]"); pg.wait_for_timeout(300); pg.select_option("#f-extractor", "openai_compat")
        groups = pg.locator("#f-model optgroup").evaluate_all("els => els.map(e => e.label)")
        check(f"dropdown has a {group} group", f"{group} (hosted, no download)" in groups, str(groups))
        pg.select_option("#f-model", model); pg.fill("#f-name", f"{group} extractor")
        check(f"{group} model is marked as hosted on its card", "☁" in pg.locator("#orchPanel .card.sel").inner_text())
        pg.click("#f-use"); pg.wait_for_selector("#msg")
        check(f"answer through a {group} extractor", "36" in ask("calculate 15% of 240"))
        sent = [e for e in log() if e.get("has_schema") and e.get("auth") == f"Bearer {key}"]
        check(f"{group} extraction used its own key and a JSON schema", bool(sent) and sent[-1]["model"] == model[len(prefix):],
              f"routed to {pg.locator('.a .badge').last.inner_text()}; last calls: {log()[-4:]}")

    # ---- keyless Azure: the (fake) Azure CLI provides the token ----
    nav("models"); tab("az"); pg.click("#azPanel .btn:has-text('Disconnect')"); pg.wait_for_selector("#az-endpoint")
    check("disconnect deletes the key and the endpoint", not (data / "az_key").exists() and not (data / "az.json").exists())
    nav("orch"); pg.wait_for_selector(".form")
    check("orchestrator names the missing connection", "connection" in pg.inner_text("#orchMsg"), pg.inner_text("#orchMsg"))
    if has_az_cli:
        nav("models"); tab("az"); pg.fill("#az-endpoint", stub_url); pg.check("#az-auth-cli")
        check("key box hidden for CLI sign-in", pg.locator("#az-token").count() == 0)
        pg.click("#azPanel form .btn.primary"); pg.wait_for_selector("#azPanel .where.az:has-text('Connected')")
        check("CLI mode stores no key", "no key stored" in pg.inner_text("#azPanel .hfbox") and not (data / "az_key").exists())
        pg.locator("#azDeployments tr[data-model='az:my-gpt'] button:has-text('Test')").click(); pg.wait_for_selector("#azDeployments tr[data-model='az:my-gpt'] .fnote:has-text('works')")
        check("CLI call carried the Entra token", calls("/openai/v1/chat/completions", model="my-gpt")[-1]["auth"] == f"Bearer {ENTRA}")
        shot("azure.png")
        pg.click("#azPanel .btn:has-text('Disconnect')"); pg.wait_for_selector("#az-endpoint")

    # ---- tabs behave like pages ----
    tab("or"); pg.fill("#or-search", "gpt"); tab("hf"); tab("or")
    check("a half-typed search survives switching tabs", pg.input_value("#or-search") == "gpt")
    pg.reload(); pg.wait_for_selector("[data-tab=or].active"); pg.wait_for_selector("#orHosted tbody tr")
    check("refresh stays on the OpenRouter tab", pg.evaluate("location.hash") == "#/models/or" and pg.locator("#orPanel").is_visible() and not pg.locator("#tab-local").is_visible())
    nav("experts"); nav("models"); pg.wait_for_selector("[data-tab=or].active")
    check("the sidebar returns to the last tab", pg.evaluate("location.hash") == "#/models/or")
    pg.goto(base + "#/models/az"); pg.reload(); pg.wait_for_selector("[data-tab=az].active")
    check("deep link to the Azure tab", pg.locator("#azPanel").is_visible())
    pg.goto(base + "#/models/nonsense"); pg.reload(); pg.wait_for_selector("[data-tab=local].active")
    check("unknown tab falls back to My models", pg.locator("#modelsTable").is_visible())

    # ---- theme and sidebar ----
    pg.click("#themeBtn"); check("light theme", pg.evaluate("document.documentElement.classList.contains('light')"))
    pg.reload(); pg.wait_for_selector(".sidebar"); check("theme persists", pg.evaluate("document.documentElement.classList.contains('light')")); pg.click("#themeBtn")
    pg.click("#hamb"); check("sidebar collapses", pg.evaluate("document.body.classList.contains('collapsed')")); pg.click("#hamb2")
    nav("traces"); pg.wait_for_timeout(600); check("traces are listed", pg.locator("#tracesPanel tbody tr").count() >= 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", type=Path, help="also save a few screenshots here")
    args = ap.parse_args()
    if not (ROOT / "noah/web/dist/index.html").exists():
        sys.exit("The UI is not built: run `cd frontend && npm run build` first.")
    stub_port, port = free_port(), free_port()
    stub = ThreadingHTTPServer(("127.0.0.1", stub_port), Stub)
    threading.Thread(target=stub.serve_forever, daemon=True).start()
    stub_url = f"http://127.0.0.1:{stub_port}"
    if args.shots:
        args.shots.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp)
        has_az_cli = sys.platform != "win32"
        if has_az_cli:  # a fake `az account get-access-token`
            (data / "bin").mkdir()
            az = data / "bin" / "az"
            az.write_text(f"#!/bin/sh\necho '{{\"accessToken\":\"{ENTRA}\",\"expires_on\":9999999999}}'\n")
            az.chmod(az.stat().st_mode | stat.S_IEXEC)
        (data / "d.json").write_text("[]")
        shutil.copytree(ROOT / "experts", data / "experts", ignore=shutil.ignore_patterns("__pycache__"))   # the editor writes here, not into the repo
        env = {**os.environ, "PATH": f"{data / 'bin'}{os.pathsep}{os.environ['PATH']}", "NOAH_AZURE_ALLOW_LOCAL": "1",
               "NOAH_LLM_URL": f"{stub_url}/ollama", "NOAH_HF_ROUTER": f"{stub_url}/router/v1", "NOAH_HF_HUB": f"{stub_url}/hub",
               "NOAH_OR_BASE": f"{stub_url}/or/api/v1", "NOAH_HF_TOKEN_FILE": str(data / "hf_token"), "NOAH_HF_MODELS": str(data / "hf_models.json"),
               "NOAH_OR_TOKEN_FILE": str(data / "or_key"), "NOAH_OR_MODELS": str(data / "or_models.json"), "NOAH_AZ_TOKEN_FILE": str(data / "az_key"),
               "NOAH_AZ_SETTINGS": str(data / "az.json"), "NOAH_AZ_MODELS": str(data / "az_models.json"), "NOAH_PROFILES": str(data / "profiles.json"),
               "NOAH_DISABLED_EXPERTS": str(data / "d.json"), "NOAH_EXPERTS": str(data / "experts"), "NOAH_TRACES": str(data / "traces.jsonl"), "NOAH_FILES_DIR": str(ROOT / "data/files"),
               "HF_TOKEN": "", "OPENROUTER_API_KEY": "", "AZURE_FOUNDRY_API_KEY": "", "AZURE_OPENAI_API_KEY": "", "AZURE_FOUNDRY_ENDPOINT": "", "AZURE_OPENAI_ENDPOINT": ""}
        server = subprocess.Popen([sys.executable, "-m", "uvicorn", "noah.server:app", "--port", str(port)], cwd=ROOT, env=env,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(90):
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/api/config", timeout=1); break
                except OSError:
                    time.sleep(1)
            else:
                sys.exit("The server did not start.")
            os.environ.update({k: v for k, v in env.items() if k.startswith("NOAH_")})
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path=os.environ.get("CHROMIUM") or None, args=["--no-sandbox"])
                pg = browser.new_page(viewport={"width": 1280, "height": 900})
                problems = []
                pg.on("pageerror", lambda e: problems.append(str(e)))
                pg.on("console", lambda m: problems.append(m.text) if m.type in ("error", "warning") else None)
                run_checks(pg, f"http://127.0.0.1:{port}/", stub_url, data, has_az_cli, args.shots)
                real = [m for m in problems if not any(code in m for code in ("400", "401", "404", "422", "502"))]  # the deliberately wrong keys above
                check("no console errors or Vue warnings", not real, str(real))
                browser.close()
        finally:
            server.terminate()
            stub.shutdown()
    print("\nFAILED: " + ", ".join(failures) if failures else "\nAll checks passed.")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
