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
    # A copy of the photographs: this test adds one, and the folder the other
    # tests share must not change under them.
    import shutil

    photos = tmp / "photos"
    shutil.copytree(E.PHOTOS, photos)
    ed = E.Editor(tmp / "album", E.write_models(tmp / "good.yaml", url), tmp / "editor.log",
                  photos=photos)
    problems: list[str] = []
    try:
        ed.wait(lambda: (ed.get("/api/album") or {}).get("preflight", {})
                .get("plan", {}).get("status") == "ok", 30, "startup check never passed")
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.on("pageerror", lambda e: problems.append(f"script error: {e}"))
            # Two things are not problems. A preview refresh that arrives while
            # another job runs is answered 409 and the editor tries again by
            # itself. And a download ends the request that started it.
            def busy(url: str, status: int | None = None) -> bool:
                return url.endswith("/api/rerender") and status in (409, None)

            page.on("console", lambda m: problems.append(f"console error: {m.text}")
                    if m.type == "error" and not busy(m.location.get("url", "")) else None)
            page.on("response", lambda r: problems.append(f"{r.status} {r.request.method} {r.url}")
                    if r.status >= 400 and not busy(r.url, r.status) else None)
            page.on("requestfailed", lambda r: problems.append(f"request failed: {r.url}")
                    if "/export/" not in r.url else None)

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

            def job_done(what: str) -> dict:
                # the editor waits a moment before refreshing the preview: let
                # that refresh start, then finish
                page.wait_for_function("!dirty", timeout=60000)
                ed.wait(lambda: not (ed.get("/api/job") or {}).get("running"), 180, f"{what} did not finish")
                j = ed.get("/api/job")
                assert not j.get("error"), j
                return j

            # 6. adding a photograph from the file chooser
            src = sorted((E.PHOTOS).glob("*.jpg"))[3]
            extra = tmp / "added_in_the_browser.jpg"
            extra.write_bytes(src.read_bytes())
            page.set_input_files("#fileAdd", str(extra))
            page.wait_for_function(f"document.querySelectorAll('#grid img').length >= {n + 1}", timeout=30000)
            assert any(p["id"] == "added_in_the_browser" for p in ed.get("/api/photos"))
            job_done("the preview after adding a photograph")
            ok("a photograph added from the file chooser appears")

            # 7. a setting changed in the editor is saved, and costs no model call
            asked = len(fake.requests)
            page.click("#tabSettings")
            page.wait_for_selector("#settings .opt[data-set=density]", timeout=15000)
            before = ed.get("/api/config")
            cur = next(o["value"] for o in before["options"] if o["key"] == "density")
            page.click(f"#settings .opt[data-set=density]:not([data-v='{cur}']) >> nth=0")
            page.wait_for_function(
                "fetch('/api/config').then(r => r.json()).then(c => "
                f"c.options.find(o => o.key === 'density').value !== '{cur}')", timeout=15000)
            job_done("the preview after a setting change")
            assert len(fake.requests) == asked, "changing a setting called the model"
            ok("a setting changed in the editor is saved and the preview follows, with no model call")

            # 8. export: the cover PDF arrives as a download
            page.wait_for_function("!document.getElementById('btnExport').disabled", timeout=30000)
            with page.expect_download(timeout=180000) as dl:
                page.click("#btnExport")
                page.click("#exportList button[data-k=cover]")
            got = tmp / "cover-download.pdf"
            dl.value.save_as(str(got))
            assert got.read_bytes()[:4] == b"%PDF", "the exported cover is not a PDF"
            ok("export: the cover PDF downloads")

            # 9. asking for a change in words makes a second version
            page.goto(f"http://127.0.0.1:{ed.port}/")
            page.wait_for_selector("#grid img", timeout=20000)
            versions = len(ed.get("/api/album")["versions"])
            page.click("#btnPlan")
            page.wait_for_selector("#dlgRevise[open]", timeout=5000)
            page.fill("#reviseText", "Seite 4 ist überladen")
            page.click("#reviseGo")
            ed.wait(lambda: len((ed.get("/api/album") or {}).get("versions", [])) > versions,
                    180, "the revision made no new version")
            job_done("the revision")
            page.click("#tabBook")
            page.wait_for_function(
                "[...document.querySelectorAll('.vrow')].some(r => r.textContent.includes('Seite 4'))",
                timeout=30000)
            ok("a change asked for in words becomes a new version in the book")

            # 10. a photograph removed from the album leaves the list, and comes back
            page.click("#tabPhotos")
            page.wait_for_selector("#grid img", timeout=10000)
            before = len(ed.get("/api/photos"))
            page.once("dialog", lambda d: d.accept())
            page.click("#grid img >> nth=2")
            page.wait_for_selector("#editor", state="visible", timeout=5000)
            page.click("#optRemove")
            page.wait_for_function(
                f"document.querySelectorAll('#grid img').length === {before - 1}", timeout=20000)
            # waited for, not assumed: the list is rebuilt after the change is saved
            ed.wait(lambda: len(ed.get("/api/photos") or []) == before - 1, 30,
                    "the removed photograph is still listed")
            gone = ed.get("/api/photos/removed")
            assert len(gone) == 1, gone
            job_done("the preview after removing a photograph")
            import json

            latest = sorted((tmp / "album" / "layouts").glob("v0*[0-9].json"))[-1]
            cur = json.loads(latest.read_text(encoding="utf-8"))
            placed = {s["photo_id"] for sp in cur.get("spreads", []) for s in sp["slots"]}
            assert gone[0]["id"] not in placed, "a removed photograph is still in the book"
            page.click("#tabSettings")
            page.wait_for_selector("#removedList [data-restore]", timeout=15000)
            page.click("#removedList [data-restore]")
            page.wait_for_function(
                "fetch('/api/photos/removed').then(r => r.json()).then(a => a.length === 0)",
                timeout=15000)
            ed.wait(lambda: len(ed.get("/api/photos") or []) == before, 30,
                    "the photograph brought back is not listed again")
            job_done("the preview after bringing a photograph back")
            ok("a photograph is removed from the album and brought back")

            # 11. the landmark status line reads as a sentence in each state
            line = page.evaluate(
                "placesLine({countries: {CH: 'ready', IT: 'downloading'}, osm: 'unavailable'})")
            assert "CH ready" in line and "IT downloading" in line and "not answering" in line, line
            assert "not needed yet" in page.evaluate("placesLine({countries: {}, osm: 'idle'})")
            ok("landmark status line")
            browser.close()
    finally:
        ed.stop()
        fake.stop()
    print("editor in a browser: ok")
    return 0


