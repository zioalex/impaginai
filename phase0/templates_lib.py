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

import compose
from geometry import Geometry, Rect

Aspect = Literal["landscape", "portrait", "square", "any"]
Side = Literal["left", "right"]


@dataclass
class SlotBox:
    rect: Rect
    aspect: Aspect = "any"
    caption_style: Literal["below", "overlay", "inset", "none"] = "below"
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
    # (left, right) page compositions for templates built by _pair, before the
    # `side` option flips them. None for templates that are not a simple pair
    # (title pages, dividers, calibration charts). The page repacker uses this
    # to take a book apart into pages and put it back together without blanks.
    pages: tuple[str, str] | None = None


# --- spread templates = a pair of page compositions ----------------------
#
# Every template below names a composition for the left page and one for the
# right (see compose.py). The slot order the planner fills is left page first,
# then right, each in reading order, so sequencing survives into print.
#
# Nothing crosses the fold. That is structural here, not a rule to remember:
# a composition is built inside one page's content area and cannot reach past it.

def _page_slots(g: Geometry, side: Side, comp_name: str, aspects=None, pixels=None) -> list[SlotBox]:
    rects, feature, cap = compose.build_page(g, side, comp_name, aspects, pixels)
    out: list[SlotBox] = []
    for i, r in enumerate(rects):
        is_feature = (i == feature)
        bleeds = compose.COMPOSITIONS[comp_name].bleed
        out.append(
            SlotBox(
                rect=r,
                aspect="any",
                # The feature's caption sits in the band beneath the page's
                # photographs; any other photograph that has one (the user
                # wrote it, so it is printed) carries it inset at its own foot,
                # on a scrim so it stays readable.
                caption_style=("overlay" if bleeds else ("below" if is_feature else "inset")),
                caption_rect=(None if bleeds else cap),
                caption_align="left",
                bleeds=bleeds,
            )
        )
    return out


def _pair(left: str, right: str):
    """Build a spread template from two page compositions."""
    def build(g: Geometry, o: dict) -> Rendered:
        # `side` flips which page gets which composition, so one template serves
        # both handednesses and the planner can vary the rhythm.
        a, b = (left, right) if o.get("side", "left") != "right" else (right, left)
        # `aspects`, when the layout carries it, is each photograph's own
        # width/height in slot order; pages are then laid out to fit them.
        asp = list(o.get("aspects") or [])
        na = compose.COMPOSITIONS[a].slots
        nb = compose.COMPOSITIONS[b].slots
        if len(asp) != na + nb:
            asp = []
        px = list(o.get("pixels") or [])
        if len(px) != len(asp):
            px = []
        slots = (_page_slots(g, "left", a, asp[:na] or None, px[:na] or None)
                 + _page_slots(g, "right", b, asp[na:] or None, px[na:] or None))
        texts = []
        for page_side, name in (("left", a), ("right", b)):
            if name == "text":
                c = g.text_column(page_side)
                texts += [
                    TextBox(Rect(c.x, round(c.y + c.h * 0.30, 2), c.w, 8), "eyebrow", key="eyebrow"),
                    TextBox(Rect(c.x, round(c.y + c.h * 0.36, 2), c.w, 34), "title", key="title"),
                    TextBox(Rect(c.x, round(c.y + c.h * 0.50, 2), c.w, 14), "subtitle", key="subtitle"),
                    TextBox(Rect(c.x, round(c.y + c.h * 0.60, 2), round(c.w * 0.82, 2), 50), "body", key="body"),
                ]
        return Rendered(slots=slots, texts=texts)
    build.pages = (left, right)
    return build


def t_photo_and_text(g: Geometry, o: dict) -> Rendered:
    photo_side: Side = "left" if o.get("side", "left") != "right" else "right"
    text_side: Side = "right" if photo_side == "left" else "left"
    c = g.text_column(text_side)
    body = Rect(c.x, round(c.y + c.h * 0.22, 2), round(c.w * 0.84, 2), round(c.h * 0.6, 2))
    eyebrow = Rect(c.x, round(c.y + c.h * 0.22 - 12, 2), c.w, 6)
    return Rendered(
        slots=_page_slots(g, photo_side, "bleed"),
        texts=[TextBox(eyebrow, "eyebrow", key="eyebrow"), TextBox(body, "body", key="body")],
    )


