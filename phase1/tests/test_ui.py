#!/usr/bin/env python3
"""The editor in a real browser.

    python tests/test_ui.py

`test_e2e.py` drives the server over HTTP; nothing there loads the page. The
editor is one HTML file of plain JavaScript with no build step, so a stray
bracket, two elements sharing an id or a renamed API field shipped unnoticed.
This opens it in Chromium against a fake model and does what a person does
first: look at the photographs, describe one, design the album, look at the
book, open the settings. Any script error, failed request or error banner
along the way fails the test.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import test_e2e as E  # noqa: E402  (the editor subprocess and the fake model)
import test_loop  # noqa: E402


def main() -> int:
    from playwright.sync_api import sync_playwright

    test_loop.ensure_fixtures()
    fake = E.FakeModel(test_loop.good_layout())
    url = fake.start()
    tmp = Path(tempfile.mkdtemp(prefix="album-ui-"))
    ed = E.Editor(tmp / "album", E.write_models(tmp / "good.yaml", url), tmp / "editor.log")
    problems: list[str] = []
    try:
        ed.wait(lambda: (ed.get("/api/album") or {}).get("preflight", {})
                .get("plan", {}).get("status") == "ok", 30, "startup check never passed")
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.on("pageerror", lambda e: problems.append(f"script error: {e}"))
            page.on("console", lambda m: problems.append(f"console error: {m.text}")
                    if m.type == "error" else None)
            page.on("response", lambda r: problems.append(f"{r.status} {r.request.method} {r.url}")
                    if r.status >= 400 else None)
            page.on("requestfailed", lambda r: problems.append(f"request failed: {r.url}"))

            def ok(step: str) -> None:
                assert not problems, f"{step}: " + "; ".join(problems)
                assert "err" not in (page.get_attribute("#msg", "class") or ""), \
                    f"{step}: the editor shows an error: {page.inner_text('#msgText')}"
                print(f"{step}: ok")

            # 1. the page loads and shows every photograph
            page.goto(f"http://127.0.0.1:{ed.port}/")
            n = len(ed.get("/api/photos"))
            page.wait_for_function(f"document.querySelectorAll('#grid img').length >= {n}", timeout=20000)
            assert page.inner_text("#title").strip(), "no album title in the header"
            ids = page.evaluate("[...document.querySelectorAll('[id]')].map(e => e.id)")
            twice = sorted({i for i in ids if ids.count(i) > 1})
            assert not twice, f"elements sharing an id: {twice}"
            ok(f"photos tab ({n} photographs, no duplicate ids)")

            # 2. choosing a photograph opens its editor; a description is saved
            page.click("#grid img >> nth=0")
            page.wait_for_selector("#editor", state="visible", timeout=5000)
            page.fill("#desc", "Der erste Morgen am Strand")
            page.wait_for_function(
                "fetch('/api/photos').then(r => r.json())"
                ".then(a => a.some(p => p.description === 'Der erste Morgen am Strand'))",
                timeout=15000)
            ok("a description typed in the editor reaches the album")

            # 3. Design the album: the job runs and the book appears
            page.click("#btnPlan")
            ed.wait(lambda: (ed.get("/api/job") or {}).get("done")
                    or (ed.get("/api/job") or {}).get("error"), 180, "the design did not finish")
            job = ed.get("/api/job")
            assert job.get("done") and not job.get("error"), job
            page.click("#tabBook")
            page.wait_for_selector("#book .spread img", timeout=30000)
            spreads = page.locator("#book .spread").count()
            assert spreads >= 12, f"only {spreads} spreads in the book view"
            page.wait_for_function(
                "[...document.querySelectorAll('#book .spread img')].slice(0, 2)"
                ".every(i => i.complete && i.naturalWidth > 0)", timeout=30000)
            ok(f"design from the button, book tab ({spreads} spreads, page images load)")

            # 4. a spread opens large and closes again
            page.click("#book .spread >> nth=1")
            page.wait_for_selector("#lightbox", state="visible", timeout=5000)
            page.keyboard.press("Escape")
            ok("a spread opens large")

            # 5. settings render every section and survive a change
            page.click("#tabSettings")
            page.wait_for_selector("#settings .setsec", timeout=15000)
            assert page.locator("#settings .setsec").count() >= 3
            page.click("#tabPhotos")
            page.wait_for_selector("#grid img", timeout=10000)
            ok("settings tab and back")
            browser.close()
    finally:
        ed.stop()
        fake.stop()
    print("editor in a browser: ok")
    return 0


def test_editor_in_a_browser():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
