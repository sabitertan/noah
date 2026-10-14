"""Drive the UI with the system Chromium and save screenshots + console errors."""
import sys

from playwright.sync_api import sync_playwright

out = sys.argv[1] if len(sys.argv) > 1 else "research/shots"
with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/snap/bin/chromium", args=["--no-sandbox"])
    pg = b.new_page(viewport={"width": 1280, "height": 800})
    errs: list[str] = []
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto("http://localhost:8765/"); pg.wait_for_timeout(800)
    pg.screenshot(path=f"{out}/1-empty.png")
    pg.fill("#msg", "calculate 15% of 240"); pg.press("#msg", "Enter"); pg.wait_for_selector(".a .body", timeout=60000)
    pg.fill("#msg", "find my budget file"); pg.press("#msg", "Enter"); pg.wait_for_function("document.querySelectorAll('.a .body').length>=2", timeout=60000)
    pg.click("summary:has-text('routing decision')"); pg.wait_for_timeout(300)
    pg.screenshot(path=f"{out}/2-chat.png")
    pg.click("[data-view=orch]"); pg.wait_for_timeout(300); pg.screenshot(path=f"{out}/3-orch.png")
    pg.click("[data-view=experts]"); pg.wait_for_timeout(300); pg.screenshot(path=f"{out}/4-experts.png")
    pg.click("[data-view=traces]"); pg.wait_for_timeout(500); pg.screenshot(path=f"{out}/5-traces.png")
    pg.click("#themeBtn"); pg.click("[data-view=models]"); pg.wait_for_timeout(300); pg.screenshot(path=f"{out}/6-light-models.png")
    print("console errors:", errs or "none")
    b.close()
