# SPDX-FileCopyrightText: 2026 Alessandro Surace
# SPDX-License-Identifier: AGPL-3.0-only
"""Page composition on a modular grid.

The first template library had no composition layer: every template placed at
most one photograph per page, so a "spread template" was really just a pair of
single-photo pages and the book read as a slideshow with blank facing pages.

This module supplies the missing level. A PAGE COMPOSITION arranges one or more
photographs within a single page, on a shared modular grid, so that images line
up across the whole book. A spread template is then simply a pair of page
compositions — which is what makes real pairings possible (a bleeding hero
facing a stack of three; a quiet single facing a mosaic) without any image ever
crossing the fold.

The grid

    Content area  = the page's trim inset by the asymmetric margins, minus a
                    caption band reserved at the foot.
    Cells         = COLS x ROWS modules with a uniform gutter between them.
    A slot        = a rectangle spanning whole cells.

Slots therefore share edges across different compositions and across pages,
which is what the eye reads as deliberate rather than arbitrary. Nothing here
emits CSS; a composition returns rectangles in canvas millimetres, exactly like
the rest of the template library, so the validator measures the same numbers
the renderer draws.

Captions in a multi-image composition would fight each other, so only the
feature slot (always index 0) carries one, in the reserved band under the grid.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from geometry import CROP_KEEP_MULTI, CROP_KEEP_SINGLE, Geometry, Rect

COLS = 6
ROWS = 6
GUTTER = 4.0
CAPTION_BAND = 10.0

Side = Literal["left", "right"]


@dataclass(frozen=True)
class Cell:
    """A slot expressed in grid cells: column, row, spans."""

    c: int
    r: int
    cs: int
    rs: int
    feature: bool = False


@dataclass
class Composition:
    name: str
    slots: int
    cells: tuple[Cell, ...]
    bleed: bool = False          # ignores the grid, covers the whole page
    blank: bool = False
    describe: str = ""


def grid_rect(g: Geometry, side: Side, cell: Cell) -> Rect:
    """Map grid cells to canvas millimetres for one page."""
    c = g.text_column(side)
    usable_h = c.h - CAPTION_BAND
    cw = (c.w - GUTTER * (COLS - 1)) / COLS
    rh = (usable_h - GUTTER * (ROWS - 1)) / ROWS
    x = c.x + cell.c * (cw + GUTTER)
    y = c.y + cell.r * (rh + GUTTER)
    w = cell.cs * cw + GUTTER * (cell.cs - 1)
    h = cell.rs * rh + GUTTER * (cell.rs - 1)
    return Rect(round(x, 2), round(y, 2), round(w, 2), round(h, 2))


def caption_band(g: Geometry, side: Side) -> Rect:
    c = g.text_column(side)
    return Rect(c.x, round(c.y + c.h - CAPTION_BAND + 2, 2), c.w, CAPTION_BAND - 2)


# --- the vocabulary ----------------------------------------------------
#
# Kept deliberately small. Every composition below is either one image given
# room, or several images in a clear hierarchy — never an even scatter, which
# is what makes a page look like a contact sheet.

COMPOSITIONS: dict[str, Composition] = {
    "bleed": Composition(
        "bleed", 1, (), bleed=True,
        describe="one photograph filling the page to all four edges"),

    "solo": Composition(
        "solo", 1, (Cell(0, 0, 6, 6, feature=True),),
        describe="one photograph in the margins, caption beneath"),

    "solo-air": Composition(
        "solo-air", 1, (Cell(0, 0, 6, 4, feature=True),),
        describe="one photograph in the upper two thirds, a third of the page left empty"),

    "duo-stack": Composition(
        "duo-stack", 2, (Cell(0, 0, 6, 4, feature=True), Cell(0, 4, 6, 2)),
        describe="a large photograph above a wide, shallow one"),

    "duo-offset": Composition(
        "duo-offset", 2, (Cell(0, 0, 4, 4, feature=True), Cell(3, 4, 3, 2)),
        describe="a large photograph with a smaller one stepped down to the right"),

    "trio-feature": Composition(
        "trio-feature", 3,
        (Cell(0, 0, 6, 3, feature=True), Cell(0, 3, 3, 3), Cell(3, 3, 3, 3)),
        describe="one wide photograph over a pair beneath it"),

    "trio-column": Composition(
        "trio-column", 3,
        (Cell(0, 0, 3, 6, feature=True), Cell(3, 0, 3, 3), Cell(3, 3, 3, 3)),
        describe="one tall photograph beside two stacked"),

    "quad": Composition(
        "quad", 4,
        (Cell(0, 0, 3, 3, feature=True), Cell(3, 0, 3, 3), Cell(0, 3, 3, 3), Cell(3, 3, 3, 3)),
        describe="four photographs in a square grid, contact-sheet feel"),

    "text": Composition("text", 0, (), describe="a text block"),
    "blank": Composition("blank", 0, (), blank=True, describe="an empty page"),
}


def build_page(g: Geometry, side: Side, name: str, aspects=None, pixels=None, band: bool = True):
    """Return (rects, feature_index, caption_rect) for one page composition.

    With `aspects` (width/height of each photograph, in slot order) the page is
    laid out for the photographs it actually carries — see `adaptive` — instead
    of on fixed grid cells that crop or letterbox whatever does not match.
    `band` False reserves no caption band at the foot (the page has no caption
    to set there), so the photographs use that paper too.
    """
    comp = COMPOSITIONS[name]
    if comp.blank or comp.slots == 0:
        return [], None, None
    if comp.bleed:
        return [g.full_bleed(side)], 0, None
    if aspects and len(aspects) == comp.slots and all(a and a > 0 for a in aspects):
        rects, cap = adaptive(g, side, list(aspects), pixels, band=band)
        return rects, 0, cap
    rects = [grid_rect(g, side, c) for c in comp.cells]
    feature = next((i for i, c in enumerate(comp.cells) if c.feature), 0)
    return rects, feature, caption_band(g, side)


# --- layout from the photographs' own shapes ------------------------------
#
# Fixed cells waste the page: a landscape frame in a square cell is either cut
# by a quarter or shrunk inside white. `adaptive` instead tries every way of
# putting the page's photographs (in order, feature first) into rows or into
# columns, sizes each row so its photographs share one height (columns: one
# width), lets each photograph be cropped by at most ADAPT_KEEP to close what
# is left, and keeps the arrangement that covers the most paper while the
# feature stays the largest image.

ADAPT_KEEP = CROP_KEEP_MULTI     # never cut more than 14% of a photograph to fill space
# A photograph alone on its page may lose a little more: a 2:1 phone panorama
# on a 4:3 page otherwise floats in a band of white half the page high.
ADAPT_KEEP_SINGLE = CROP_KEEP_SINGLE
MIN_DPI = 200.0       # no photograph is enlarged past this to fill its page


def _partitions(n: int):
    """Ordered ways to cut n items into consecutive groups."""
    if n == 0:
        yield []
        return
    for k in range(1, n + 1):
        for rest in _partitions(n - k):
            yield [k] + rest


def _lines(W: float, H: float, aspects: list[float], parts: list[int], keep: float = ADAPT_KEEP):
    """Lay `aspects` out in rows of `parts` sizes inside W x H, top-left origin.
    Returns [(x, y, w, h)] in slot order, and the block's (w, h)."""
    G = GUTTER
    rows, i = [], 0
    for k in parts:
        rows.append(aspects[i:i + k])
        i += k
    # natural height of each row when it spans the full width
    nat = [(W - G * (len(r) - 1)) / sum(r) for r in rows]
    avail_h = H - G * (len(rows) - 1)
    stretch_x = 1.0
    if sum(nat) > avail_h:                          # too tall: shrink, then widen
        s = avail_h / sum(nat)
        hs = [h * s for h in nat]
        stretch_x = 1.0 / keep                      # widen rows back towards W
    else:                                           # too short: heighten rows
        f = min(avail_h / sum(nat), 1.0 / keep)
        hs = [h * f for h in nat]
    out, y, block_w = [], 0.0, 0.0
    for r, h in zip(rows, hs):
        ws = [a * h for a in r]
        room = W - G * (len(r) - 1)
        # widen up to the crop allowance, or narrow a row that heightening
        # pushed past the page (that too is a crop, bounded by ADAPT_KEEP)
        fx = min(stretch_x, room / sum(ws))
        ws = [w * fx for w in ws]
        x = 0.0
        for w in ws:
            out.append((x, y, w, h))
            x += w + G
        block_w = max(block_w, x - G)
        y += h + G
    return out, block_w, y - G


