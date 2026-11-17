#!/usr/bin/env python3
"""The page repacker and the crop guard, offline.

    python tests/test_pages.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace as NS

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT.parent / "phase0"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import render as R  # noqa: E402
import templates_lib as T  # noqa: E402
import compose  # noqa: E402
from geometry import Rect, fit_rect  # noqa: E402
from schema import Layout  # noqa: E402

import pages as PG  # noqa: E402
from test_loop import good_layout  # noqa: E402

LAB = R.load_lab(ROOT.parent / "phase0" / "labs" / "saal.yaml")


def blank_pages(lay: dict) -> int:
    n = 0
    for sp in lay["spreads"]:
        spec = T.REGISTRY[sp["template"]]
        if spec.pages:
            n += sum(1 for c in spec.pages if compose.COMPOSITIONS[c].slots == 0)
        elif spec.slot_count == 0:
            n += 2
    return n


def _groups() -> list:
    return [
        NS(id="g1", photo_ids=["p001", "p015", "p005", "p002", "p003"], place="Cala Gonone", date_label="3. Mai"),
        NS(id="g2", photo_ids=["p006", "p008", "p009", "p010", "p004"], place="Orosei", date_label="5. Mai"),
        NS(id="g3", photo_ids=["p011", "p012", "p014", "p007"], place="Nuoro", date_label="7. Mai"),
    ]


def repack_and_crop_guard(groups):
    # 1. a book with photographs to spare: no empty page survives, no
    #    divider page survives past the opening, every location is stamped.
    lay = good_layout()
    lay["spreads"] = [lay["spreads"][0]] + lay["spreads"][1:] * 3   # plenty of pages
    for i, sp in enumerate(lay["spreads"], 1):
        sp["index"] = i
    lay["meta"]["spread_count"] = len(lay["spreads"])
    placed_before = sorted(s["photo_id"] for sp in lay["spreads"] for s in sp["slots"])
    PG.repack(lay, groups, LAB)
    Layout.model_validate(lay)
    placed_after = sorted(s["photo_id"] for sp in lay["spreads"] for s in sp["slots"])
    assert placed_before == placed_after, "a photograph was lost or duplicated"
    assert lay["spreads"][0]["template"].startswith("front:"), "the opening is not a front page"
    assert lay["spreads"][0]["slots"][0]["photo_id"] == "p001", "the opening's own photograph was not kept"
    assert lay["spreads"][0]["text"]["title"] == lay["meta"]["title"]
    assert lay["spreads"][0]["text"]["eyebrow"] == "Cala Gonone · 3. Mai"
    later = [sp["template"] for sp in lay["spreads"][1:]]
    assert not set(later) & set(PG.OPENING), f"text-only page after the opening: {later}"
    assert blank_pages(lay) <= 1, f"{blank_pages(lay)} empty pages left"
    stamps = [v for sp in lay["spreads"] for v in (sp.get("options") or {}).get("stamps", {}).values()]
    assert stamps == ["Orosei · 5. Mai", "Nuoro · 7. Mai"], stamps
    assert len(lay["spreads"]) * 2 >= LAB["pages"]["min"]
    print(f"repack: {len(lay['spreads'])} spreads, {blank_pages(lay)} empty page(s), stamps {stamps}")

    # 2. a short book still reaches the lab minimum, by splitting composed pages first.
    lay = good_layout()
    PG.repack(lay, groups, LAB)
    Layout.model_validate(lay)
    assert len(lay["spreads"]) * 2 >= LAB["pages"]["min"], "short book below the lab minimum"
    print(f"short book: {len(lay['spreads']) * 2} pages")

    # 3. the crop guard: a 4:3 photograph in a square slot is fitted, not cut.
    sq = Rect(0, 0, 100, 100)
    assert fit_rect(sq, 4 / 3) == Rect(0, 12.5, 100, 75), fit_rect(sq, 4 / 3)
    assert fit_rect(sq, 3 / 4) == Rect(12.5, 0, 75, 100)
    assert fit_rect(sq, 1.1) is sq, "a mild crop should still fill the slot"
    print("crop guard: ok")


# One entry per section, so pytest reports each on its own and a failure in
# one no longer hides the rest. `python tests/test_pages.py` runs them in order.
SECTIONS = ("repack_and_crop_guard", "adaptive_pages", "fuller_pages", "compositions_survive",
            "subjects_without_a_box", "user_spreads", "gutter_and_bleed", "costs_and_places",
            "precise_places", "engadin_run_fixes")


def _run(name: str) -> None:
    import inspect

    fn = globals()[name]
    fn(_groups()) if inspect.signature(fn).parameters else fn()


def main() -> int:
    test_loop_ensure()
    for name in SECTIONS:
        _run(name)
    return 0


def test_portable_dates_and_text():
    """Two things that worked on Linux and failed on Windows: a date format
    code only glibc has, and text files written in whatever the system's
    encoding happened to be."""
    import tempfile
    from datetime import datetime

    import cluster
    import overrides as OV
    import versions

    day = lambda *a: NS(taken_at=datetime(*a))
    assert cluster.date_range([day(2026, 8, 5, 9)]) == "5 August 2026"
    assert cluster.date_range([day(2026, 8, 5, 9), day(2026, 8, 9, 9)]) == "5–9 August 2026"
    assert cluster.date_range([day(2026, 7, 30, 9), day(2026, 8, 2, 9)]) == "30 Jul – 2 Aug 2026"
    words = "Zürich · Lej da Segl → 山 “quoted”"
    with tempfile.TemporaryDirectory() as td:
        store = versions.Store(Path(td))
        store.add({"meta": {"title": words}}, parent=None, user_comment=words)
        assert store.versions()[0].user_comment == words
        assert store.get()[1]["meta"]["title"] == words
        assert words.encode("utf-8") in (Path(td) / "versions.json").read_bytes(), \
            "state files must be UTF-8 on every system"
        ov = OV.Overrides.load(Path(td))
        ov.set("p001", description=words)
        ov.save()
        assert OV.Overrides.load(Path(td)).get("p001").description == words


def test_repack_and_crop_guard(): _run("repack_and_crop_guard")
def test_adaptive_pages(): _run("adaptive_pages")
def test_fuller_pages(): _run("fuller_pages")
def test_compositions_survive(): _run("compositions_survive")
def test_subjects_without_a_box(): _run("subjects_without_a_box")
def test_user_spreads(): _run("user_spreads")
def test_gutter_and_bleed(): _run("gutter_and_bleed")
def test_costs_and_places(): _run("costs_and_places")
def test_precise_places(): _run("precise_places")
def test_settings_keys_places_and_ids(): _run("engadin_run_fixes")


def engadin_run_fixes(groups):
    """What broke on the first real album: pixel focal points from the vision
    model, a schema over Anthropic's union limit, the place lookup retried on
    every reload, a short book padded with blank paper while photographs sat
    unused, and an empty front page."""
    import annotate
    import geocode
    import providers

    # focal points in pixels are turned into fractions, and never reach a layout
    assert annotate.normalise_focal([180, 140], (512, 341)) == (round(180 / 512, 4), round(140 / 341, 4))
    assert annotate.normalise_focal([0.4, 0.6], (512, 341)) == (0.4, 0.6)
    assert annotate.normalise_focal("x", None) == (0.5, 0.5)
    ph = NS(id="p1", width=4000, height=3000, annotation="", strength=0, suggested_focal=None,
            landmark="", detail="")
    anns = annotate.Annotations({"p1": annotate.Annotation(id="p1", focal=(260.0, 140.0))})
    annotate.apply([ph], anns)
    fx, fy = ph.suggested_focal
    assert 0 <= fx <= 1 and 0 <= fy <= 1, ph.suggested_focal
    lay = {"spreads": [{"slots": [{"photo_id": "p1", "focal": [0.3, 0.3]}]}]}
    PG.apply_focal(lay, {"p1": NS(suggested_focal=(260.0, 140.0))})
    assert lay["spreads"][0]["slots"][0]["focal"] == [0.3, 0.3], "an out-of-range focal replaced a good one"
    print("focal points normalised: ok")

    # Anthropic refuses more than 16 union-typed parameters
    n = providers.union_count(providers._layout_schema())
    assert n <= 16, f"{n} union-typed parameters in the layout schema"
    print(f"layout schema: {n} union-typed parameters (limit 16): ok")

    # the place lookup fails once and then stays quiet for the cooldown
    import tempfile
    calls = []

    def down(lat, lon, lang):
        calls.append(1)
        raise TimeoutError("The read operation timed out")

    geocode._DOWN_UNTIL = 0.0
    with tempfile.TemporaryDirectory() as td:
        pts = [NS(id=str(i), gps=(46.4 + i * 0.05, 9.9), place=None, features=[]) for i in range(4)]
        geocode.apply(pts, Path(td), offline_db=False, online=True, fetch=down)
        geocode.apply(pts, Path(td), offline_db=False, online=True, fetch=down)
        assert len(calls) == 1, f"the lookup was retried {len(calls)} times during its cooldown"
        # After the cooldown the place that failed is not asked first again:
        # one slow place used to block every other one, every time.
        asked = []

        def slow_first(lat, lon, lang):
            asked.append(lat)
            if lat == 46.4:
                raise TimeoutError("The read operation timed out")
            return [{"name": "Lej Nair", "kind": "lake", "lat": lat, "lon": lon}]

        geocode._DOWN_UNTIL = 0.0
        geocode.apply(pts, Path(td), offline_db=False, online=True, fetch=slow_first)
        assert 46.4 not in asked and len(asked) == 3, asked
        assert pts[1].features, "the other places are named while one is backing off"
    geocode._DOWN_UNTIL = 0.0
    geocode._CELL_RETRY.clear()

    # --- landmark names from the list kept on this machine -----------------
    import gazetteer
    import json
    import os
    import time

    def row(name, code, lat, lon, cc="CH"):
        return "\t".join(["1", name, name, "", str(lat), str(lon), "T", code, cc] + [""] * 10)

    CH = [row("Vadret da Morteratsch", "GLCR", 46.405, 9.935), row("Munt Pers", "PK", 46.42, 9.955),
          row("Lej Nair", "LK", 46.412, 9.99), row("Berninapass", "PASS", 46.4103, 10.0222),
          row("Pontresina", "PPL", 46.4955, 9.9016), row("Hotel Morteratsch", "HTL", 46.435, 9.94),
          row("far away lake", "LK", 47.3, 8.5), "broken line"]
    IT = [row("Lago Bianco Sud", "LK", 46.39, 10.03, "IT")]
    rows = gazetteer.parse(CH)
    assert [r[0] for r in rows] == ["Vadret da Morteratsch", "Munt Pers", "Lej Nair",
                                    "Berninapass", "far away lake"], rows
    assert {r[1] for r in rows} == {"glacier", "peak", "lake", "mountain_pass"}
    saved_cfg, saved_cf = os.environ.get("ALBUM_CONFIG_DIR"), gazetteer.countries_for
    geocode._OSM_SAID = False
    try:
        with tempfile.TemporaryDirectory() as cfg, tempfile.TemporaryDirectory() as td:
            os.environ["ALBUM_CONFIG_DIR"] = cfg
            gazetteer.reset()
            got = []

            def fake_dl(cc):
                got.append(cc)
                if cc == "FR":
                    raise TimeoutError("The read operation timed out")
                return gazetteer.parse({"CH": CH, "IT": IT}[cc])

            def never(lat, lon, lang):
                raise AssertionError("near() and a cache-only apply() must not touch the network")

            # a photograph on the glacier, close to the Italian border
            gazetteer.countries_for = lambda pts, reach_m=2500.0: {"CH", "IT"}
            pt = [NS(id="a", gps=(46.41, 9.94), place=None, features=[]),
                  NS(id="b", gps=(46.392, 10.028), place=None, features=[])]
            assert geocode.prefetch_gazetteer(pt, fetch=fake_dl) is True
            assert sorted(got) == ["CH", "IT"] and (Path(cfg) / "gazetteer" / "CH.json").exists()
            assert geocode.prefetch_gazetteer(pt, fetch=fake_dl) is False and len(got) == 2, \
                "a country already on this machine was downloaded again"
            geocode.apply(pt, Path(td), offline_db=False, online=False, fetch=never)
            assert pt[0].place == "Vadret da Morteratsch", pt[0].place
            assert "Vadret da Morteratsch (glacier" in pt[0].features[0], pt[0].features
            assert not any("far away" in f or "Hotel" in f for f in pt[0].features)
            assert pt[1].place == "Lago Bianco Sud", "a landmark across the border was not found"
            # a new process: read from disk, no download
            gazetteer.reset()
            assert geocode.prefetch_gazetteer(pt, fetch=never) is True
            # the option switched off leaves landmark names out
            pt[0].place = None
            geocode.apply(pt, Path(td), offline_db=False, online=False, landmarks=False)
            assert pt[0].place is None and pt[0].features == []
            # OpenStreetMap's answer for the same glacier replaces the list's, others are added
            (Path(td) / "features_cache.json").write_text(json.dumps({"46.41,9.94": [
                {"name": "Morteratschgletscher", "kind": "glacier", "lat": 46.4052, "lon": 9.9352},
                {"name": "Aussichtspunkt", "kind": "viewpoint", "lat": 46.411, "lon": 9.941}]}), encoding="utf-8")
            geocode.apply(pt, Path(td), offline_db=False, online=False, fetch=never)
            names = [f.split(" (")[0] for f in pt[0].features]
            assert names[0] == "Morteratschgletscher" and "Vadret da Morteratsch" not in names, names
            assert "Aussichtspunkt" in names and "Munt Pers" in names, names
            # a download that fails: said once, not retried during its pause, nothing breaks
            gazetteer.countries_for = lambda pts, reach_m=2500.0: {"FR"}
            fr = [NS(id="f", gps=(45.9, 6.9), place=None, features=[])]
            assert geocode.prefetch_gazetteer(fr, fetch=fake_dl) is False
            assert geocode.prefetch_gazetteer(fr, fetch=fake_dl) is False
            assert got.count("FR") == 1 and gazetteer.status()["FR"] == "unavailable"
            # a slow download is not waited for beyond the limit
            gazetteer.countries_for = lambda pts, reach_m=2500.0: {"AT"}
            import threading as _th
            release = _th.Event()

            def slow(cc):
                release.wait(10)
                return []

            t0 = time.monotonic()
            assert geocode.prefetch_gazetteer(fr, fetch=slow, wait_s=0.3) is False
            assert time.monotonic() - t0 < 2 and gazetteer.status()["AT"] == "downloading"
            release.set()
            gazetteer._THREADS["AT"].join(5)
            # OpenStreetMap failing says so once and never raises
            calls.clear()
            geocode._DOWN_UNTIL = 0.0
            geocode._CELL_RETRY.clear()
            assert geocode.prefetch_overpass(pt, Path(td) / "x", fetch=down) == 0 and len(calls) == 1
            assert geocode.places_status()["osm"] == "unavailable"
    finally:
        gazetteer.countries_for = saved_cf
        gazetteer.reset()
        geocode._DOWN_UNTIL = 0.0
        geocode._CELL_RETRY.clear()
        if saved_cfg is None:
            os.environ.pop("ALBUM_CONFIG_DIR", None)
        else:
            os.environ["ALBUM_CONFIG_DIR"] = saved_cfg
    # Designing does not wait for OpenStreetMap: it is asked in the background.
    sys.path.insert(0, str(ROOT.parent / "phase2"))
    import server
    saved = (dict(server.STATE), geocode.prefetch_overpass, geocode.prefetch_gazetteer)
    started, release = _th.Event(), _th.Event()

    def hangs(photos, album_dir, language=None, fetch=None):
        started.set()
        release.wait(20)
        return 0

    try:
        server.STATE.update(online_places=True, all=[], album=Path("/nowhere"), language="de")
        geocode.prefetch_overpass, geocode.prefetch_gazetteer = hangs, (lambda ph, wait_s=None, fetch=None: False)
        t0 = time.monotonic()
        server.prefetch_places([], wait_s=20)
        assert time.monotonic() - t0 < 2, "the design waited for OpenStreetMap"
        assert started.wait(5), "OpenStreetMap was not asked in the background"
        server.prefetch_places([], wait_s=20)        # a second one does not pile up
        release.set()
    finally:
        release.set()
        server.STATE.clear()
        server.STATE.update(saved[0])
        geocode.prefetch_overpass, geocode.prefetch_gazetteer = saved[1], saved[2]
    print("landmark names: offline list, across a border, merged with OpenStreetMap, quiet failures: ok")
    print("place lookup cooldown: ok")

    # a short book takes the photographs the design left out before blank paper,
    # and opens on the chosen front page photograph
    lay = good_layout()
    lay["spreads"] = lay["spreads"][:3]           # 3 spreads: far below 24 pages
    for i, sp in enumerate(lay["spreads"], 1):
        sp["index"] = i
    lay["meta"]["spread_count"] = 3
    placed = {s["photo_id"] for sp in lay["spreads"] for s in sp["slots"]}
    every = [pid for g in groups for pid in g.photo_ids]
    spare = [pid for pid in every if pid not in placed]
    log = PG.repack(lay, groups, LAB, spare=spare, front="p006")
    Layout.model_validate(lay)
    opening = lay["spreads"][0]
    assert opening["template"].startswith("front:") and opening["slots"][0]["photo_id"] == "p006", opening
    assert opening["text"]["title"] == lay["meta"]["title"]
    ids = [s["photo_id"] for sp in lay["spreads"] for s in sp["slots"]]
    assert ids.count("p006") == 1, "the front page photograph is printed twice"
    brought = [x for x in spare if x in ids]
    assert brought, f"no spare photograph was used: {log}"
    print(f"short book: {len(brought)} spare photograph(s) used before blank paper, "
          f"front page p006: ok")

    # the user's case: few photographs, one per page, at the lab minimum. The
    # front page is the photograph with the title on it (no page of ink), and
    # a photograph marked "in a composition" never stands alone on a page.
    every = [pid for g in groups for pid in g.photo_ids]
    lay = good_layout()
    lay["spreads"] = [{"index": i + 1, "section_id": None, "template": "two-up-equal",
                       "slots": [{"photo_id": a, "focal": [0.5, 0.5], "caption": None,
                                  "caption_position": "below"} for a in pair],
                       "text": None, "options": {}, "note": None}
                      for i, pair in enumerate(zip(every[0::2], every[1::2]))]
    lay["meta"]["spread_count"] = len(lay["spreads"])
    left_out = every[-1] if len(every) % 2 else None
    want = {"p005", "p009"} | ({left_out} if left_out else set())
    log = PG.repack(lay, groups, LAB, spare=[left_out] if left_out else [],
                    front="p001", together=want)
    Layout.model_validate(lay)
    first = lay["spreads"][0]
    assert first["template"].startswith("front:") and first["template"] != "front:blank", first
    assert first["slots"][0]["photo_id"] == "p001" and len(first["slots"]) > 1
    rend = T.build(first["template"], R.geometry_for(LAB, "28x21"), first["options"])
    assert not rend.background.startswith("ink"), "the front page is printed on black"
    assert any(tb.key == "title" and tb.invert for tb in rend.texts)
    for pid in want:
        hit = None
        for sp in lay["spreads"]:
            spec = T.REGISTRY[sp["template"]]
            a, b = spec.pages
            if (sp.get("options") or {}).get("side") == "right":
                a, b = b, a
            n = compose.COMPOSITIONS[a].slots
            for part in (sp["slots"][:n], sp["slots"][n:]):
                if any(s["photo_id"] == pid for s in part):
                    hit = part
        assert hit is not None, f"{pid} is not in the book: {log}"
        assert len(hit) > 1, f"{pid} is alone on its page: {log}"
    assert len(lay["spreads"]) * 2 >= LAB["pages"]["min"]
    print(f"front page with title on the photograph, {len(want)} photo(s) kept in a "
          f"composition: ok")

    # a front photograph without the pixels to fill the page is set inside
    # the margins with the title beneath, rather than blocking the render
    lay = good_layout()
    PG.repack(lay, groups, LAB, front="p001", no_bleed={"p001"})
    Layout.model_validate(lay)
    first = lay["spreads"][0]
    assert first["template"].startswith("front-framed:"), first["template"]
    rend = T.build(first["template"], R.geometry_for(LAB, "21x21"), first["options"])
    assert not rend.slots[0].bleeds and any(tb.key == "title" for tb in rend.texts)
    PG.repack(lay, groups, LAB, front="p001", no_bleed={"p001"})   # a revision keeps it
    assert lay["spreads"][0]["slots"][0]["photo_id"] == "p001"
    print("low-resolution front page set inside the margins: ok")

    # a photo id the model shortened is read as the real one; one that names
    # no photograph is left out instead of blocking the render
    import overrides as OV
    known = ["IMG-20260805-WA0019", "IMG_20260805_090039_1", "IMG_20260805_085308", "p002"]
    assert OV.match_id("WA0019", known) == "IMG-20260805-WA0019"
    assert OV.match_id("img_20260805_085308", known) == "IMG_20260805_085308"
    assert OV.match_id("IMG_20260805", known) is None, "ambiguous ids must not be guessed"
    wa = known + ["IMG-20260805-WA0018", "IMG-20260805-WA0020"]
    assert OV.match_id("IMG_20260805-WA0019", wa) == "IMG-20260805-WA0019", \
        "a swapped separator is read as the real id even among near-identical neighbours"
    assert OV.match_id("IMG_20260805_WA0021", wa) is None
    lay = {"spreads": [
        {"index": 1, "template": "two-up-hero", "options": {}, "text": None, "section_id": None,
         "slots": [{"photo_id": "p002", "focal": [.5, .5], "caption": None, "caption_position": "below"},
                   {"photo_id": "WA0019", "focal": [.5, .5], "caption": None, "caption_position": "below"}]},
        {"index": 2, "template": "two-up-equal", "options": {}, "text": None, "section_id": None,
         "slots": [{"photo_id": "IMG_20260805_085308", "focal": [.5, .5], "caption": None, "caption_position": "below"},
                   {"photo_id": "nonexistent", "focal": [.5, .5], "caption": None, "caption_position": "below"}]}],
        "meta": {}}
    notes = OV.apply_to_layout(lay, OV.Overrides(), known=known)
    ids = [s["photo_id"] for sp in lay["spreads"] for s in sp["slots"]]
    assert "IMG-20260805-WA0019" in ids and "WA0019" not in ids, ids
    assert "nonexistent" not in ids, ids
    for sp in lay["spreads"]:
        assert T.REGISTRY[sp["template"]].slot_count == len(sp["slots"]), sp
    print(f"mistyped photo ids repaired, unknown ones left out ({len(notes)} note(s)): ok")

    # within one place, pages run in the order the photographs were taken
    g1 = NS(id="g1", photo_ids=["a", "b", "c", "d"], place="Pontresina", date_label="5. August")
    lay = {"meta": {}, "spreads": [
        {"index": 1, "template": "two-up-equal", "options": {}, "text": None, "section_id": None,
         "slots": [{"photo_id": x, "focal": [.5, .5], "caption": None, "caption_position": "below"}
                   for x in pair]} for pair in (("c", "a"), ("d", "b"))]}
    taken = {"a": "2026-08-05T08:53", "b": "2026-08-05T09:00", "c": "2026-08-05T09:36",
             "d": "2026-08-05T10:23"}
    PG.repack(lay, [g1], {"pages": {"min": 0, "increment": 2}}, taken=taken, front="a")
    order = [s["photo_id"] for sp in lay["spreads"] for s in sp["slots"]]
    assert order == ["a", "b", "c", "d"], order
    print("pages of one place in the order taken: ok")

    # a photograph the vision pass saw lying on its side is stood up: the turn
    # goes into rotations.yaml, its shape and focal point turn with it, and a
    # photograph the user turned by hand is left alone
    import imaging
    import annotate
    import photos as PH
    from PIL import Image
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        pdir, adir = td / "photos", td / "album"
        pdir.mkdir()
        Image.new("RGB", (400, 300), "red").save(pdir / "side.jpg")
        Image.new("RGB", (400, 300), "blue").save(pdir / "mine.jpg")
        imaging.set_rotation(pdir, "mine", 180)
        anns = annotate.Annotations({
            "side": annotate.Annotation(id="side", focal=(0.2, 0.3), turn=90, v=annotate.ANNOTATION_VERSION),
            "mine": annotate.Annotation(id="mine", turn=90, v=annotate.ANNOTATION_VERSION)})
        ph = PH.read_dir(pdir)
        turned = annotate.apply_turns(ph, anns, adir / "annotations.json", pdir, adir)
        assert turned == ["side"], turned
        assert imaging.rotations(pdir) == {"side": 90, "mine": 180}, imaging.rotations(pdir)
        again = annotate.Annotations.load(adir / "annotations.json")
        assert again.by_id["side"].turn == 0 and tuple(again.by_id["side"].focal) == (0.7, 0.2)
        side = next(p for p in PH.read_dir(pdir) if p.id == "side")
        assert (side.width, side.height) == (300, 400), (side.width, side.height)
        with Image.open(pdir / "side.jpg") as im:
            assert R.to_srgb(im).size == (300, 400)
    print("sideways photographs stood up from the vision pass: ok")

    # the editor's Settings: option validation and precedence
    sys.path.insert(0, str(ROOT.parent / "phase2"))
    import config as CFG
    vals = CFG.resolve({"density": "dense", "gap_hours": 6, "selection": "nonsense"},
                       {"gap_hours": 2.0})
    assert vals["density"] == "dense" and vals["gap_hours"] == 2.0, vals
    assert vals["selection"] == "best", "an unknown saved value must fall back to the default"
    for bad in (("density", "huge"), ("gap_hours", 99), ("auto_rotate", "maybe")):
        try:
            CFG.BY_KEY[bad[0]].check(bad[1])
            raise AssertionError(f"{bad} accepted")
        except ValueError:
            pass
    rows = {r["key"]: r for r in CFG.describe(vals, {"gap_hours": 2.0}, {"saal": "Saal"})}
    assert rows["gap_hours"]["locked"] and "--gap-hours" in rows["gap_hours"]["locked_by"]
    assert rows["density"]["info"] and rows["selection"]["info"], "density/selection need their pop-up"
    assert CFG.needs(["captions"]) == ["rerender", "redesign"]
    assert CFG.needs(["gap_hours"]) == ["rerender"] and CFG.needs(["vision_px"]) == ["relook"]
    print("settings: validation, precedence, locks and effects: ok")

    # the repacker follows them: every photo, no place names, airy pages
    def singles_layout():
        lay = good_layout()
        every = [pid for g in groups for pid in g.photo_ids]
        lay["spreads"] = [{"index": i + 1, "section_id": None, "template": "two-up-equal",
                           "slots": [{"photo_id": a, "focal": [0.5, 0.5], "caption": None,
                                      "caption_position": "below"} for a in pair],
                           "text": None, "options": {}, "note": None}
                          for i, pair in enumerate(zip(every[0:6:2], every[1:6:2]))]
        return lay, every
    lay, every = singles_layout()
    spare = [x for x in every if x not in {s["photo_id"] for sp in lay["spreads"] for s in sp["slots"]}]
    PG.repack(lay, groups, {"pages": {"min": 0, "increment": 2}}, spare=spare, front=every[0],
              use_all=True, place_names=False)
    ids = {s["photo_id"] for sp in lay["spreads"] for s in sp["slots"]}
    assert set(every) <= ids, f"'every photo' left out {set(every) - ids}"
    assert not any((sp.get("options") or {}).get("stamps") for sp in lay["spreads"]), "place names printed"
    assert not (lay["spreads"][0].get("text") or {}).get("eyebrow")
    lay, every = singles_layout()
    PG.repack(lay, groups, {"pages": {"min": 0, "increment": 2}}, front=every[0], density="airy")
    for sp in lay["spreads"][1:]:
        assert T.REGISTRY[sp["template"]].pages and all(
            compose.COMPOSITIONS[c].slots <= 1 for c in T.REGISTRY[sp["template"]].pages), sp["template"]
    print("repacker follows 'every photo', 'no place names' and 'airy': ok")

    # a photograph added after the design is brought in, even when the book is
    # long enough and the design left it out
    lay, every = singles_layout()
    placed = {s["photo_id"] for sp in lay["spreads"] for s in sp["slots"]}
    newcomer = next(x for x in every if x not in placed)
    PG.repack(lay, groups, {"pages": {"min": 0, "increment": 2}},
              spare=[x for x in every if x not in placed], front=every[0], include={newcomer})
    ids = [s["photo_id"] for sp in lay["spreads"] for s in sp["slots"]]
    assert newcomer in ids and ids.count(newcomer) == 1, ids
    others = set(every) - placed - {newcomer}
    assert not (others & set(ids)), "only the new photograph should be brought in"
    print("photographs added after the design are brought in: ok")

    # never crop the people or the main subject: the keep-box from the vision
    # pass, read in any unit, positions every crop, and a page set to the edges
    # that would cut it holds the photograph inside its margins instead
    import annotate
    nb = annotate.normalise_box
    assert nb([0.1, 0.2, 0.6, 0.9], None) == [0.1, 0.2, 0.6, 0.9]
    assert nb([256, 0, 128, 384], (512, 384)) == [0.25, 0.0, 0.5, 1.0], "pixels, corners swapped"
    assert nb([10, 20, 60, 90], None) == [0.1, 0.2, 0.6, 0.9], "percent"
    assert nb([0, 0, 1, 1], None) is None, "a whole-frame box protects nothing"
    assert nb("nonsense", None) is None and nb([0.5, 0.5, 0.5, 0.9], None) is None
    from geometry import Rect as _R
    tall = _R(0, 0, 100, 150)                       # a portrait slot
    # a wide group (people across 80% of a 3:2 frame) cannot fit a portrait
    # crop: the photograph is shown whole instead of cut
    rect, px, py = R.place_photo(tall, 1.5, (0.5, 0.5), [0.1, 0.2, 0.9, 0.8], False)
    assert rect.w <= tall.w and rect.h < tall.h - 1, rect
    # a subject at the right edge: the crop moves to keep it all, despite the focal
    sq = _R(0, 0, 100, 70)                          # a 1.43 slot, a 1.6 photo: a real crop
    rect, px, py = R.place_photo(sq, 1.6, (0.2, 0.5), [0.6, 0.2, 0.95, 0.8], False)
    v = R._visible(rect, 1.6)[0]
    assert v < 1, "this slot must crop"
    start = px / 100 * (1 - v)
    assert start <= 0.6 + 1e-6 and start + v >= 0.95 - 1e-6, (px, v, start)
    # without a box the focal point decides, as before
    _, px0, _ = R.place_photo(sq, 1.6, (0.2, 0.5), None, False)
    assert px0 < px
    lay = {"meta": {}, "spreads": [
        {"index": 1, "template": "full-bleed-single", "options": {"side": "left"},
         "slots": [{"photo_id": "wide", "focal": [.5, .5], "caption": None,
                    "caption_position": "none"}], "text": None, "section_id": None},
        {"index": 2, "template": T.front_name("solo"), "options": {"side": "left"},
         "slots": [{"photo_id": "wide", "focal": [.5, .5], "caption": None, "caption_position": "none"},
                   {"photo_id": "ok", "focal": [.5, .5], "caption": None, "caption_position": "none"}],
         "text": None, "section_id": None}]}
    notes = PG.protect_subjects(lay, {"wide": [0.05, 0.1, 0.95, 0.9], "ok": [0.4, 0.3, 0.6, 0.7]},
                                {"wide": 2.0, "ok": 1.0}, 1.0)
    pages0 = T.REGISTRY[lay["spreads"][0]["template"]].pages
    assert "bleed" not in pages0 and "solo" in pages0, lay["spreads"][0]["template"]
    assert lay["spreads"][1]["template"] == T.front_framed_name("solo"), lay["spreads"][1]["template"]
    assert len(notes) == 2, notes
    lay["spreads"][0]["template"] = "full-bleed-single"
    assert not PG.protect_subjects(lay, {"wide": [0.4, 0.1, 0.6, 0.9]}, {"wide": 2.0}, 1.0), \
        "a subject that fits the page crop keeps the bleed"
    PG.attach_keeps(lay, {"wide": [0.4, 0.1, 0.6, 0.9]})
    assert lay["spreads"][1]["options"]["keeps"] == [[0.4, 0.1, 0.6, 0.9], None]
    print("subjects never cropped: keep-box read, crops placed, edge pages framed: ok")

    # similar photographs of one moment go together, three or four to a page
    gs = [NS(id="g1", photo_ids=["a", "b", "c", "d", "e"], place="Lej da Staz", date_label="")]
    feats = {"a": {"scene": "seascape", "subject": "turquoise lake with boats", "who": ""},
             "b": {"scene": "seascape", "subject": "lake shore at noon", "who": ""},
             "c": {"scene": "landscape", "subject": "boats on the lake", "who": ""},
             "d": {"scene": "seascape", "subject": "lake reflections", "who": ""},
             "e": {"scene": "portrait", "subject": "woman in red jacket", "who": "anna"}}
    tk = {x: f"2026-08-05T10:{10 + i:02d}" for i, x in enumerate("abcde")}

    def singles():
        return {"meta": {}, "spreads": [{"index": i + 1, "section_id": None, "template": "two-up-equal",
                "slots": [{"photo_id": x, "focal": [.5, .5], "caption": None, "caption_position": "below"}
                          for x in pair], "text": None, "options": {}, "note": None}
                for i, pair in enumerate((("z", "a"), ("b", "c"), ("d", "e")))]}
    gs[0].photo_ids.append("z")
    lay = singles()
    PG.repack(lay, gs, {"pages": {"min": 0, "increment": 2}}, front="z", taken=tk,
              strength={"c": 5}, features=feats)
    comps = []
    for sp in lay["spreads"]:
        a, b = T.REGISTRY[sp["template"]].pages
        if (sp.get("options") or {}).get("side") == "right":
            a, b = b, a
        n = compose.COMPOSITIONS[a].slots
        comps += [(a, [x["photo_id"] for x in sp["slots"][:n]]), (b, [x["photo_id"] for x in sp["slots"][n:]])]
    # four similar photographs; the odd page count may take one of them back
    # out, but never more: at least three stay together, the strongest first
    big = [c for c in comps if c[0] in ("quad", "trio-feature")]
    assert big and len(big[0][1]) >= 3 and set(big[0][1]) <= set("abcd"), comps
    assert big[0][1][0] == "c", "the strongest photograph leads the page"
    assert any("e" in ids and len(ids) == 1 for _, ids in comps), "a different scene stays apart"
    lay = singles()
    PG.repack(lay, gs, {"pages": {"min": 0, "increment": 2}}, front="z", taken=tk,
              features=feats, density="airy")
    assert not any(T.REGISTRY[sp["template"]].pages and "quad" in T.REGISTRY[sp["template"]].pages
                   for sp in lay["spreads"]), "airy keeps single photographs apart"
    print("similar photographs of one moment composed three or four to a page: ok")

    # a photograph turned by the vision pass turns its keep-box too
    assert imaging.turn_box([0.1, 0.2, 0.3, 0.4], 90) == [0.6, 0.1, 0.8, 0.3]

    import prompt as P
    txt = P.plan_user_content("T", None, "21x21", "de", [], 12, None,
                              {"density": "dense", "selection": "all", "captions": "descriptive"})
    assert "DENSE" in txt and "EVERY PHOTOGRAPH" in txt and "DESCRIPTIVE" in txt, txt
    assert "about 12 spreads" in txt
    assert "PREFERENCES" not in P.plan_user_content("T", None, "21x21", "de", [], None, None)
    print("the planner is told the user's preferences: ok")

    # API keys: stored 0600 outside the album, an environment key always wins,
    # never shown except masked, and redacted from anything written
    import os
    import stat
    import json
    import keystore
    import providers
    secret = "sk-or-v1-0123456789abcdefSECRETtail9876"
    saved_env = {k: os.environ.pop(k) for k in list(os.environ)
                 if k in ("ALBUM_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY",
                          "ANTHROPIC_API_KEY", "ALBUM_CONFIG_DIR")}
    try:
        with tempfile.TemporaryDirectory() as td:
            os.environ["ALBUM_CONFIG_DIR"] = td
            keystore.set_key("openrouter", secret)
            mode = stat.S_IMODE(os.stat(Path(td) / "keys.json").st_mode)
            # Windows has no owner-only mode bits; the file sits in the user's profile there
            assert mode == 0o600 or os.name == "nt", oct(mode)
            st = keystore.status("openrouter")
            assert st["source"] == "user" and secret not in json.dumps(st), st
            assert st["masked"].startswith("sk-or-") and st["masked"].endswith("9876")
            p = providers.resolve(base_url="openrouter", model="x/y")
            assert p.api_key == secret, "a stored key is used when the environment has none"
            os.environ["OPENROUTER_API_KEY"] = "sk-or-v1-fromtheenvironment0000"
            p = providers.resolve(base_url="openrouter", model="x/y")
            assert p.api_key == "sk-or-v1-fromtheenvironment0000", "the environment key must win"
            assert keystore.status("openrouter")["source"] == "env"
            line = f"401 from openrouter for key {secret} and {os.environ['OPENROUTER_API_KEY']}"
            red = keystore.redact(line)
            assert secret not in red and "fromtheenvironment" not in red, red
            keystore.delete_key("openrouter")
            assert keystore.stored("openrouter") is None
        # A named key goes only to its own host: the words in a path do not count.
        assert keystore.slot_for("openai", "https://evil.example/api.openai.com") == "other"
        assert keystore.slot_for("openai", "https://openrouter.ai.evil.example/v1") == "other"
        assert keystore.slot_for("openai", "https://api.openai.com/v1") == "openai"
        assert keystore.slot_for("openai", "https://openrouter.ai/api/v1") == "openrouter"
        os.environ["OPENAI_API_KEY"] = "sk-openai-fromtheenvironment00"
        p = providers.resolve(base_url="https://evil.example/api.openai.com/v1", model="x")
        assert p.api_key is None, "OPENAI_API_KEY was handed to another host"
        p = providers.resolve(base_url="openai", model="x")
        assert p.api_key == "sk-openai-fromtheenvironment00"
        os.environ.pop("OPENAI_API_KEY")
        # State files are replaced whole: a failed write leaves the old content.
        import safeio
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "overrides.yaml"
            safeio.write_text(f, "first", encoding="utf-8")
            try:
                safeio.write_text(f, 12345, encoding="utf-8")          # not text: the write fails
            except TypeError:
                pass
            assert f.read_text(encoding="utf-8") == "first" and list(Path(td).iterdir()) == [f]
        # The project was renamed: keys stored under the old folder name
        # follow it, permissions intact, and a folder under the new name wins.
        # Path.home() reads HOME on Linux and macOS, USERPROFILE on Windows
        saved_home = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE")}
        with tempfile.TemporaryDirectory() as home:
            os.environ["HOME"] = os.environ["USERPROFILE"] = home
            os.environ.pop("ALBUM_CONFIG_DIR", None)
            old = Path(home) / ".config" / keystore.OLD_APP_DIR
            old.mkdir(parents=True)
            (old / "keys.json").write_text(json.dumps({"openrouter": secret}), encoding="utf-8")
            os.chmod(old / "keys.json", 0o600)
            assert keystore.config_dir() == Path(home) / ".config" / keystore.APP_DIR
            assert not old.exists(), "the old folder is moved, not copied"
            assert keystore.stored("openrouter") == secret, "stored keys survive the rename"
            mode = stat.S_IMODE(os.stat(keystore.config_dir() / "keys.json").st_mode)
            assert mode == 0o600 or os.name == "nt", oct(mode)
            old.mkdir()
            (old / "keys.json").write_text(json.dumps({"openrouter": "sk-or-stale"}), encoding="utf-8")
            assert keystore.stored("openrouter") == secret, "the new folder wins over a stale old one"
        for k, v in saved_home.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    finally:
        for k in ("OPENROUTER_API_KEY", "ALBUM_CONFIG_DIR"):
            os.environ.pop(k, None)
        os.environ.update(saved_env)
    hp = providers.resolve(base_url="openrouter", model="a/b", reasoning="high")
    assert hp.reasoning == "high" and hp.max_tokens >= providers.HIGH_MIN_TOKENS
    rate, known = providers.rate_for(providers.Provider("openai", "anthropic/claude-sonnet-5.5",
                                                        "https://openrouter.ai/api/v1", "k"))
    assert known and rate == (2.0, 10.0), rate
    print("keys: stored 0600, environment wins, masked, redacted; effort high; prices: ok")

    # model lists from the endpoints: OpenRouter (prices, vision), Ollama,
    # Anthropic (key in its own header), and failures as sentences
    import httpx
    real_get = httpx.get
    seen = []

    class Resp:
        def __init__(self, code, data):
            self.status_code, self._d = code, data

        def json(self):
            return self._d

    def fake_get(url, headers=None, timeout=None):
        seen.append((url, dict(headers or {})))
        if "openrouter" in url:
            return Resp(200, {"data": [
                {"id": "anthropic/claude-sonnet-5.5", "name": "Claude Sonnet 5.5",
                 "architecture": {"input_modalities": ["text", "image"]},
                 "pricing": {"prompt": "0.000002", "completion": "0.00001"}},
                {"id": "z/text-only", "architecture": {"input_modalities": ["text"]}}]})
        if "11434" in url:
            return Resp(200, {"models": [{"name": "qwen3-vl:32b"}, {"name": "llama3:8b"}]})
        if "anthropic.com" in url:
            return Resp(200, {"data": [{"id": "claude-opus-5-5", "display_name": "Claude Opus 5.5"}]})
        if "refuses" in url:
            return Resp(401, {})
        raise httpx.ConnectError("down")
    httpx.get = fake_get
    providers._LIST_CACHE.clear()
    try:
        orr = providers.list_models(providers.Provider("openai", "x", "https://openrouter.ai/api/v1", None))
        assert [m["id"] for m in orr] == ["anthropic/claude-sonnet-5.5", "z/text-only"], orr
        assert orr[0]["price_in"] == 2.0 and orr[0]["price_out"] == 10.0 and orr[0]["vision"], orr[0]
        vis = providers.list_models(providers.Provider("openai", "x", "https://openrouter.ai/api/v1", None),
                                    need_vision=True)
        assert [m["id"] for m in vis] == ["anthropic/claude-sonnet-5.5"], vis
        oll = providers.list_models(providers.Provider("openai", "x", "http://localhost:11434/v1", None),
                                    need_vision=True)
        assert [m["id"] for m in oll] == ["qwen3-vl:32b"], oll
        ant = providers.list_models(providers.Provider("anthropic", "x", None, "sk-ant-test-key-123"))
        assert ant[0]["id"] == "claude-opus-5-5" and seen[-1][1].get("x-api-key") == "sk-ant-test-key-123"
        assert not any("Authorization" in h for u, h in seen if "openrouter" in u), "OpenRouter got a key"
        for url, word in (("https://refuses.example/v1", "refused"), ("https://down.example/v1", "did not answer")):
            try:
                providers.list_models(providers.Provider("openai", "x", url, "k"))
                raise AssertionError(url)
            except RuntimeError as exc:
                assert word in str(exc), exc
    finally:
        httpx.get = real_get
        providers._LIST_CACHE.clear()
    print("model lists from OpenRouter, Ollama and Anthropic, failures as sentences: ok")


def adaptive_pages():
    """A composed page is laid out for its photographs' real shapes: nothing
    leaves the page's content area, nothing overlaps, no photograph is cut by
    more than compose.ADAPT_KEEP, the feature leads, and the paper is used."""
    from geometry import Geometry, crop_keep

    g = Geometry(210, 210, 3)
    for side in ("left", "right"):
        col = g.text_column(side)
        for asp in ([1.5], [0.67], [2.5], [1.5, 1.5], [1.5, 0.67], [0.75, 0.75, 1.5],
                    [1.33] * 4, [1.5, 0.67, 1.0, 1.33]):
            rects, cap = compose.adaptive(g, side, asp)
            assert len(rects) == len(asp)
            for r, a in zip(rects, asp):
                assert r.x >= col.x - 0.05 and r.x2 <= col.x2 + 0.05, (asp, r, col)
                assert r.y >= col.y - 0.05 and r.y2 <= col.y2 + 0.05, (asp, r, col)
                keep = compose.ADAPT_KEEP if len(asp) > 1 else compose.ADAPT_KEEP_SINGLE
                assert crop_keep(r, a) >= keep - 0.01, (asp, r, a)
            for i, a in enumerate(rects):
                for b in rects[i + 1:]:
                    ov_w = min(a.x2, b.x2) - max(a.x, b.x)
                    ov_h = min(a.y2, b.y2) - max(a.y, b.y)
                    assert ov_w <= 0.05 or ov_h <= 0.05, f"overlap {asp}: {a} {b}"
            area = [r.w * r.h for r in rects]
            if len(asp) > 1 and len(set(asp)) > 1:
                pass
            used = sum(area) / (col.w * (col.h - compose.CAPTION_BAND))
            assert used > 0.5, f"{asp}: only {used:.0%} of the page used"
    # a small photograph is not enlarged into a soft one to fill the page
    from geometry import effective_dpi
    rects, _ = compose.adaptive(g, "right", [2.0], [1600])
    assert effective_dpi(1600, 800, rects[0]) >= compose.MIN_DPI - 0.5, rects
    # the template path: options.aspects reach the composition
    r = T.build("trio-page", g, {"aspects": [1.5, 0.67, 0.67]})
    fixed = T.build("trio-page", g, {})
    assert [b.rect for b in r.slots[:3]] != [b.rect for b in fixed.slots[:3]]
    print("adaptive pages: ok")


PHOTO_DIR = ROOT.parent / "phase0" / "photos"


def _dims() -> dict:
    from PIL import Image

    test_loop_ensure()
    return {f.stem: Image.open(f).size for f in PHOTO_DIR.glob("*.jpg")}


def test_loop_ensure():      # also a fixture step: the placeholder photographs exist
    import test_loop

    test_loop.ensure_fixtures()


def _img_rects(html: str) -> list[Rect]:
    """The rectangles of the <img> tags in one page's HTML, as drawn."""
    import re

    out = []
    for m in re.finditer(r'<img [^>]*style="left:([\d.\-]+)mm;top:([\d.\-]+)mm;'
                         r'width:([\d.]+)mm;height:([\d.]+)mm;', html):
        x, y, w, h = (float(v) for v in m.groups())
        out.append(Rect(x, y, w, h))
    return out