def _title_texts(g: Geometry, side: Side, invert: bool) -> list[TextBox]:
    c = g.text_column(side)
    return [
        TextBox(Rect(c.x, round(c.y + c.h * 0.42, 2), c.w, 8), "eyebrow", key="eyebrow", invert=invert),
        TextBox(Rect(c.x, round(c.y + c.h * 0.48, 2), c.w, 30), "title", key="title", invert=invert),
        TextBox(Rect(c.x, round(c.y + c.h * 0.62, 2), round(c.w * 0.8, 2), 40), "body", key="body", invert=invert),
    ]


def _text_and_photo(ink: bool):
    """A text page facing a bleeding photograph.

    The default for chapter openings and for the title. A divider that spends a
    whole spread on three words leaves two pages carrying nothing; pairing the
    text with the chapter's opening image costs no extra paper and gives the
    reader something to look at while being told where they are.
    """
    def build(g: Geometry, o: dict) -> Rendered:
        text_side: Side = "right" if o.get("side", "left") == "right" else "left"
        photo_side: Side = "left" if text_side == "right" else "right"
        return Rendered(
            slots=_page_slots(g, photo_side, "bleed"),
            texts=_title_texts(g, text_side, ink),
            background=("ink-" + text_side) if ink else "paper",
        )
    return build


def t_section_divider_plain(g: Geometry, o: dict) -> Rendered:
    """Solid ink spread, no photograph. A deliberate full stop, and the black
    density test in the calibration book."""
    return Rendered(background="ink", texts=_title_texts(g, "right", True))


def t_panorama_spread(g: Geometry, o: dict) -> Rendered:
    """Retained for the Phase 0 calibration book only; withheld from the planner
    unless the lab config permits fold crossing."""
    s = g.safe("right")
    return Rendered(slots=[SlotBox(g.spread_bleed, "landscape", "overlay",
                                   Rect(s.x, s.y2 - 9, s.w, 9), "right", bleeds=True)])


# --- Phase 0 calibration templates (never offered to the planner) -------


def t_test_chart(g: Geometry, o: dict) -> Rendered:
    return Rendered(chart="test_chart")


def t_gutter_target(g: Geometry, o: dict) -> Rendered:
    return Rendered(chart="gutter_target")


_LISTED = [
        # --- one photograph, given room -------------------------------
        TemplateSpec("full-bleed-single", 1, ("any",),
                     "one photograph to the edges, FACING PAGE LEFT EMPTY",
                     _pair("bleed", "blank")),
        TemplateSpec("single-centered", 1, ("any",),
                     "one photograph with white space, FACING PAGE LEFT EMPTY",
                     _pair("blank", "solo")),

        # --- one composed page, facing a quiet one --------------------
        TemplateSpec("duo-page", 2, ("any", "any"),
                     "two photographs composed on ONE page (large above wide), FACING PAGE LEFT EMPTY",
                     _pair("duo-stack", "blank")),
        TemplateSpec("trio-page", 3, ("any",) * 3,
                     "three photographs composed on ONE page (wide one over a pair), FACING PAGE LEFT EMPTY",
                     _pair("trio-feature", "blank")),
        TemplateSpec("grid-four", 4, ("any",) * 4,
                     "four photographs in a square grid on ONE page, FACING PAGE LEFT EMPTY",
                     _pair("quad", "blank")),

        # --- both pages carrying images -------------------------------
        TemplateSpec("two-up-equal", 2, ("any", "any"),
                     "one photograph on each page, same weight",
                     _pair("solo", "solo")),
        TemplateSpec("two-up-hero", 2, ("any", "any"),
                     "a bleeding photograph facing a smaller one",
                     _pair("bleed", "solo-air")),
        TemplateSpec("hero-and-pair", 3, ("any",) * 3,
                     "a bleeding photograph facing two stacked on the other page",
                     _pair("bleed", "duo-stack")),
        TemplateSpec("hero-and-trio", 4, ("any",) * 4,
                     "a bleeding photograph facing three composed on the other page",
                     _pair("bleed", "trio-feature")),
        TemplateSpec("solo-and-pair", 3, ("any",) * 3,
                     "one framed photograph facing a stepped pair",
                     _pair("solo", "duo-offset")),
        TemplateSpec("column-and-solo", 4, ("any",) * 4,
                     "a tall photograph beside two stacked, facing a single",
                     _pair("trio-column", "solo-air")),
        TemplateSpec("mosaic-spread", 6, ("any",) * 6,
                     "three composed on each page — the densest layout, use sparingly",
                     _pair("trio-feature", "trio-feature")),

        # --- text ------------------------------------------------------
        TemplateSpec("photo-and-text", 1, ("any",),
                     "a bleeding photograph facing a narrative block", t_photo_and_text),
        TemplateSpec("section-divider", 1, ("any",),
                     "chapter title on black facing the chapter's opening photograph",
                     _text_and_photo(ink=True)),
        TemplateSpec("title-page", 1, ("any",),
                     "the book's title facing its opening photograph",
                     _text_and_photo(ink=False)),
        TemplateSpec("section-divider-plain", 0, (),
                     "chapter title alone on black, both pages — a deliberate full stop, "
                     "COSTS TWO PAGES carrying no photograph",
                     t_section_divider_plain),
        TemplateSpec("text-page", 0, (),
                     "text alone, facing page empty — COSTS TWO PAGES carrying no photograph",
                     _pair("blank", "text")),

        # --- withheld --------------------------------------------------
        TemplateSpec("panorama-spread", 1, ("landscape",),
                     "image across both pages", t_panorama_spread, spans_fold=True),
        TemplateSpec("test-chart", 0, (), "PHASE 0 calibration chart", t_test_chart, planner_visible=False),
        TemplateSpec("gutter-target", 0, (), "PHASE 0 fold alignment target", t_gutter_target, planner_visible=False, spans_fold=True),
]

