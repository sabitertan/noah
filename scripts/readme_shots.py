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
    b.close()
