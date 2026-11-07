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
        {"index": 1, "section_id": "s1", "template": "text-page", "slots": [],
         "text": {"eyebrow": "Sardinien · 2026", "title": "Sardinien 2026",
                  "subtitle": "Zehn Tage am Meer", "body": "Zehn Tage, ein Auto, kein Plan."},
         "options": {"side": "right"}, "note": "title page"},
        {"index": 2, "section_id": "s1", "template": "full-bleed-single",
         "slots": [{"photo_id": "p001", "focal": [0.72, 0.42],
                    "caption": None, "caption_position": "none"}],
         "options": {"side": "right"}, "note": "opening image, sets the tone"},
        {"index": 3, "section_id": "s1", "template": "two-up-equal",
         "slots": [{"photo_id": "p002", "focal": [0.5, 0.35], "caption": "Morgens am Hafen", "caption_position": "below"},
                   {"photo_id": "p003", "focal": [0.5, 0.4], "caption": "Die Gasse hinter der Kirche", "caption_position": "below"}],
         "note": "two portraits, same weight"},
        {"index": 4, "section_id": "s1", "template": "single-centered",
         "slots": [{"photo_id": "p005", "focal": [0.5, 0.5], "caption": "Der alte Feigenbaum", "caption_position": "below"}],
         "options": {"side": "right"}, "note": "the book breathes"},
        {"index": 5, "section_id": "s2", "template": "section-divider", "slots": [],
         "text": {"eyebrow": "Kapitel zwei", "title": "Der Wind", "body": "Vier Tage Nordwest."},
         "note": "chapter break"},
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
         "options": {"side": "left"}, "note": "closing text"},
    ][:spread_count]
    return {
        "meta": {"title": "Sardinien 2026", "subtitle": "Zehn Tage am Meer",
                 "language": "de", "format": "21x21", "spread_count": len(spreads)},
        "sections": [{"id": "s1", "title": "Ankunft", "intro_text": None},
                     {"id": "s2", "title": "Der Wind", "intro_text": None}],
        "spreads": spreads,
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


def main() -> int:
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
    print("\nALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