# Every pairing of two photo-carrying page compositions, as hidden templates.
# The planner never sees these; the page repacker (phase1/pages.py) uses them
# when it closes up the empty pages the planner left, so two half-empty spreads
# become one full one instead of two sheets of paper carrying nothing.
PAGE_COMPOSITIONS = tuple(n for n, c in compose.COMPOSITIONS.items() if c.slots > 0)


def pair_name(left: str, right: str) -> str:
    return f"pages:{left}+{right}"


_GENERATED = [
    TemplateSpec(pair_name(a, b),
                 compose.COMPOSITIONS[a].slots + compose.COMPOSITIONS[b].slots,
                 ("any",) * (compose.COMPOSITIONS[a].slots + compose.COMPOSITIONS[b].slots),
                 f"{a} facing {b} (generated by the page repacker)",
                 _pair(a, b), planner_visible=False)
    for a, b in [(a, b) for a in PAGE_COMPOSITIONS for b in PAGE_COMPOSITIONS]
    + [(a, "blank") for a in PAGE_COMPOSITIONS]
]

REGISTRY: dict[str, TemplateSpec] = {t.name: t for t in _LISTED + _GENERATED}
for _t in REGISTRY.values():
    _t.pages = getattr(_t.build, "pages", None)

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


def attach_aspects(spreads, dims: dict) -> int:
    """Record each photograph's width/height on its spread (options.aspects),
    so composed pages are laid out for the photographs they carry. `spreads`
    are dicts or Spread models; `dims` maps photo id to (width, height).
    Returns how many spreads were given aspects."""
    n = 0
    for sp in spreads:
        get = (lambda k, sp=sp: sp.get(k)) if isinstance(sp, dict) else (lambda k, sp=sp: getattr(sp, k))
        spec = REGISTRY.get(get("template"))
        if spec is None or not getattr(spec.build, "pages", None):
            continue
        slots = get("slots") or []
        ids = [s["photo_id"] if isinstance(s, dict) else s.photo_id for s in slots]
        if not ids or any(i not in dims for i in ids):
            continue
        asp = [round(dims[i][0] / dims[i][1], 4) for i in ids]
        pix = [int(dims[i][0]) for i in ids]
        opts = get("options")
        if opts is None:
            opts = {}
            if isinstance(sp, dict):
                sp["options"] = opts
            else:
                sp.options = opts
        opts["aspects"] = asp
        opts["pixels"] = pix
        n += 1
    return n
