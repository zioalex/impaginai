"""The fixed template library.

Templates are DATA, not CSS. Each one is a function from geometry to a list of
slot rectangles in canvas millimetres. The renderer positions boxes from these
numbers and the validator measures DPI and fold safety from the same numbers,
so the two can never disagree.

The planner model only ever emits a template NAME, a photo id, a normalised
focal point and a caption. It never sees this file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal

from geometry import Geometry, Rect

Aspect = Literal["landscape", "portrait", "square", "any"]
Side = Literal["left", "right"]


@dataclass
class SlotBox:
    rect: Rect
    aspect: Aspect = "any"
    caption_style: Literal["below", "overlay", "none"] = "below"
    caption_rect: Rect | None = None
    caption_align: Literal["left", "right", "center"] = "left"
    bleeds: bool = False


@dataclass
class TextBox:
    rect: Rect
    role: Literal["title", "subtitle", "body", "eyebrow", "folio"]
    align: Literal["left", "right", "center"] = "left"
    key: str = "body"          # which field of spread.text to render
    invert: bool = False       # white type on dark ground


@dataclass
class Rendered:
    slots: list[SlotBox] = field(default_factory=list)
    texts: list[TextBox] = field(default_factory=list)
    background: str = "paper"  # "paper" | "ink"
    chart: str | None = None   # calibration templates only


@dataclass
class TemplateSpec:
    name: str
    slot_count: int
    aspects: tuple[Aspect, ...]
    use: str
    build: Callable[[Geometry, dict], Rendered]
    spans_fold: bool = False
    planner_visible: bool = True   # calibration templates are hidden from the LLM


# --- helpers ------------------------------------------------------------

CAPTION_H = 9.0
CAPTION_GAP = 4.0


def _side(options: dict) -> Side:
    s = options.get("side", "right")
    return "left" if s == "left" else "right"


def _other(side: Side) -> Side:
    return "right" if side == "left" else "left"


def _caption_below(r: Rect, align="left") -> Rect:
    return Rect(r.x, r.y2 + CAPTION_GAP, r.w, CAPTION_H)


def _caption_overlay(g: Geometry, side: Side, where: str) -> Rect:
    s = g.safe(side)
    h = CAPTION_H
    y = s.y if where.startswith("top") else s.y2 - h
    return Rect(s.x, y, s.w, h)


# --- templates ----------------------------------------------------------

def t_full_bleed_single(g: Geometry, o: dict) -> Rendered:
    side = _side(o)
    r = g.full_bleed(side)
    return Rendered(
        slots=[
            SlotBox(
                rect=r,
                aspect="any",
                bleeds=True,
                caption_style="overlay",
                caption_rect=_caption_overlay(g, side, o.get("caption_position", "bottom-left")),
                caption_align="left",
            )
        ]
    )


def t_panorama_spread(g: Geometry, o: dict) -> Rendered:
    return Rendered(
        slots=[
            SlotBox(
                rect=g.spread_bleed,
                aspect="landscape",
                bleeds=True,
                caption_style="overlay",
                caption_rect=_caption_overlay(g, "right", o.get("caption_position", "bottom-right")),
                caption_align="right",
            )
        ]
    )


def t_single_centered(g: Geometry, o: dict) -> Rendered:
    side = _side(o)
    c = g.text_column(side)
    h = c.h - CAPTION_H - CAPTION_GAP
    r = Rect(c.x, c.y, c.w, h)
    return Rendered(slots=[SlotBox(r, "any", "below", _caption_below(r))])


def t_two_up_equal(g: Geometry, o: dict) -> Rendered:
    slots = []
    for side in ("left", "right"):
        c = g.text_column(side)
        h = c.h - CAPTION_H - CAPTION_GAP
        r = Rect(c.x, c.y, c.w, h)
        slots.append(SlotBox(r, "any", "below", _caption_below(r)))
    return Rendered(slots=slots)


def t_two_up_hero(g: Geometry, o: dict) -> Rendered:
    hero_side: Side = _side(o)
    small_side = _other(hero_side)
    hero = g.full_bleed(hero_side)
    c = g.text_column(small_side)
    sw = c.w * 0.66
    sh = sw * 0.75
    sx = c.x + (c.w - sw) / 2
    sy = c.y + (c.h - sh) / 2
    small = Rect(round(sx, 2), round(sy, 2), round(sw, 2), round(sh, 2))
    return Rendered(
        slots=[
            SlotBox(hero, "any", "none", bleeds=True),
            SlotBox(small, "any", "below", _caption_below(small)),
        ]
    )


def t_three_up_strip(g: Geometry, o: dict) -> Rendered:
    """Three images reading left to right, with the fold falling in a gap.

    The obvious construction — three equal boxes spread evenly across the whole
    opening — puts the middle image straight through the crease. Instead: two on
    the left page, one on the right, all on the same baseline and the same
    height. It still reads as a sequence; nothing is printed into the fold.
    """
    left_c = g.text_column("left")
    right_c = g.text_column("right")
    gap = 6.0
    w = (left_c.w - gap) / 2
    h = w * 0.78
    y = g.bleed + (g.page_h - h) / 2
    rects = [
        Rect(round(left_c.x, 2), round(y, 2), round(w, 2), round(h, 2)),
        Rect(round(left_c.x + w + gap, 2), round(y, 2), round(w, 2), round(h, 2)),
        Rect(round(right_c.x, 2), round(y, 2), round(right_c.w, 2), round(h, 2)),
    ]
    return Rendered(
        slots=[
            SlotBox(rects[0], "landscape", "none"),
            SlotBox(rects[1], "landscape", "none"),
            SlotBox(rects[2], "landscape", "below", _caption_below(rects[2])),
        ]
    )


def t_grid_four(g: Geometry, o: dict) -> Rendered:
    """A 2x2 grid reading across the whole opening: two cells per page.

    Slot order is reading order — top-left, top-right, bottom-left,
    bottom-right — so the planner's sequencing survives into the print.
    """
    gap = 6.0
    cells: dict[tuple[int, int], Rect] = {}
    for col, side in enumerate(("left", "right")):
        c = g.text_column(side)
        h = (c.h - gap) / 2
        for row in range(2):
            cells[(row, col)] = Rect(
                round(c.x, 2), round(c.y + row * (h + gap), 2), round(c.w, 2), round(h, 2)
            )
    order = [(0, 0), (0, 1), (1, 0), (1, 1)]
    return Rendered(slots=[SlotBox(cells[k], "any", "none") for k in order])


def t_photo_and_text(g: Geometry, o: dict) -> Rendered:
    photo_side: Side = _side(o)
    text_side = _other(photo_side)
    r = g.full_bleed(photo_side)
    c = g.text_column(text_side)
    body = Rect(c.x, round(c.y + c.h * 0.22, 2), round(c.w * 0.84, 2), round(c.h * 0.6, 2))
    eyebrow = Rect(c.x, round(c.y + c.h * 0.22 - 12, 2), c.w, 6)
    return Rendered(
        slots=[SlotBox(r, "any", "none", bleeds=True)],
        texts=[
            TextBox(eyebrow, "eyebrow", "left", key="eyebrow"),
            TextBox(body, "body", "left", key="body"),
        ],
    )


def t_section_divider(g: Geometry, o: dict) -> Rendered:
    """Solid ink spread with white type.

    Doubles as a dense-black reproduction test on the Fuji paper: any banding
    or bronzing shows up here first.
    """
    c = g.text_column("right")
    return Rendered(
        background="ink",
        texts=[
            TextBox(Rect(c.x, round(c.y + c.h * 0.42, 2), c.w, 8), "eyebrow", "left", key="eyebrow", invert=True),
            TextBox(Rect(c.x, round(c.y + c.h * 0.48, 2), c.w, 30), "title", "left", key="title", invert=True),
            TextBox(Rect(c.x, round(c.y + c.h * 0.62, 2), round(c.w * 0.8, 2), 40), "body", "left", key="body", invert=True),
        ],
    )


def t_text_page(g: Geometry, o: dict) -> Rendered:
    side = _side(o)
    c = g.text_column(side)
    return Rendered(
        texts=[
            TextBox(Rect(c.x, round(c.y + c.h * 0.30, 2), c.w, 8), "eyebrow", "left", key="eyebrow"),
            TextBox(Rect(c.x, round(c.y + c.h * 0.36, 2), c.w, 34), "title", "left", key="title"),
            TextBox(Rect(c.x, round(c.y + c.h * 0.50, 2), c.w, 14), "subtitle", "left", key="subtitle"),
            TextBox(Rect(c.x, round(c.y + c.h * 0.60, 2), round(c.w * 0.82, 2), 50), "body", "left", key="body"),
        ]
    )


# --- Phase 0 calibration templates (never offered to the planner) -------

def t_test_chart(g: Geometry, o: dict) -> Rendered:
    return Rendered(chart="test_chart")


def t_gutter_target(g: Geometry, o: dict) -> Rendered:
    return Rendered(chart="gutter_target")


REGISTRY: dict[str, TemplateSpec] = {
    t.name: t
    for t in [
        TemplateSpec("full-bleed-single", 1, ("any",), "hero image, one page", t_full_bleed_single),
        TemplateSpec("panorama-spread", 1, ("landscape",), "image across both pages", t_panorama_spread, spans_fold=True),
        TemplateSpec("single-centered", 1, ("any",), "image with generous white space + caption", t_single_centered),
        TemplateSpec("two-up-equal", 2, ("any", "any"), "pair, same weight", t_two_up_equal),
        TemplateSpec("two-up-hero", 2, ("any", "any"), "one large, one small", t_two_up_hero),
        TemplateSpec("three-up-strip", 3, ("landscape",) * 3, "horizontal sequence, two pictures then one", t_three_up_strip),
        TemplateSpec("grid-four", 4, ("any",) * 4, "detail shots, contact-sheet feel", t_grid_four),
        TemplateSpec("photo-and-text", 1, ("any",), "half image, half narrative block", t_photo_and_text),
        TemplateSpec("section-divider", 0, (), "title page between chapters", t_section_divider),
        TemplateSpec("text-page", 0, (), "intro or closing text", t_text_page),
        TemplateSpec("test-chart", 0, (), "PHASE 0 calibration chart", t_test_chart, planner_visible=False),
        TemplateSpec("gutter-target", 0, (), "PHASE 0 fold alignment target", t_gutter_target, planner_visible=False, spans_fold=True),
    ]
}


def build(name: str, g: Geometry, options: dict | None = None) -> Rendered:
    spec = REGISTRY[name]
    return spec.build(g, options or {})


def catalogue_for_prompt(allow_fold_crossing: bool = False) -> list[dict]:
    """What the planner is allowed to see.

    Templates that print a photograph across the fold are withheld entirely when
    the policy forbids it. Offering a template and then rejecting its use is a
    guaranteed retry; not offering it costs nothing.
    """
    return [
        {"template": s.name, "slots": s.slot_count, "aspects": list(s.aspects), "use": s.use}
        for s in REGISTRY.values()
        if s.planner_visible and (allow_fold_crossing or not s.spans_fold)
    ]
