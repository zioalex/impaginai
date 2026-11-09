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
ALBUM = Path("/tmp/album_test")


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
        subprocess.run([sys.executable, str(gen)], check=True, capture_output=True)
    demo = ROOT / "demo_photos"
    if not demo.exists() or not any(demo.glob("*.jpg")):
        gen = ROOT / "tools" / "make_demo_photos.py"
        if gen.exists():
            print(f"generating fixtures: {gen.name}")
            try:
                subprocess.run([sys.executable, str(gen)], check=True, capture_output=True)
            except subprocess.CalledProcessError:
                print("  (skipped: needs piexif)")


def main() -> int:
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

    # 8. the model's layout must survive the real validator and renderer
    from validate import check, load_photos
    from schema import Layout
    layout = Layout.model_validate(store.get(2)[1])
    rep = check(layout, load_photos(PHOTO_DIR), g, lab)
    print()
    rep.print_report()
    assert not rep.blocking, "planned layout failed print validation"

    tmp = ALBUM / "layout-v002.json"
    tmp.write_text(json.dumps(store.get(2)[1], ensure_ascii=False, indent=2))
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
        "- name: Testbucht\n  lat: 40.1150\n  lon: 9.6900\n  radius_m: 3000\n")
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

    print("\nALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
