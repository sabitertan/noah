"""Regenerate the README screenshots against a running server (default http://localhost:8765)."""
import os
import sys
from playwright.sync_api import sync_playwright

base = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8765"


def ask(pg, q):
    n = pg.locator(".a .body").count()
    pg.fill("#msg", q); pg.press("#msg", "Enter")
    pg.wait_for_function(f"document.querySelectorAll('.a .body').length>{n}", timeout=120000)


with sync_playwright() as p:
    b = p.chromium.launch(executable_path=os.environ.get("CHROMIUM"), args=["--no-sandbox"])  # CHROMIUM=/path or `playwright install chromium`
    pg = b.new_page(viewport={"width": 1280, "height": 800})  # fresh context: no saved chats
    pg.goto(base); pg.wait_for_selector(".chip")
    pg.screenshot(path="docs/home.png")

    # three experts, short answers, last routing decision expanded
    for q in ["calculate 15% of 240", "find my budget file", "what's the weather in Paris"]:
        ask(pg, q)
    pg.locator("summary:has-text('routing decision')").last.click()
    pg.wait_for_timeout(300)
    pg.screenshot(path="docs/chat.png")

    # code expert in a new chat, scrolled to the top of the answer
    pg.click("#newChat")
    ask(pg, "write a C# method that reverses a string")
    pg.locator(".a").first.scroll_into_view_if_needed()
    pg.evaluate("document.getElementById('log').scrollTop = 0")
    pg.wait_for_timeout(300)
    pg.screenshot(path="docs/code.png")

    pg.click("[data-view=orch]"); pg.wait_for_timeout(400)
    pg.screenshot(path="docs/orchestrators.png")

    # Models page tabs: My models (local + hosted, labelled), Hugging Face (hosted or GGUF download), OpenRouter (hosted)
    hf = b.new_page(viewport={"width": 1280, "height": 900})
    hf.goto(base + "/#/models"); hf.wait_for_selector("#modelsTable tbody tr")

    def open_tab(name):
        hf.click(f"[data-tab={name}]"); hf.wait_for_selector(f"[data-tab={name}].active")

    def use_hosted(prefix, key, query):  # add the first search result that supports tool calls; returns its id
        open_tab(key)
        hf.wait_for_selector(f"#{key}Hosted tbody tr")
        hf.fill(f"#{key}-search", query); hf.wait_for_timeout(1500); hf.wait_for_selector(f"#{key}Hosted tbody tr")
        row = hf.locator(f"#{key}Hosted tbody tr", has_text="tool calls").first
        mid = row.locator("td").first.inner_text().split("\n")[0]
        row.locator("button:has-text('Use hosted')").click()
        hf.wait_for_selector(f"#modelsTable tr[data-model='{prefix}{mid}']", state="attached")
        return mid
    hf_model, or_model = use_hosted("hf:", "hf", "qwen"), use_hosted("or:", "or", "llama")

    open_tab("local")
    hf.evaluate("document.querySelector('#v-models').scrollTop = 1e5"); hf.wait_for_timeout(200)   # the hosted rows are at the bottom
    hf.screenshot(path="docs/models.png")

    open_tab("hf")
    hf.locator("#hfGguf button:has-text('Download…')").first.click(); hf.wait_for_selector("#hfGguf select")
    hf.evaluate("document.querySelector('#hfGguf').scrollIntoView({block: 'start'})"); hf.wait_for_timeout(300)
    hf.screenshot(path="docs/huggingface.png")

    open_tab("or")
    hf.evaluate("document.querySelector('#v-models').scrollTop = 0"); hf.wait_for_timeout(300)
    hf.screenshot(path="docs/openrouter.png")
    hf.request.delete(f"{base}/api/hf/my/{hf_model}"); hf.request.delete(f"{base}/api/or/my/{or_model}")  # leave the server as we found it
    b.close()