def _page_html(g, sp: dict, side: str, dims: dict) -> str:
    """One page of one spread as the renderer draws it."""
    from schema import Layout as L

    lay = {"meta": {"title": "t", "format": "28x21", "spread_count": 1}, "sections": [],
           "spreads": [{**sp, "index": 1, "section_id": None}]}
    spread = L.model_validate(lay).spreads[0]
    T.attach_aspects([spread], dims)
    rendered = T.build(spread.template, g, spread.options)
    paths = {s.photo_id: PHOTO_DIR / f"{s.photo_id}.jpg" for s in spread.slots}
    return R.canvas_html(spread, rendered, g, paths, g.page_window(side))


def fuller_pages():
    """White space: a photograph alone on its page fills the page's content
    area, the compositor and the renderer agree on its rectangle, and a
    repacked book is mostly photograph."""
    from geometry import CROP_KEEP_SINGLE, MIN_CROP_KEEP

    g = R.geometry_for(LAB, "28x21")
    dims = _dims()

    # fill_ratio_single: a 4:3 landscape fills at least 80% of the text column;
    # a portrait, which used to be shrunk back to a narrow strip, at least 60%
    # as drawn.
    rects, _ = compose.adaptive(g, "right", [4 / 3], [4000])
    assert compose.fill_ratio(g, "right", rects) >= 0.80, compose.fill_ratio(g, "right", rects)
    sp = {"template": "single-centered", "slots": [
        {"photo_id": "p002", "focal": [0.5, 0.5], "caption": None, "caption_position": "none"}],
        "options": {}}
    drawn = _img_rects(_page_html(g, sp, "right", dims))
    assert len(drawn) == 1
    portrait = compose.fill_ratio(g, "right", drawn)
    assert portrait >= 0.60, f"a portrait fills only {portrait:.0%} of the page"
    # no caption on the page: no band reserved at its foot
    with_band, _ = compose.adaptive(g, "right", [1.0])
    no_band, _ = compose.adaptive(g, "right", [1.0], band=False)
    assert no_band[0].h > with_band[0].h + 5, (no_band, with_band)
    assert g.margin_outer < g.page_w * 0.05 and g.margin_top < g.page_h * 0.05

    # fit_rect_agrees_with_adaptive: what compose.adaptive sized is what is
    # drawn (the renderer used to fit it again with a stricter crop limit)
    for pid in ("p002", "p007", "p005", "p001", "p004"):
        sp = {"template": "single-centered", "slots": [
            {"photo_id": pid, "focal": [0.5, 0.5], "caption": None, "caption_position": "none"}],
            "options": {}}
        from schema import Layout as L

        spread = L.model_validate({"meta": {"title": "t", "format": "28x21", "spread_count": 1},
                                   "spreads": [{**sp, "index": 1, "section_id": None}]}).spreads[0]
        T.attach_aspects([spread], dims)
        slot = T.build(spread.template, g, spread.options).slots[0].rect
        drawn = _img_rects(_page_html(g, sp, "right", dims))[0]
        assert abs(drawn.w - slot.w) < 0.1 and abs(drawn.h - slot.h) < 0.1, (pid, slot, drawn)
    assert CROP_KEEP_SINGLE < MIN_CROP_KEEP

    # a repacked book: the pages that carry photographs are mostly photograph
    groups = [NS(id="g1", photo_ids=["p001", "p015", "p005", "p002", "p003"], place="A", date_label="1"),
              NS(id="g2", photo_ids=["p006", "p008", "p009", "p010", "p004"], place="B", date_label="2"),
              NS(id="g3", photo_ids=["p011", "p012", "p014", "p007"], place="C", date_label="3")]
    lay = good_layout()
    PG.repack(lay, groups, LAB)
    T.attach_aspects(lay["spreads"], dims)
    fills = []
    for sp in lay["spreads"]:
        html_l = _img_rects(_page_html(g, sp, "left", dims)) if sp["slots"] else []
        html_r = _img_rects(_page_html(g, sp, "right", dims)) if sp["slots"] else []
        for side, rs in (("left", html_l), ("right", html_r)):
            if rs:
                fills.append(min(1.0, compose.fill_ratio(g, side, rs)))
    mean = sum(fills) / len(fills)
    assert len(fills) >= 14 and mean >= 0.6, f"mean fill {mean:.0%} over {len(fills)} pages"
    print(f"fuller pages: 4:3 {compose.fill_ratio(g, 'right', rects):.0%}, portrait {portrait:.0%}, "
          f"book mean {mean:.0%}: ok")


