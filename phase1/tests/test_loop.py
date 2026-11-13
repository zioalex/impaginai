#!/usr/bin/env python3
"""End-to-end test of the Phase 1 loop with no API key and no network.

A scripted completer stands in for the model. Its first answer is deliberately
invalid — a template given the wrong number of slots — which is the single most
likely way a real model breaks the contract. The test asserts that the loop
rejects it, feeds the validation error back, accepts the corrected answer, and
that history, revision and rollback all behave.

    python tests/test_loop.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT.parent / "phase0"))
sys.path.insert(0, str(ROOT))

import render as R  # noqa: E402
import photos as PH  # noqa: E402
import planner  # noqa: E402
from planner import Usage  # noqa: E402
from versions import Store  # noqa: E402

PHOTO_DIR = ROOT.parent / "phase0" / "photos"
CAPTIONS = ROOT / "examples" / "captions.yaml"
import tempfile as _tempfile  # noqa: E402

# Not /tmp: that path does not exist on Windows.
ALBUM = Path(_tempfile.gettempdir()) / "album_test"


def good_layout(spread_count: int = 12, summary: str | None = None) -> dict:
    spreads = [
        {"index": 1, "section_id": "s1", "template": "title-page",
         "slots": [{"photo_id": "p001", "focal": [0.6, 0.45], "caption": None, "caption_position": "none"}],
         "text": {"eyebrow": "Sardinien · 2026", "title": "Sardinien 2026",
                  "subtitle": "Zehn Tage am Meer", "body": "Zehn Tage, ein Auto, kein Plan."},
         "note": "title facing the opening photograph"},
        {"index": 2, "section_id": "s1", "template": "two-up-hero",
         "slots": [{"photo_id": "p015", "focal": [0.5, 0.45], "caption": None, "caption_position": "none"},
                   {"photo_id": "p005", "focal": [0.5, 0.4], "caption": "Der alte Feigenbaum", "caption_position": "below"}],
         "options": {"side": "left"}, "note": "bleeding hero facing a smaller frame"},
        {"index": 3, "section_id": "s1", "template": "two-up-equal",
         "slots": [{"photo_id": "p002", "focal": [0.5, 0.35], "caption": "Morgens am Hafen", "caption_position": "below"},
                   {"photo_id": "p003", "focal": [0.5, 0.4], "caption": "Die Gasse hinter der Kirche", "caption_position": "below"}],
         "note": "two portraits, same weight"},
        {"index": 4, "section_id": "s1", "template": "single-centered",
         "slots": [{"photo_id": "p005", "focal": [0.5, 0.5], "caption": "Der alte Feigenbaum", "caption_position": "below"}],
         "options": {"side": "right"}, "note": "the book breathes"},
        {"index": 5, "section_id": "s2", "template": "section-divider",
         "slots": [{"photo_id": "p006", "focal": [0.5, 0.4], "caption": None, "caption_position": "none"}],
         "text": {"eyebrow": "Kapitel zwei", "title": "Der Wind", "body": "Vier Tage Nordwest."},
         "note": "chapter title on black facing the chapter's opening image"},
        {"index": 6, "section_id": "s2", "template": "hero-and-trio",
         "slots": [{"photo_id": "p008", "focal": [0.5, 0.5], "caption": None, "caption_position": "none"},
                   {"photo_id": "p009", "focal": [0.35, 0.5], "caption": "Abendessen draußen", "caption_position": "below"},
                   {"photo_id": "p010", "focal": [0.5, 0.5], "caption": None, "caption_position": "none"},
                   {"photo_id": "p004", "focal": [0.5, 0.5], "caption": None, "caption_position": "none"}],
         "note": "bleeding hero facing three composed on one page"},
        {"index": 7, "section_id": "s2", "template": "grid-four",
         "slots": [{"photo_id": "p011", "focal": [0.5, 0.5], "caption": None, "caption_position": "none"},
                   {"photo_id": "p012", "focal": [0.5, 0.5], "caption": None, "caption_position": "none"},
                   {"photo_id": "p014", "focal": [0.5, 0.5], "caption": None, "caption_position": "none"},
                   {"photo_id": "p007", "focal": [0.5, 0.4], "caption": None, "caption_position": "none"}],
         "note": "supporting details"},
        {"index": 8, "section_id": "s2", "template": "full-bleed-single",
         "slots": [{"photo_id": "p015", "focal": [0.5, 0.45],
                    "caption": "Letzter Abend", "caption_position": "bottom-left"}],
         "options": {"side": "right"}, "note": "closing image"},
        {"index": 9, "section_id": "s2", "template": "two-up-hero",
         "slots": [{"photo_id": "p006", "focal": [0.5, 0.4], "caption": None, "caption_position": "none"},
                   {"photo_id": "p004", "focal": [0.5, 0.5], "caption": "Mittagslicht", "caption_position": "below"}],
         "options": {"side": "left"}, "note": "hero left, inset right"},
        {"index": 10, "section_id": "s2", "template": "photo-and-text",
         "slots": [{"photo_id": "p007", "focal": [0.45, 0.45], "caption": None, "caption_position": "none"}],
         "text": {"eyebrow": "Trockenmauern", "body": "Der Weg nach oben dauerte länger als gedacht."},
         "options": {"side": "left"}, "note": "narrative beat"},
        {"index": 11, "section_id": "s2", "template": "single-centered",
         "slots": [{"photo_id": "p010", "focal": [0.5, 0.5], "caption": "Santa Maria, geschlossen", "caption_position": "below"}],
         "options": {"side": "right"}, "note": "quiet page before the end"},
        {"index": 12, "section_id": "s2", "template": "text-page", "slots": [],
         "text": {"eyebrow": "Zum Schluss", "title": "Zurück", "body": "Das Licht ging schnell."},
         "options": {"side": "left"}, "note": "closing text — the one deliberate pause"},
    ][:spread_count]
    return {
        "meta": {"title": "Sardinien 2026", "subtitle": "Zehn Tage am Meer",
                 "language": "de", "format": "21x21", "spread_count": len(spreads)},
        "sections": [{"id": "s1", "title": "Ankunft", "intro_text": None},
                     {"id": "s2", "title": "Der Wind", "intro_text": None}],
        "spreads": spreads,
        "cover": {"front_photo_id": "p004", "front_focal": [0.5, 0.45],
                  "title": "Sardinien 2026", "subtitle": "Zehn Tage am Meer",
                  "spine_title": "Sardinien 2026", "back_text": None},
        "change_summary": summary,
    }


def fold_crossing_layout() -> dict:
    """A panorama across the fold — valid JSON, forbidden by project policy."""
    d = good_layout()
    d["spreads"][1] = {
        "index": 2, "section_id": "s1", "template": "panorama-spread",
        "slots": [{"photo_id": "p001", "focal": [0.5, 0.5],
                   "caption": None, "caption_position": "none"}],
        "note": "wide view across both pages",
    }
    return d


def bad_layout() -> dict:
    """two-up-equal given three photographs — the classic contract break."""
    d = good_layout()
    d["spreads"][2]["slots"].append(
        {"photo_id": "p004", "focal": [0.5, 0.5], "caption": None, "caption_position": "none"}
    )
    return d


def scripted(*answers: str):
    seq = list(answers)
    def complete(system, messages):
        return seq.pop(0), Usage(1000, 2000, 1, 0)
    return complete


def ensure_fixtures() -> None:
    """Generate the photo fixtures if they are not there.

    A test that needs a manual setup step is a test people skip, and on a fresh
    clone this one died with a bare FileNotFoundError.
    """
    import subprocess

    if not PHOTO_DIR.exists() or not any(PHOTO_DIR.glob("*.jpg")):
        gen = ROOT.parent / "phase0" / "tools" / "make_placeholder_photos.py"
        print(f"generating fixtures: {gen.name}")
        r = subprocess.run([sys.executable, str(gen)], capture_output=True, text=True)
        # say why: a bare "exit status 1" hid a font path that only Linux has
        assert r.returncode == 0, f"{gen.name} failed:\n{r.stderr[-2000:]}"
    demo = ROOT / "demo_photos"
    if not demo.exists() or not any(demo.glob("*.jpg")):
        gen = ROOT / "tools" / "make_demo_photos.py"
        if gen.exists():
            print(f"generating fixtures: {gen.name}")
            try:
                subprocess.run([sys.executable, str(gen)], check=True, capture_output=True)
            except subprocess.CalledProcessError:
                print("  (skipped: needs piexif)")


def test_run_classification() -> None:
    """The retry loop's separate budgets, with fake completers (no network, no
    rendering). An empty reply must change the *request*, never the messages."""
    import tempfile
    from planner import Reply, TransientError, PlanningError

    sleeps: list[float] = []
    planner._sleep = sleeps.append
    good = json.dumps(good_layout())
    u = Usage(10, 20, 1, 0)

    def script(*steps):
        seq, calls = list(steps), []
        def complete(system, messages, **opts):
            calls.append({"messages": json.loads(json.dumps(messages)), "opts": opts})
            step = seq.pop(0)
            if isinstance(step, Exception):
                raise step
            return step
        return complete, calls

    def run(complete, **kw):
        return planner._run("sys", "hello", complete, **kw)

    # empty + length -> same messages, reasoning low, 32000 tokens
    c, calls = script(Reply("", u, finish="length", raw_finish="max_tokens", reasoning_tokens=15800),
                      Reply(good, u, finish="stop"))
    res = run(c)
    assert len(calls) == 2 and calls[1]["opts"] == {"reasoning": "low", "max_tokens": 32000}, calls
    assert calls[0]["messages"] == calls[1]["messages"] and len(calls[1]["messages"]) == 1
    assert res.usage.input_tokens == 20, "failed calls must still be counted"

    # length twice -> clear error
    c, calls = script(Reply("", u, finish="length", reasoning_tokens=5),
                      Reply("", u, finish="length", reasoning_tokens=5))
    try:
        run(c); raise AssertionError("expected PlanningError")
    except PlanningError as exc:
        assert "reasoning" in str(exc) and "32,000" in str(exc), exc
    assert len(calls) == 2

    # transient x2 then good: no repair consumed, no messages change, 2s then 4s
    sleeps.clear()
    c, calls = script(TransientError("503"), TransientError("429", retry_after=3), Reply(good, u))
    run(c)
    assert sleeps == [2, 4], sleeps
    assert all(len(x["messages"]) == 1 for x in calls)

    # finish="error" is transient; retry_after larger than backoff wins, capped at 30
    sleeps.clear()
    c, _ = script(Reply("", u, finish="error", error="upstream"),
                  TransientError("x", retry_after=99), Reply(good, u))
    run(c)
    assert sleeps == [2, 30], sleeps

    # transient budget exhausted
    c, calls = script(*[TransientError("down")] * 4)
    try:
        run(c); raise AssertionError("expected PlanningError")
    except PlanningError as exc:
        assert "kept failing" in str(exc) and "down" in str(exc)
    assert len(calls) == 4

    # refusal: immediate
    c, calls = script(Reply("", u, finish="refusal", refusal="cannot help"))
    try:
        run(c); raise AssertionError("expected PlanningError")
    except PlanningError as exc:
        assert "cannot help" in str(exc)
    assert len(calls) == 1

    # empty with response_format -> json_fallback on the next call only
    c, calls = script(Reply("", u, finish="stop", response_format="json_schema"),
                      Reply(good, u, finish="stop"))
    run(c)
    assert calls[1]["opts"] == {"json_fallback": True} and calls[0]["opts"] == {}

    # non-empty bad JSON -> today's repair flow
    c, calls = script(Reply("not json at all", u, finish="stop"), Reply(good, u, finish="stop"))
    run(c)
    msgs = calls[1]["messages"]
    assert len(msgs) == 3 and msgs[1] == {"role": "assistant", "content": "not json at all"}
    assert all(m["content"] for m in msgs)

    # old-style tuple completer
    res = run(lambda system, messages: (good, u))
    assert res.layout.page_count

    # logs written on failure, and a bad log dir never raises
    with tempfile.TemporaryDirectory() as d:
        c, _ = script(Reply("nope", u, finish="stop", raw={"id": "x"}),
                      Reply("nope", u), Reply("nope", u))
        try:
            run(c, log_dir=Path(d) / "logs", kind="plan"); raise AssertionError("expected failure")
        except PlanningError as exc:
            assert "the raw replies are in" in str(exc), exc
        files = sorted((Path(d) / "logs").glob("plan-*-attempt*.json"))
        assert len(files) == 3, files
        rec = json.loads(files[0].read_text(encoding="utf-8"))
        assert rec["text"] == "nope" and rec["raw"] == {"id": "x"} and rec["kind"] == "plan"
    c, _ = script(Reply(good, u))
    # a log folder that cannot be created (its parent is a file) must not
    # stop the plan; /proc was used here, which only Linux has
    with tempfile.TemporaryDirectory() as d:
        blocker = Path(d) / "a-file"
        blocker.write_text("x", encoding="utf-8")
        run(c, log_dir=blocker / "logs")

    # --- fallback model ---------------------------------------------------
    # transient budget exhausted -> switch once, succeed on the fallback
    sleeps.clear()
    events: list[str] = []
    c, calls = script(*[TransientError("down")] * 4)
    f, fcalls = script(Reply(good, u, finish="stop"))
    res = run(c, fallback=f, on_event=events.append)
    assert res.layout.page_count and len(calls) == 4 and len(fcalls) == 1
    assert any("fallback" in e.lower() for e in events), events
    assert any("busy" in e for e in events), events

    # fallback that fails too -> PlanningError that says it was tried
    c, _ = script(*[TransientError("down")] * 4)
    f, fcalls = script(*[TransientError("also down")] * 4)
    try:
        run(c, fallback=f); raise AssertionError("expected PlanningError")
    except PlanningError as exc:
        assert "fallback" in str(exc) and "also down" in str(exc), exc
    assert len(fcalls) == 4, "the fallback gets a fresh transient budget, and no second switch"

    # non-transient RuntimeError (unknown model) -> fallback; opts start clean
    events.clear()
    c, calls = script(RuntimeError("404 model not found"))
    f, fcalls = script(Reply(good, u, finish="stop"))
    res = run(c, fallback=f, on_event=events.append)
    assert res.layout.page_count and fcalls[0]["opts"] == {}
    assert any("fallback" in e.lower() for e in events), events

    # empty/length recovery exhausted -> fallback, with the primary's knobs reset
    c, calls = script(Reply("", u, finish="length", reasoning_tokens=5),
                      Reply("", u, finish="length", reasoning_tokens=5))
    f, fcalls = script(Reply(good, u, finish="stop"))
    run(c, fallback=f)
    assert len(calls) == 2 and fcalls[0]["opts"] == {}

    # refusal does NOT use the fallback
    c, _ = script(Reply("", u, finish="refusal", refusal="cannot help"))
    f, fcalls = script(Reply(good, u))
    try:
        run(c, fallback=f); raise AssertionError("expected PlanningError")
    except PlanningError as exc:
        assert "cannot help" in str(exc)
    assert not fcalls

    # without a fallback a non-transient RuntimeError becomes a PlanningError
    c, calls = script(RuntimeError("404 model not found"))
    try:
        run(c); raise AssertionError("expected PlanningError")
    except PlanningError as exc:
        assert "404 model not found" in str(exc), exc
    assert len(calls) == 1

    # a raising on_event never breaks planning
    def boom(msg):
        raise ValueError("ui is gone")
    c, _ = script(TransientError("503"), Reply(good, u))
    assert run(c, on_event=boom).layout.page_count
    print("planner retry classification: ok")


def test_openai_reply_shapes() -> None:
    """The OpenAI-compatible completer must say *why* a reply is empty, and
    must not call a JSON answer delivered as tool-call arguments empty."""
    import httpx
    import providers
    from planner import TransientError

    queue: list = []
    sent: list = []

    def fake_post(url, headers=None, json=None, timeout=None):
        sent.append(json)
        code, body = queue.pop(0)
        return httpx.Response(code, json=body, request=httpx.Request("POST", url))

    def ch(content, finish="stop", **msg):
        return (200, {"choices": [{"message": {"content": content, **msg},
                                   "finish_reason": finish}],
                      "usage": {"prompt_tokens": 1, "completion_tokens": 2,
                                "completion_tokens_details": {"reasoning_tokens": 7}}})

    saved = httpx.post
    httpx.post = fake_post
    try:
        providers._MODE_CACHE.clear()
        c = providers._openai(providers.Provider(
            "openai", "m", "https://openrouter.ai/api/v1", "k", False, json_mode=True))
        queue[:] = [ch(None, "length", reasoning="xxxx")]
        r = c("s", [{"role": "user", "content": "x"}], reasoning="low", max_tokens=32000)
        assert (r.finish, r.text, r.reasoning_tokens) == ("length", "", 7)
        assert sent[-1]["reasoning"] == {"effort": "low"} and sent[-1]["max_tokens"] == 32000
        queue[:] = [ch(None, "tool_calls", tool_calls=[
            {"type": "function", "function": {"name": "album_layout", "arguments": '{"a": 1}'}}])]
        assert c("s", []).text == '{"a": 1}'
        queue[:] = [(200, {"error": {"message": "upstream overloaded"}})]
        try:
            c("s", [])
            raise AssertionError("200-with-error must be transient")
        except TransientError as exc:
            assert "overloaded" in str(exc)
        queue[:] = [(503, {"error": {"message": "down"}})]
        try:
            c("s", [])
            raise AssertionError("503 must be transient")
        except TransientError:
            pass
        queue[:] = [ch("{}")]
        r = c("s", [], json_fallback=True)
        assert r.response_format == "json_object", r.response_format
    finally:
        httpx.post = saved
        providers._MODE_CACHE.clear()
    print("provider reply shapes: ok")


def test_unplanned_and_spread_overrides():
    """What the editor shows as used is in the design, and a photograph can be
    set across two pages when it has the pixels."""
    ensure_fixtures()
    import overrides as OV
    from schema import Layout
    from types import SimpleNamespace as NS

    placed_in = lambda lay: [s["photo_id"] for sp in lay["spreads"] for s in sp.get("slots", [])]

    # full_page_unplanned_photo_inserted: the planner never placed p013's
    # neighbour; the user marked it full page. It used to be skipped.
    lay = good_layout()
    for sp in lay["spreads"]:
        sp["slots"] = [s for s in sp["slots"] if s["photo_id"] != "p012"] if sp["template"] != "grid-four" \
            else sp["slots"]
    grid = next(sp for sp in lay["spreads"] if sp["template"] == "grid-four")
    grid["slots"] = [s for s in grid["slots"] if s["photo_id"] != "p012"]
    grid["template"] = "trio-page"
    assert "p012" not in placed_in(lay)
    ov = OV.Overrides({})
    ov.set("p012", size="full-page", description="Der letzte Blick")
    notes = OV.apply_to_layout(lay, ov, known=[f"p{i:03d}" for i in range(1, 16)])
    assert placed_in(lay).count("p012") == 1, notes
    host = next(sp for sp in lay["spreads"] if any(s["photo_id"] == "p012" for s in sp["slots"]))
    assert host["template"] == "full-bleed-single" and host["slots"][0]["caption"] == "Der letzte Blick"
    assert [sp["index"] for sp in lay["spreads"]] == list(range(1, len(lay["spreads"]) + 1))
    # ... at its place in the book: after the last spread of its group
    groups = [NS(id="g3", photo_ids=["p011", "p012", "p014", "p007"])]
    lay = good_layout()
    for sp in lay["spreads"]:
        sp["slots"] = [s for s in sp["slots"] if s["photo_id"] != "p012"]
    lay["spreads"] = [sp for sp in lay["spreads"] if sp["template"] != "grid-four"]
    OV.apply_to_layout(lay, ov, groups=groups)
    assert placed_in(lay).count("p012") == 1
    # an unknown id is not invented into the book
    lay = good_layout()
    ov2 = OV.Overrides({})
    ov2.set("zzz", size="full-page")
    OV.apply_to_layout(lay, ov2, known=[f"p{i:03d}" for i in range(1, 16)])
    assert "zzz" not in placed_in(lay)

    # a spread: pulled out of its host, given a user-spread of its own, once
    lay = good_layout()
    ov = OV.Overrides({})
    ov.set("p004", size="spread")
    OV.apply_to_layout(lay, ov, known=[f"p{i:03d}" for i in range(1, 16)])
    hosts = [sp for sp in lay["spreads"] if sp["template"] == "user-spread"]
    assert len(hosts) == 1 and hosts[0]["slots"][0]["photo_id"] == "p004", hosts
    assert Layout.model_validate(lay)
    # and one the planner never placed gets its own spread too
    lay = good_layout()
    lay["spreads"][-1]["slots"] = lay["spreads"][-1]["slots"]
    ov = OV.Overrides({})
    ov.set("p013", size="spread")
    OV.apply_to_layout(lay, ov, known=[f"p{i:03d}" for i in range(1, 16)])
    assert any(sp["template"] == "user-spread" for sp in lay["spreads"])
    # spread, full page and "in a composition" exclude one another
    o = ov.set("p004", size="spread")
    o = ov.set("p004", compose=True)
    assert o.compose and o.size == "auto"
    o = ov.set("p004", size="spread")
    assert o.size == "spread" and not o.compose
    ov.path = Path(tempfile.mkdtemp()) / "overrides.yaml"
    ov.save()
    assert OV.Overrides.load(ov.path.parent).get("p004").size == "spread", "spread did not round-trip"
    assert "spread" in OV.SIZES

    # a low-res photograph is refused the spread: p016 is 700 px wide
    g = R.geometry_for(R.load_lab(ROOT.parent / "phase0" / "labs" / "saal.yaml"), "28x21")
    small = PH.Photo(id="s", path=None, width=1100, height=800)
    big = PH.Photo(id="b", path=None, width=6000, height=2400)
    PH.size_capability([small, big], g, 200, 150)
    assert small.can["spread"] == "no" and big.can["spread"] == "ok", (small.can, big.can)
    print("overrides: unplanned full-page photograph inserted, two-page spread, low-res refused: ok")


def main() -> int:
    test_run_classification()
    test_openai_reply_shapes()
    test_unplanned_and_spread_overrides()
    test_core_loop()
    print("\nALL CHECKS PASSED")
    return 0


def test_core_loop() -> None:
    """Plan, revise, roll back, cluster, annotate, overrides, cover, providers."""
    ensure_fixtures()
    lab = R.load_lab(ROOT.parent / "phase0" / "labs" / "saal.yaml")
    g = R.geometry_for(lab, "21x21")
    ph = PH.load(PHOTO_DIR, CAPTIONS, g, lab["resolution"]["warn_below_dpi"])
    assert ph, "no photos — run phase0/tools/make_placeholder_photos.py first"
    assert any(p.unusable for p in ph), "expected p013 to be flagged unusable"
    print(f"loaded {len(ph)} photos, {sum(p.unusable for p in ph)} unusable")

    if ALBUM.exists():
        for f in sorted(ALBUM.rglob("*"), reverse=True):
            f.unlink() if f.is_file() else f.rmdir()
    store = Store(ALBUM)

    # 1. plan: first answer invalid, second good, wrapped in a stray fence.
    complete = scripted(json.dumps(bad_layout()),
                        "```json\n" + json.dumps(good_layout()) + "\n```")
    res = planner.plan(title="Sardinien 2026", subtitle="Zehn Tage am Meer", fmt="21x21",
                       language="de", photos=ph, g=g, lab=lab, complete=complete,
                       target_spreads=12, thumb_dir=None)
    assert res.usage.attempts == 2, "retry did not happen"
    assert len(res.layout.spreads) == 12
    v1 = store.add(res.layout.model_dump(mode="json"), parent=None, change_summary="initial plan")
    print(f"plan ok after {res.usage.attempts} attempts -> v{v1.version_no:03d}")

    # 2. revise
    revised = good_layout(summary="Kapitelwechsel nach vorne gezogen, Seite 4 entlastet")
    res2 = planner.revise(current=store.get()[1], comment="Seite 4 ist überladen",
                          photos=ph, g=g, lab=lab, language="de",
                          complete=scripted(json.dumps(revised)))
    v2 = store.add(res2.layout.model_dump(mode="json"), parent=v1.version_no,
                   user_comment="Seite 4 ist überladen",
                   change_summary=res2.layout.change_summary)
    assert v2.parent_version_id == v1.version_no
    print(f"revise ok -> v{v2.version_no:03d}: {res2.layout.change_summary}")

    # 3. rollback appends rather than rewrites
    v3 = store.rollback(1)
    assert v3.version_no == 3 and len(store.versions()) == 3
    print(f"rollback ok -> v{v3.version_no:03d}")
    print("\n" + store.history())

    # 4. clustering: synthetic times and places, since the placeholders carry no EXIF
    import cluster
    from datetime import datetime, timedelta
    import copy
    synth = copy.deepcopy(ph)
    t0 = datetime(2026, 6, 1, 9, 0)
    for i, p in enumerate(synth):
        p.taken_at = t0 + timedelta(hours=9 * (i // 5), minutes=7 * (i % 5))
        p.gps = (39.14 + 0.02 * (i // 5), 9.51)
    groups = cluster.assign(synth)
    assert len(groups) == 3, f"expected 3 moment groups, got {len(groups)}"
    print(f"\nclustering: {len(groups)} groups")
    print(cluster.summary(groups))

    # 5. a spread mixing two groups must be flagged
    from validate import check as vcheck, load_photos as vload
    from schema import Layout as L2
    by_photo = {pid: grp.id for grp in groups for pid in grp.photo_ids}
    mixed = good_layout()
    mixed["spreads"][2]["slots"] = [
        {"photo_id": synth[0].id, "focal": [0.5, 0.5], "caption": None, "caption_position": "none"},
        {"photo_id": synth[-1].id, "focal": [0.5, 0.5], "caption": None, "caption_position": "none"},
    ]
    rep_mixed = vcheck(L2.model_validate(mixed), vload(PHOTO_DIR), g, lab, by_photo)
    assert any(i.code == "cohesion" for i in rep_mixed.issues), "cohesion check did not fire"
    print("cohesion check fires on a spread mixing groups")

    # 6. a photograph across the fold must be rejected outright
    rep_fold = vcheck(L2.model_validate(fold_crossing_layout()), vload(PHOTO_DIR), g, lab)
    assert any(i.code == "fold-crossing" and i.level == "error" for i in rep_fold.issues), \
        "fold-crossing was not rejected"
    assert rep_fold.blocking
    print("fold-crossing layout rejected")

    # 7. annotate: scripted vision pass, cached and reused
    import annotate
    thumbs = ALBUM / "thumbs"
    thumbs.mkdir(parents=True, exist_ok=True)
    from PIL import Image
    for p in ph[:8]:
        with Image.open(p.path) as im:
            im = R.to_srgb(im)
            im.thumbnail((256, 256), Image.LANCZOS)
            im.save(thumbs / f"{p.id}.jpg", "JPEG", quality=70)
    fake = json.dumps([
        {"id": p.id, "subject": "boats at the harbour", "people": 2,
         "who": "two children", "scene": "seascape", "focal": [0.4, 0.45], "strength": 4}
        for p in ph[:8]
    ])
    anns, ausage = annotate.run(ph[:8], thumbs, ALBUM / "annotations.json",
                                scripted(fake), force=True)
    assert len(anns.by_id) == 8
    annotate.apply(ph, anns)
    assert "two children" in ph[0].prompt_line()
    reloaded = annotate.Annotations.load(ALBUM / "annotations.json")
    assert len(reloaded.missing(ph[:8])) == 0, "cache not reused"
    print(f"annotate ok: {len(anns.by_id)} cached, {ausage.line()}")

    # 7b. a flaky or dead vision endpoint must not fail the build
    from planner import TransientError
    slept: list = []
    saved_sleep, annotate._sleep = annotate._sleep, slept.append
    try:
        calls = {"n": 0}

        def flaky(system, messages, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise TransientError("503 busy")
            return fake, Usage(1, 1, 1, 0)
        anns2, _ = annotate.run(ph[:8], thumbs, ALBUM / "annotations-flaky.json", flaky, force=True)
        assert len(anns2.by_id) == 8 and slept == [2.0], (len(anns2.by_id), slept)

        dead_calls = {"n": 0}

        def dead(system, messages, **kw):
            dead_calls["n"] += 1
            raise TransientError("connection refused")
        anns3, _ = annotate.run(ph[:8], thumbs, ALBUM / "annotations-dead.json", dead, force=True)
        assert len(anns3.by_id) == 0
        assert dead_calls["n"] == annotate.ANNOTATE_TRANSIENT, dead_calls
    finally:
        annotate._sleep = saved_sleep
    print("annotate survives a flaky and a dead endpoint: ok")

    # 8. the model's layout must survive the real validator and renderer
    from validate import check, load_photos
    from schema import Layout
    layout = Layout.model_validate(store.get(2)[1])
    rep = check(layout, load_photos(PHOTO_DIR), g, lab)
    print()
    rep.print_report()
    assert not rep.blocking, "planned layout failed print validation"

    tmp = ALBUM / "layout-v002.json"
    tmp.write_text(json.dumps(store.get(2)[1], ensure_ascii=False, indent=2), encoding="utf-8")
    argv = ["render.py", "proof", "--layout", str(tmp), "--photos", str(PHOTO_DIR),
            "--out", str(ALBUM / "renders"), "--format", "21x21"]
    saved, sys.argv = sys.argv, argv
    try:
        rc = R.main()
    finally:
        sys.argv = saved
    assert rc == 0, "proof render failed"
    # 9. the offline capabilities: geocoding, overrides, location stamping
    import geocode, overrides as OV
    alb = ALBUM / "ovtest"
    alb.mkdir(parents=True, exist_ok=True)
    (alb / "places.yaml").write_text(
        "- name: Testbucht\n  lat: 40.1150\n  lon: 9.6900\n  radius_m: 3000\n", encoding="utf-8")
    demo = ROOT / "demo_photos"
    if demo.exists():
        dph = PH.load(demo, ROOT / "examples" / "demo_captions.yaml", g, 200, 150)
        named = geocode.apply(dph, alb)
        assert named, "no photo was located"
        assert any(p.place == "Testbucht" for p in dph), "places.yaml did not win over the database"
        dg = cluster.assign(dph)
        assert all(x.place for x in dg), "a group has no place"
        assert all(x.date_label for x in dg), "a group has no date"
        print(f"\ngeocoding: {named} located, {len(dg)} groups, places {[x.place for x in dg]}")

        small = next(p for p in dph if p.id == "p016")
        assert small.can["full-page"] == "no" and small.can["small"] == "ok", \
            "size capability not computed"
        print(f"size guard: p016 full-page={small.can['full-page']} small={small.can['small']}")

    ov = OV.Overrides(path=alb / "overrides.yaml")
    ov.set("p009", size="full-page"); ov.set("p010", exclude=True)
    ov.set("p008", treatment="frame", description="Eine Tür")
    ov.save()
    assert OV.Overrides.load(alb).get("p009").size == "full-page", "overrides did not round-trip"

    lay = good_layout()
    before = len(lay["spreads"])
    notes = OV.apply_to_layout(lay, ov)
    assert notes, "overrides produced no changes"
    placed = [s["photo_id"] for sp in lay["spreads"] for s in sp.get("slots", [])]
    assert "p010" not in placed, "excluded photo still present"
    full = [sp for sp in lay["spreads"]
            if sp["template"] == "full-bleed-single"
            and any(s["photo_id"] == "p009" for s in sp.get("slots", []))]
    assert full, "full-page override did not create its own spread"
    assert len(lay["spreads"]) >= before, "spread lost"
    treated = [s for sp in lay["spreads"] for s in sp.get("slots", [])
               if s["photo_id"] == "p008"]
    assert treated and treated[0]["treatment"] == "frame", "treatment not applied"
    assert treated[0]["caption"] == "Eine Tür", "description did not become the caption"
    print(f"overrides: {len(notes)} change(s) applied -> {'; '.join(notes)}")

    stamped = OV.stamp_locations(lay, groups, {})
    print(f"location stamping: {stamped} chapter opening(s)")

    # 10. the cover must actually render, not come out blank
    cov = Layout.model_validate(store.get(2)[1]).cover
    assert cov and cov.front_photo_id, "planner output carried no cover"
    argv = ["render.py", "cover", "--layout", str(tmp), "--photos", str(PHOTO_DIR),
            "--out", str(ALBUM / "renders"), "--format", "21x21"]
    saved, sys.argv = sys.argv, argv
    try:
        rc = R.main()
    finally:
        sys.argv = saved
    cover_pdf = ALBUM / "renders" / "cover.pdf"
    assert rc == 0 and cover_pdf.exists(), "cover render failed"
    assert cover_pdf.stat().st_size > 50_000, (
        f"cover is only {cover_pdf.stat().st_size} bytes — almost certainly blank")
    print(f"cover: {cover_pdf.stat().st_size / 1e6:.1f} MB")

    # 11. the provider layer: OpenAI-protocol translation must not lose images
    import providers as PR
    pr = PR.resolve(base_url="http://127.0.0.1:1/v1", model="m", api_key="k")
    assert pr.name == "openai", "a base-url did not imply the openai protocol"
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "id: p001"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "AAAA"}},
    ]}]
    oai = PR._to_openai("SYS", msgs)
    assert oai[0]["role"] == "system" and oai[0]["content"] == "SYS"
    img = [b for b in oai[1]["content"] if b["type"] == "image_url"]
    assert img and img[0]["image_url"]["url"].startswith("data:image/jpeg;base64,"), \
        "image block was not translated to a data URI"
    assert PR.count_images(msgs) == 1
    stripped = PR._strip_images(msgs)
    assert PR.count_images(stripped) == 0 and stripped[0]["content"][0]["text"] == "id: p001", \
        "stripping images lost the text"
    assert PR.resolve(base_url="ollama").is_local, "a localhost endpoint was not treated as local"
    assert PR.resolve(base_url="ollama").usable, "a local endpoint should need no key"
    print(f"providers: openai translation ok, {len(PR.ALIASES)} endpoint shorthands")

    # 12. role routing: each call type can land on a different endpoint
    cfgf = ALBUM / "models.yaml"
    cfgf.write_text(
        "default:\n  base_url: http://remote.invalid/v1\n  model: planner\n"
        "  api_key: k\nannotate:\n  base_url: ollama\n  model: visionmodel\n", encoding="utf-8")
    roles = PR.resolve_all(config_path=cfgf)
    assert roles["annotate"].model == "visionmodel", "annotate did not take its override"
    assert roles["plan"].model == "planner", "plan did not inherit the default"
    assert roles["revise"].model == "planner", "revise did not inherit the default"
    assert roles["annotate"].vision, "annotate must keep vision"
    assert not roles["plan"].vision and not roles["revise"].vision, \
        "plan/revise must never request vision — they send no images"
    assert roles["annotate"].usable, "a local annotate endpoint needs no key"
    assert "more" in PR.describe_roles(roles) or "\n" in PR.describe_roles(roles), \
        "a split setup should not collapse to one line"
    one = PR.resolve_all(config_path=None)
    assert "\n" not in PR.describe_roles(one), "a single model should collapse to one line"
    print(f"roles: annotate={roles['annotate'].model} plan={roles['plan'].model} "
          f"revise={roles['revise'].model}")

    # 13. vision copies must actually be smaller, and cost must scale with area
    demo = ROOT / "demo_photos"
    if demo.exists():
        dph = PH.load(demo, None, g, 200, 150)[:4]
        big = annotate.build_vision_copies(dph, ALBUM / "v1200", 1200)
        small = annotate.build_vision_copies(dph, ALBUM / "v512", 512)
        tb = annotate.estimate_tokens(dph, big)
        ts = annotate.estimate_tokens(dph, small)
        assert ts * 3 < tb, f"512px should cost far less than 1200px, got {ts} vs {tb}"
        from PIL import Image
        with Image.open(small / f"{dph[0].id}.jpg") as im:
            assert max(im.size) <= 512, "vision copy was not resized"
            assert "icc_profile" not in im.info, "ICC profile not stripped from vision copy"
        print(f"vision copies: 1200px {tb:,} tok -> 512px {ts:,} tok "
              f"({tb / max(ts, 1):.1f}x cheaper)")

    # 14. HEIC: the format most phones actually produce
    import imaging
    assert ".heic" in imaging.EXTENSIONS and ".heif" in imaging.EXTENSIONS
    from validate import load_photos as _lp
    import inspect
    # the loader and the validator must agree on what counts as a photograph,
    # or HEIC files load and then report as missing
    assert "imaging.EXTENSIONS" in inspect.getsource(_lp), \
        "validate.load_photos does not use the shared extension list"
    assert "imaging.EXTENSIONS" in inspect.getsource(PH.read_dir), \
        "photos.read_dir does not use the shared extension list"
    if imaging.heif_available():
        import piexif
        from PIL import Image as _I
        hd = ALBUM / "heic"
        hd.mkdir(parents=True, exist_ok=True)
        src = sorted(PHOTO_DIR.glob("*.jpg"))[:2]
        for k, f in enumerate(src):
            ex = {"0th": {}, "Exif": {piexif.ExifIFD.DateTimeOriginal:
                                      b"2026:08:05 08:53:00"},
                  "GPS": {piexif.GPSIFD.GPSLatitudeRef: b"N",
                          piexif.GPSIFD.GPSLatitude: ((46, 1), (29, 1), (3000, 100)),
                          piexif.GPSIFD.GPSLongitudeRef: b"E",
                          piexif.GPSIFD.GPSLongitude: ((9, 1), (50, 1), (2000, 100))},
                  "1st": {}, "thumbnail": None}
            # uppercase extension, as a phone writes it
            _I.open(f).convert("RGB").save(hd / f"IMG_{k}.HEIC", "HEIF",
                                           quality=70, exif=piexif.dump(ex))
        hph = PH.load(hd, None, g, 200, 150)
        assert len(hph) == 2, f"HEIC files not read: {len(hph)}"
        assert all(p.taken_at for p in hph), "HEIC EXIF timestamp not read"
        assert all(p.gps for p in hph), "HEIC GPS not read"
        assert len(_lp(hd)) == 2, "validator did not see the HEIC files"
        print(f"heic: {len(hph)} read with EXIF and GPS, validator agrees")
    else:
        print("heic: pillow-heif not installed, skipped")

    # 15. the structured-output schema must survive provider simplification
    import providers as PR2
    sch = PR2._layout_schema()
    import json as _json
    txt = _json.dumps(sch)
    assert "prefixItems" not in txt, "prefixItems survives; Vertex rejects it"
    assert "$ref" not in txt and "$defs" not in txt, "refs not inlined"
    # 'title' is both a JSON Schema annotation and a field of meta; stripping
    # it as a keyword once deleted the album title from the schema entirely
    assert "title" in sch["properties"]["meta"]["properties"], \
        "meta.title was stripped as if it were a schema annotation"
    focal = (sch["properties"]["spreads"]["items"]["properties"]["slots"]
             ["items"]["properties"]["focal"])
    assert focal.get("type") == "array" and focal.get("items", {}).get("type") == "number", \
        "focal lost its shape when prefixItems was rewritten"
    # Anthropic's structured output rejects value constraints, minItems above
    # 1 and any additionalProperties other than false. Pydantic still checks
    # all of these after decoding, so the schema only steers.
    def _walk(n):
        if isinstance(n, dict):
            for k, v in n.items():
                if k == "properties":
                    for sub in v.values():
                        yield from _walk(sub)
                    continue
                yield k, v
                yield from _walk(v)
        elif isinstance(n, list):
            for x in n:
                yield from _walk(x)
    for k, v in _walk(sch):
        assert k not in ("minimum", "maximum", "maxItems", "minLength", "maxLength"), \
            f"{k} survives; Anthropic rejects it"
        assert not (k == "minItems" and v > 1), "minItems above 1 survives"
        assert not (k == "additionalProperties" and v is not False), \
            "open additionalProperties survives; Anthropic only accepts false"
    # Anthropic's grammar compiler also caps optional properties at 24.
    def _count(n):
        n = 0
        if isinstance(n, dict):
            if isinstance(n.get("properties"), dict):
                n += len(set(n["properties"]) - set(n.get("required", [])))
                for sub in n["properties"].values():
                    n += _count(sub)
            for k, v in n.items():
                if k != "properties":
                    n += _count(v)
        elif isinstance(n, list):
            for x in n:
                n += _count(x)
        return n
    optional = _count(sch)
    assert optional <= 24, f"{optional} optional properties; Anthropic allows 24"
    opts = sch["properties"]["spreads"]["items"]["properties"]["options"]
    assert "side" in opts["properties"], "options closed without declaring side"
    try:
        import jsonschema

        def as_written(obj, node):
            """What a model writes under the schema: optional fields that are
            null are left out (the schema keeps only 16 unions)."""
            if isinstance(node, dict) and "anyOf" in node and isinstance(obj, (dict, list)):
                node = next((a for a in node["anyOf"] if a.get("type") != "null"), {})
            if isinstance(obj, dict) and isinstance(node, dict):
                props = node.get("properties") or {}
                req = set(node.get("required", []))
                return {k: as_written(v, props.get(k, {})) for k, v in obj.items()
                        if not (v is None and k not in req)}
            if isinstance(obj, list) and isinstance(node, dict):
                return [as_written(x, node.get("items", {})) for x in obj]
            return obj

        jsonschema.validate(as_written(good_layout(), sch), sch)
        print("schema: simplified form accepts a real layout, no prefixItems or refs")
    except ImportError:
        print("schema: simplified, no prefixItems or refs (jsonschema not installed)")


if __name__ == "__main__":
    sys.exit(main())
