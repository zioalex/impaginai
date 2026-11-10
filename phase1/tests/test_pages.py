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


def main() -> int:
    groups = [
        NS(id="g1", photo_ids=["p001", "p015", "p005", "p002", "p003"], place="Cala Gonone", date_label="3. Mai"),
        NS(id="g2", photo_ids=["p006", "p008", "p009", "p010", "p004"], place="Orosei", date_label="5. Mai"),
        NS(id="g3", photo_ids=["p011", "p012", "p014", "p007"], place="Nuoro", date_label="7. Mai"),
    ]

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
    assert lay["spreads"][0]["template"] == "title-page", "the opening spread was not kept"
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

    adaptive_pages()
    gutter_and_bleed()
    costs_and_places()
    return 0


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
                assert crop_keep(r, a) >= compose.ADAPT_KEEP - 0.01, (asp, r, a)
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
    # the template path: options.aspects reach the composition
    r = T.build("trio-page", g, {"aspects": [1.5, 0.67, 0.67]})
    fixed = T.build("trio-page", g, {})
    assert [b.rect for b in r.slots[:3]] != [b.rect for b in fixed.slots[:3]]
    print("adaptive pages: ok")


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
    css = (ROOT.parent / "phase0" / "assets" / "style.css").read_text()
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


if __name__ == "__main__":
    sys.exit(main())