def compositions_survive(groups):
    """Trios and quads are not the first thing the 24-page minimum breaks."""
    def single(i, pid, section="s1"):
        return {"index": i, "section_id": section, "template": "single-centered",
                "slots": [{"photo_id": pid, "focal": [.5, .5], "caption": None,
                           "caption_position": "below"}], "text": None, "options": {}, "note": None}

    ids = [f"q{i:02d}" for i in range(1, 16)]
    gs = [NS(id="g1", photo_ids=ids, place="Lej", date_label="")]
    feats = {x: {"scene": "other", "subject": "", "who": ""} for x in ids}
    for x in ids[4:8]:
        feats[x] = {"scene": "seascape", "subject": "lake", "who": ""}

    def book():
        return {"meta": {"title": "t"}, "spreads": [single(i + 1, x) for i, x in enumerate(ids)]}

    def comps(lay):
        out = []
        for sp in lay["spreads"]:
            spec = T.REGISTRY[sp["template"]]
            a, b = spec.pages or ("blank", "blank")
            if (sp.get("options") or {}).get("side") == "right" and not sp["template"].startswith("front"):
                a, b = b, a
            if sp["template"].startswith("front"):
                a, b = "bleed", spec.pages[1]
            out += [a, b]
        return out

    # trio_quad_survive_minimum: similar photographs, 15 photos, 24-page minimum
    lay = book()
    PG.repack(lay, gs, LAB, front=ids[0], features=feats, taken={x: f"2026-08-05T10:{i:02d}" for i, x in enumerate(ids)})
    assert len(lay["spreads"]) * 2 >= LAB["pages"]["min"]
    assert any(c in ("trio-feature", "trio-column", "quad") for c in comps(lay)), comps(lay)
    # ... but a book that has just enough photographs for the minimum is not
    # given empty pages to keep that composition: it gives way instead
    full = [f"r{i:02d}" for i in range(1, 25)]          # 24 photographs, 24 pages
    fg = [NS(id="g1", photo_ids=full, place="Lej", date_label="")]
    ff = {x: {"scene": "other", "subject": "", "who": ""} for x in full}
    for x in full[4:8]:
        ff[x] = {"scene": "seascape", "subject": "lake", "who": ""}
    lay = {"meta": {"title": "t"}, "spreads": [single(i + 1, x) for i, x in enumerate(full)]}
    PG.repack(lay, fg, LAB, front=full[0], features=ff,
              taken={x: f"2026-08-05T10:{i:02d}" for i, x in enumerate(full)})
    assert len(lay["spreads"]) * 2 == LAB["pages"]["min"], len(lay["spreads"])
    assert "blank" not in comps(lay) and "text" not in comps(lay), comps(lay)
    assert sorted({s["photo_id"] for sp in lay["spreads"] for s in sp["slots"]}) == full
    # ... and the photographs the user marked, when they sit side by side
    lay = book()
    PG.repack(lay, gs, LAB, front=ids[0], together=set(ids[9:13]))
    assert len(lay["spreads"]) * 2 >= LAB["pages"]["min"]
    assert any(c in ("trio-feature", "trio-column", "quad") for c in comps(lay)), comps(lay)
    assert all(x in {s["photo_id"] for sp in lay["spreads"] for s in sp["slots"]} for x in ids)
    # even with nothing marked, the lighter pages give way first: a book made
    # of pairs and one quad keeps the quad
    lay = {"meta": {"title": "t"}, "spreads": [
        {"index": 1, "section_id": "s1", "template": "two-up-equal", "slots": [
            {"photo_id": x, "focal": [.5, .5], "caption": None, "caption_position": "below"}
            for x in ids[:2]], "text": None, "options": {}, "note": None},
        {"index": 2, "section_id": "s1", "template": "grid-four", "slots": [
            {"photo_id": x, "focal": [.5, .5], "caption": None, "caption_position": "below"}
            for x in ids[2:6]], "text": None, "options": {}, "note": None},
        {"index": 3, "section_id": "s1", "template": "duo-page", "slots": [
            {"photo_id": x, "focal": [.5, .5], "caption": None, "caption_position": "below"}
            for x in ids[6:8]], "text": None, "options": {}, "note": None},
        {"index": 4, "section_id": "s1", "template": "duo-page", "slots": [
            {"photo_id": x, "focal": [.5, .5], "caption": None, "caption_position": "below"}
            for x in ids[8:10]], "text": None, "options": {}, "note": None}]}
    PG.repack(lay, gs, LAB, front=ids[0], spare=ids[10:])
    assert "quad" in comps(lay) or "trio-feature" in comps(lay), comps(lay)

    # compose_flagged_makes_trio: three side by side become one trio, directly
    pages = [PG.Page("solo", [{"photo_id": x, "focal": [.5, .5], "caption": None,
                               "caption_position": "below"}]) for x in ids[:6]]
    moved = PG._compose_flagged(pages, set(ids[1:4]), {})
    assert [(p.comp, len(p.slots)) for p in pages] == \
        [("solo", 1), ("trio-feature", 3), ("solo", 1), ("solo", 1)], \
        [(p.comp, len(p.slots)) for p in pages]
    assert sorted(moved) == sorted(ids[1:4])
    print("trios and quads survive the page minimum; three marked photographs make one trio: ok")