def test_ids_reaching_the_dom_are_escaped():
    """A photograph's id is its filename stem, so it can hold a quote.

    `tile()` builds its markup with innerHTML. An id like `a" onerror="…`
    arriving from a folder the user pointed at (the upload route sanitises
    names; a folder is read as it is) would then run script in the editor's
    own origin — the origin whose API can read the key store. An id holding
    "#" or "?" truncated the thumbnail URL instead, and the picture silently
    failed to load.

    This needs no browser, so it also guards the file where Chromium is
    absent. It checks the sinks rather than the symptom: every id written
    into markup or into a URL must go through esc() or encodeURIComponent().
    """
    html = (Path(__file__).resolve().parents[1].parent
            / "phase2" / "static" / "index.html").read_text(encoding="utf-8")

    # Every /thumb/ and /photo/ URL built from an id encodes it.
    for line_no, line in enumerate(html.splitlines(), 1):
        for route in ("/thumb/${", "/photo/${"):
            at = line.find(route)
            if at < 0:
                continue
            expr = line[at + len(route):line.index("}", at)]
            assert expr.startswith("encodeURIComponent("), \
                f"index.html:{line_no}: {route}{expr}}} is not URL-encoded"

    # No id is interpolated raw into innerHTML.
    for line_no, line in enumerate(html.splitlines(), 1):
        if "innerHTML" not in line:
            continue
        for raw in ("${p.id}", "${id}", "${photo.id}"):
            assert raw not in line, \
                f"index.html:{line_no}: {raw} goes into innerHTML unescaped; use esc()"


def test_editor_in_a_browser():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
