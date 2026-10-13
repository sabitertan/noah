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

    # Hugging Face: local and hosted models side by side, then a GGUF download with its quantisations (nothing is downloaded)
    hf = b.new_page(viewport={"width": 1280, "height": 900})
    hf.goto(base + "/#/models"); hf.wait_for_selector("#hfHosted tbody tr")
    hf.fill("#hf-search", "qwen"); hf.wait_for_timeout(1500); hf.wait_for_selector("#hfHosted tbody tr")
    model = hf.locator("#hfHosted tbody tr").first.locator("td").first.inner_text()
    hf.locator("#hfHosted tbody tr button:has-text('Use hosted')").first.click()
    hf.wait_for_selector(f"#modelsTable tr[data-model='hf:{model}']")
    hf.locator(f"#modelsTable tr[data-model='hf:{model}'] button:has-text('Test')").wait_for()
    hf.evaluate("document.querySelector('#v-models').scrollTop = 0"); hf.wait_for_timeout(200)
    hf.screenshot(path="docs/models.png")
    hf.locator("#hfGguf button:has-text('Download…')").first.click(); hf.wait_for_selector("#hfGguf select")
    hf.locator("#hfGguf").scroll_into_view_if_needed(); hf.evaluate("document.querySelector('#hfGguf').scrollIntoView({block: 'start'})")
    hf.wait_for_timeout(300)
    hf.screenshot(path="docs/huggingface.png")
    hf.request.delete(f"{base}/api/hf/my/{model}")  # leave the server as we found it
    b.close()