def subjects_without_a_box():
    """A person the vision pass saw but drew no box around is still not cut."""
    # people, no box: a fallback box around the focal point
    box = PG.fallback_keep((0.9, 0.5), 3)
    assert box and box[2] <= 1.0 and box[0] >= 0 and box[0] < 0.9 < box[2], box
    assert PG.fallback_keep((0.5, 0.5), 0) is None
    one = PG.fallback_keep((0.5, 0.5), 1)
    assert (one[2] - one[0]) < (box[2] - box[0]), "a group is a wider box than one person"
    photos = [NS(id="a", keep=None, suggested_focal=(0.9, 0.5), people=3),
              NS(id="b", keep=[0.1, 0.1, 0.4, 0.9], suggested_focal=(0.2, 0.5), people=1),
              NS(id="c", keep=None, suggested_focal=(0.5, 0.5), people=0)]
    keeps = PG.derive_keeps(photos)
    assert keeps["a"] == box and keeps["b"] == [0.1, 0.1, 0.4, 0.9] and "c" not in keeps
    # subject_not_cut_without_keepbox: a portrait of three people set to the
    # edges of a landscape page is held inside the margins, not cropped
    lay = {"meta": {}, "spreads": [
        {"index": 1, "template": "full-bleed-single", "options": {"side": "left"},
         "slots": [{"photo_id": "a", "focal": [.9, .5], "caption": None, "caption_position": "none"}],
         "text": None, "section_id": None}]}
    notes = PG.protect_subjects(lay, {}, {"a": 0.75}, 280 / 210)
    assert not notes, "without any box nothing is known"
    notes = PG.protect_subjects(lay, keeps, {"a": 0.75}, 280 / 210)
    assert notes and "bleed" not in T.REGISTRY[lay["spreads"][0]["template"]].pages, lay["spreads"][0]
    # the renderer too: a bleeding slot whose box cannot fit shows the whole
    # photograph inside the margins rather than cutting it
    g = R.geometry_for(LAB, "28x21")
    rect, px, py = R.place_photo(g.full_bleed("left"), 0.75, (0.9, 0.5), keep=box, bleeds=True,
                                 fallback=g.text_column("left"))
    assert rect != g.full_bleed("left") and rect.x >= g.trim("left").x, rect
    print("subjects without a keep-box are not cut: ok")


