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
from typing import Callable, Literal

from geometry import Geometry, Rect

COLS = 6
ROWS = 6
GUTTER = 4.0
CAPTION_BAND = 11.0

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


def build_page(g: Geometry, side: Side, name: str):
    """Return (rects, feature_index, caption_rect) for one page composition."""
    comp = COMPOSITIONS[name]
    if comp.blank or comp.slots == 0:
        return [], None, None
    if comp.bleed:
        return [g.full_bleed(side)], 0, None
    rects = [grid_rect(g, side, c) for c in comp.cells]
    feature = next((i for i, c in enumerate(comp.cells) if c.feature), 0)
    return rects, feature, caption_band(g, side)
