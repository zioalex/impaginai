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
    return 0


if __name__ == "__main__":
    sys.exit(main())