def fill_ratio(g: Geometry, side: Side, rects) -> float:
    """How much of the page's text column the photographs cover: their area
    (clipped to the column) over the column's. A measure of white space."""
    c = g.text_column(side)
    area = 0.0
    for r in rects:
        w = min(r.x2, c.x2) - max(r.x, c.x)
        h = min(r.y2, c.y2) - max(r.y, c.y)
        if w > 0 and h > 0:
            area += w * h
    return area / (c.w * c.h) if c.w * c.h else 0.0


def adaptive(g: Geometry, side: Side, aspects: list[float], pixels=None,
             min_dpi: float = MIN_DPI, band: bool = True):
    """Rectangles for photographs of the given aspect ratios on one page.

    `pixels`, when known, is each photograph's pixel width: the whole block is
    then scaled down (centred) until none prints below `min_dpi`, rather than
    enlarging a small photograph into a soft one to fill the page."""
    c = g.text_column(side)
    W, H = c.w, c.h - (CAPTION_BAND if band else 0.0)
    n = len(aspects)
    keep = ADAPT_KEEP if n > 1 else ADAPT_KEEP_SINGLE
    best = None
    for parts in _partitions(n):
        for transpose in (False, True):
            if transpose and len(parts) == n and n == 1:
                continue
            if transpose:
                cells, bw, bh = _lines(H, W, [1 / a for a in aspects], parts, keep)
                cells = [(y, x, h, w) for (x, y, w, h) in cells]
                bw, bh = bh, bw
            else:
                cells, bw, bh = _lines(W, H, aspects, parts, keep)
            areas = [w * h for (_, _, w, h) in cells]
            score = sum(areas) / (W * H)
            if n > 1:
                if areas[0] < max(areas[1:]) * 0.999:
                    score *= 0.75                   # the feature must lead
                if min(areas) < 0.09 * W * H:
                    score *= 0.8                    # nothing postage-stamp sized
            if best is None or score > best[0] + 1e-9:
                best = (score, cells, bw, bh)
    _, cells, bw, bh = best
    if pixels and len(pixels) == n:
        # Cover-fit: the drawn width is the wider of the cell and the
        # photograph scaled to the cell's height.
        f = 1.0
        for (x, y, w, h), a, px in zip(cells, aspects, pixels):
            drawn = max(w, h * a)
            limit = px / min_dpi * 25.4
            if px and drawn > limit:
                f = min(f, limit / drawn)
        if f < 1.0:
            cells = [(x * f, y * f, w * f, h * f) for (x, y, w, h) in cells]
            bw, bh = bw * f, bh * f
    ox = c.x + (W - bw) / 2
    oy = c.y + (H - bh) / 2
    rects = [Rect(round(ox + x, 2), round(oy + y, 2), round(w, 2), round(h, 2))
             for (x, y, w, h) in cells]
    cap = Rect(round(ox, 2), round(oy + bh + 2.5, 2), round(bw, 2), CAPTION_BAND - 2.5)
    return rects, cap