def user_spreads(groups):
    """A photograph set across two pages."""
    g = R.geometry_for(LAB, "28x21")
    dims = _dims()
    spec = T.REGISTRY[T.USER_SPREAD]
    assert spec.spans_fold and not spec.planner_visible
    assert T.USER_SPREAD not in [c["template"] for c in T.catalogue_for_prompt(True)]
    wide = T.build(T.USER_SPREAD, g, {"aspects": [2.4]}).slots[0]
    assert wide.bleeds and wide.rect == g.spread_bleed and g.spans_fold(wide.rect)
    tall = T.build(T.USER_SPREAD, g, {"aspects": [1.5]}).slots[0]
    assert not tall.bleeds and g.spans_fold(tall.rect) and abs(
        (tall.rect.x + tall.rect.x2) / 2 - g.fold_x) < 1, tall.rect

    lay = good_layout()
    lay["spreads"][2]["template"] = T.USER_SPREAD
    lay["spreads"][2]["slots"] = lay["spreads"][2]["slots"][:1]
    pid = lay["spreads"][2]["slots"][0]["photo_id"]
    PG.repack(lay, groups, LAB)
    Layout.model_validate(lay)
    at = [i for i, sp in enumerate(lay["spreads"]) if sp["template"] == T.USER_SPREAD]
    assert len(at) == 1 and lay["spreads"][at[0]]["slots"][0]["photo_id"] == pid, \
        [sp["template"] for sp in lay["spreads"]]
    assert [s["photo_id"] for sp in lay["spreads"] for s in sp["slots"]].count(pid) == 1
    assert len(lay["spreads"]) * 2 >= LAB["pages"]["min"]
    # still there when the book is short and every other page is taken apart,
    # and when the spread would be the page facing the front
    lay2 = good_layout()
    lay2["spreads"] = [lay2["spreads"][0], dict(lay2["spreads"][2], template=T.USER_SPREAD,
                                                slots=lay2["spreads"][2]["slots"][:1])] + lay2["spreads"][3:]
    for i, sp in enumerate(lay2["spreads"], 1):
        sp["index"] = i
    lay2["meta"]["spread_count"] = len(lay2["spreads"])
    PG.repack(lay2, groups, LAB)
    Layout.model_validate(lay2)
    assert lay2["spreads"][0]["template"].startswith("front")
    assert sum(sp["template"] == T.USER_SPREAD for sp in lay2["spreads"]) == 1
    assert T.REGISTRY[lay2["spreads"][0]["template"]].pages[1] != "blank" or len(lay2["spreads"]) > 1
    # the validator allows the fold for this template, and only this one
    import validate
    from validate import load_photos

    photos = load_photos(PHOTO_DIR)
    rep = validate.check(Layout.model_validate(lay), photos, g, LAB)
    assert not [i for i in rep.issues if i.code == "fold-crossing"], \
        [str(i) for i in rep.issues if i.code == "fold-crossing"]
    bad = Layout.model_validate(lay)
    bad.spreads[at[0]].template = "panorama-spread"
    rep = validate.check(bad, photos, g, LAB)
    assert [i for i in rep.issues if i.code == "fold-crossing"], "allow_fold_crossing is still off"
    # rendered: one picture reaches both pages
    sp = lay["spreads"][at[0]]
    left, right = (_img_rects(_page_html(g, sp, s, dims)) for s in ("left", "right"))
    assert len(left) == 1 and len(right) == 1 and left[0] == right[0], (left, right)
    assert left[0].x < g.fold_x < left[0].x2
    print("a photograph across two pages: placed once, never split, fold allowed only for it: ok")


def gutter_and_bleed():
    """The two bugs in the user's screenshots, as pixels.

    1. A page must not show its neighbour: a full-bleed photograph on the left
       page, a blank right page — the right page's inner edge stays white.
    2. A full-bleed photograph fills its page even when its shape is far from
       the page's (a 2.5:1 panorama on a square page), instead of floating in
       white."""
    import tempfile

    from PIL import Image

    photos_dir = ROOT.parent / "phase0" / "photos"
    if not any(photos_dir.glob("*.jpg")):
        print("gutter/bleed: skipped (no fixtures — run test_loop.py first)")
        return
    from validate import load_photos

    info = load_photos(photos_dir)
    pano = max(info.values(), key=lambda p: p.width / p.height).id
    lay = {"meta": {"title": "t", "format": "21x21", "spread_count": 1}, "spreads": [
        {"index": 1, "template": "full-bleed-single", "options": {"side": "left"},
         "slots": [{"photo_id": pano, "focal": [0.5, 0.5], "caption": None}]}]}
    layout = Layout.model_validate(lay)
    g = R.geometry_for(LAB, "21x21")
    paths = {pano: info[pano].path}
    css = (ROOT.parent / "phase0" / "assets" / "style.css").read_text(encoding="utf-8")
    html, _ = R.build_inner_html(layout, g, paths, css)
    with tempfile.TemporaryDirectory() as td:
        files = R.render_jpegs(html, Path(td) / "x.html", Path(td) / "pages", dpi=40)
        left, right = (Image.open(f).convert("L") for f in files)
        w, h = right.size
        strip = [right.getpixel((x, y)) for x in range(0, max(2, w // 20)) for y in range(0, h, 4)]
        assert min(strip) > 235, f"the facing page shows at the gutter (min {min(strip)})"
        lw, lh = left.size
        corners = [left.getpixel((x, y)) for x in (1, lw // 2, lw - 2) for y in (1, lh - 2)]
        assert max(corners) < 235, f"the full-bleed photograph does not reach the edges: {corners}"
    print("gutter clean, full bleed fills the page: ok")


def costs_and_places():
    """Pricing per model, a reported cost winning, local models free; and the
    named-places lookup ranking a glacier over a town-centre viewpoint."""
    import geocode
    import providers
    from planner import Usage

    u = Usage(1_000_000, 100_000, 1, 0)
    providers.price(providers.Provider(model="claude-sonnet-4-5"), u)
    assert abs(u.cost_usd - 4.5) < 1e-6, u.cost_usd
    u = Usage(1000, 1000, 1, 0, cost_usd=0.0123)
    providers.price(providers.Provider(model="claude-sonnet-4-5"), u)
    assert u.cost_usd == 0.0123, "a reported cost was overwritten"
    u = Usage(10_000, 10_000, 1, 0)
    providers.price(providers.Provider(name="openai", model="qwen", base_url="http://localhost:11434/v1"), u)
    assert u.cost_usd == 0.0
    u = Usage(1_000_000, 0, 1, 0)
    providers.price(providers.Provider(name="openai", model="someone/unknown", base_url="https://x"), u)
    assert u.estimated and u.cost_usd > 0
    total = Usage()
    total.add(Usage(1, 1, 1, 0, cost_usd=0.5))
    total.add(Usage(1, 1, 1, 0, cost_usd=0.25, estimated=True))
    assert total.cost_usd == 0.75 and total.estimated

    import tempfile
    calls = []

    def fake(lat, lon, lang):
        calls.append((lat, lon))
        return [{"name": "Pontresina Aussicht", "kind": "viewpoint", "lat": lat, "lon": lon},
                {"name": "Morteratschgletscher", "kind": "glacier", "lat": lat + 0.005, "lon": lon}]

    ph = [NS(id="a", gps=(46.43, 9.93), place=None, features=[]),
          NS(id="b", gps=(46.4301, 9.9301), place=None, features=[]),
          NS(id="c", gps=None, place=None, features=[])]
    with tempfile.TemporaryDirectory() as td:
        geocode.apply(ph, Path(td), offline_db=False, online=True, fetch=fake)
        assert len(calls) == 1, "the lookup is per ~1 km cell, cached"
        assert ph[0].place == "Morteratschgletscher", ph[0].place
        assert ph[0].features[0].startswith("Morteratschgletscher"), ph[0].features
        geocode.apply(ph, Path(td), offline_db=False, online=True, fetch=fake)
        assert len(calls) == 1, "the cache was not used"
        geocode.apply(ph, Path(td), offline_db=False, online=False)
        assert ph[0].features, "cached features should still be used offline"
    print("cost pricing and named places: ok")


def precise_places():
    """Precise town names from Nominatim: off unless asked for, one request
    per ~500 m cell and language, used at once only before the first layout
    (or after a refresh), never over the user's own names, quiet when the
    server is down, and at most one request a second."""
    import json
    import tempfile
    import time
    import urllib.request

    import geocode

    # the label: the most specific settlement, then the region
    lab = geocode.nominatim_label
    assert lab({"hamlet": "Isenfluh", "village": "Lauterbrunnen", "state": "Bern"}) == "Isenfluh, Bern"
    assert lab({"city": "Zürich", "suburb": "Kreis 1", "state": "Zürich"}) == "Zürich"
    assert lab({"town": "Baunei", "county": "Ogliastra"}) == "Baunei, Ogliastra"
    assert lab({"road": "SS125", "state": "Sardegna"}) is None, "no settlement is not a name"
    assert geocode.nominatim_base("https://geo.example.org/reverse/") == "https://geo.example.org"
    assert geocode.nominatim_base(None).startswith("https://"), geocode.nominatim_base(None)

    asked = []

    def fake(lat, lon, lang):
        asked.append((lat, lon, lang))
        return {"en": "Isenfluh, Bern"}.get(lang, "Isenfluh, BE")

    ph = [NS(id="a", gps=(46.6201, 7.8902), place=None, features=[]),
          NS(id="b", gps=(46.6203, 7.8904), place=None, features=[]),   # same cell
          NS(id="c", gps=(46.70, 7.90), place=None, features=[]),
          NS(id="d", gps=None, place=None, features=[])]

    def run(td, **kw):
        geocode.apply(ph, Path(td), landmarks=False, precise_fetch=fake, **kw)
        return [p.place for p in ph]

    geocode._NOM_DOWN_UNTIL = 0.0
    with tempfile.TemporaryDirectory() as td:
        # the offline database's answers, as if reverse_geocoder had given them
        (Path(td) / "places_cache.json").write_text(json.dumps(
            {"46.620,7.890": "Lauterbrunnen, Bern", "46.700,7.900": "Interlaken, Bern"}), encoding="utf-8")
        assert run(td) == ["Lauterbrunnen, Bern", "Lauterbrunnen, Bern", "Interlaken, Bern", None]
        assert not asked and not (Path(td) / "places_online.json").exists(), "asked although off"

        # before the first layout: asked once per cell, used at once
        assert run(td, precise=True, online=True, language="en", adopt=lambda: True)[:2] \
            == ["Isenfluh, Bern"] * 2
        assert len(asked) == 2, f"one request per ~500 m cell: {asked}"
        assert {(round(a[0], 3), round(a[1], 3)) for a in asked} == {(46.62, 7.89), (46.7, 7.9)}, \
            "the cell is asked, not the photograph's exact point"
        assert run(td, precise=True, online=True, language="en")[0] == "Isenfluh, Bern"
        assert len(asked) == 2, "the cache was not used"
        assert run(td, precise=True, language="en")[0] == "Isenfluh, Bern", "editor reload, offline"
        assert run(td, language="en")[0] == "Lauterbrunnen, Bern", "turned off: the offline name again"

        # after the first layout: a new answer waits for a refresh
        assert run(td, precise=True, online=True, language="de", adopt=lambda: False)[0] \
            == "Lauterbrunnen, Bern", "a late answer changed a book already made"
        assert len(asked) == 4, "another language is another question"
        assert geocode.refresh_precise(Path(td), "de") == 2
        assert run(td, precise=True, language="de")[0] == "Isenfluh, BE"

        # the user's own names always win
        (Path(td) / "places.yaml").write_text(
            "- name: Unser Chalet\n  lat: 46.6202\n  lon: 7.8903\n  radius_m: 300\n", encoding="utf-8")
        assert run(td, precise=True, language="de")[:3] == ["Unser Chalet", "Unser Chalet", "Isenfluh, BE"]

    # a server that does not answer: one try, then quiet for the cooldown
    calls = []

    def down(lat, lon, lang):
        calls.append(1)
        raise TimeoutError("The read operation timed out")

    geocode._NOM_DOWN_UNTIL, geocode._NOM_SAID = 0.0, False
    with tempfile.TemporaryDirectory() as td:
        pr = geocode.Precise(Path(td), fetch=down)
        assert pr.fetch_missing([p.gps for p in ph]) == 0
        assert pr.fetch_missing([p.gps for p in ph]) == 0
        assert len(calls) == 1, f"asked {len(calls)} times during the cooldown"
        assert geocode.places_status()["precise"] == "unavailable"
        assert not pr.cache, "a failure is not an answer"
    geocode._NOM_DOWN_UNTIL = 0.0

    # the real request: the right parameters, and one a second at most
    urls, saved = [], (urllib.request.urlopen, geocode.NOM_INTERVAL_S)

    class Answer:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return self.body

    def fake_open(req, timeout=None):
        urls.append((time.monotonic(), req.full_url, req.get_header("User-agent")))
        if "lat=0.0000" in req.full_url:
            return Answer(b'{"error": "Unable to geocode"}')
        return Answer(json.dumps({"address": {"village": "Wengen", "state": "Bern"}}).encode())

    try:
        urllib.request.urlopen, geocode.NOM_INTERVAL_S = fake_open, 0.3
        assert geocode._nominatim(46.605, 7.92, "de", "https://geo.example.org/") == "Wengen, Bern"
        assert geocode._nominatim(0.0, 0.0, None, "https://geo.example.org") is None
    finally:
        urllib.request.urlopen, geocode.NOM_INTERVAL_S = saved
    (t1, u1, ua), (t2, u2, _) = urls
    assert u1.startswith("https://geo.example.org/reverse?") and "accept-language=de" in u1, u1
    assert "zoom=14" in u1 and "addressdetails=1" in u1 and "accept-language" not in u2, (u1, u2)
    assert ua and "impaginai" in ua, "the usage policy asks for an identifying User-Agent"
    assert t2 - t1 >= 0.29, f"two requests {t2 - t1:.2f}s apart"
    print("precise place names: ok")


if __name__ == "__main__":
    sys.exit(main())
