"""Close up the empty pages the planner leaves, and stamp each location.

The planner thinks in spreads, and many of its templates spend one page of the
spread on nothing (FACING PAGE LEFT EMPTY), or on a chapter title alone. The
prompt asks it to ration those; it does not reliably do so, and a book with a
blank page every other sheet reads as unfinished. So this pass takes the book
apart into PAGES and puts it back together:

1. The opening spread is kept as it is when it is the title (title-page, a
   divider or a text page). It is the one page that is meant to be special.
2. Every other spread is split into its two page compositions. Blank pages and
   text-only pages are dropped. A chapter divider keeps its photograph as a
   full-bleed page; its title is not lost, it becomes the location stamp.
3. The remaining pages are paired again in order, two to a spread, using the
   catalogue template for that pair when one exists and a hidden generated one
   (`pages:<left>+<right>`) when not.
4. The place and date of every location group are stamped on the page that
   carries that group's first photograph — not on a page of their own.
5. If closing the gaps takes the book below the lab's minimum, composed pages
   are split into lighter ones (a page of three becomes a page of one and a
   page of two) before any blank paper is added back.

It runs after the user's overrides on every version, like them, so a revision
cannot bring the empty pages back.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "phase0"))

import compose  # noqa: E402
import templates_lib as T  # noqa: E402

OPENING = ("title-page", "section-divider", "section-divider-plain", "text-page",
           "photo-and-text")

# How to make one composed page into two lighter ones when the book is short.
# (first page, second page): the feature keeps the first.
SPLITS = {
    "quad": ("duo-stack", "duo-stack"),
    "trio-feature": ("solo", "duo-stack"),
    "trio-column": ("solo", "duo-stack"),
    "duo-stack": ("solo", "solo-air"),
    "duo-offset": ("solo", "solo-air"),
}


SINGLES = ("solo", "solo-air")


@dataclass
class Page:
    comp: str
    slots: list[dict]
    section_id: str | None = None
    stamp: str | None = None
    note: str | None = None
    caption_fallback: dict = field(default_factory=dict)


def _pages_of(sp: dict) -> list[Page]:
    """The photo-carrying pages of one spread, in reading order."""
    spec = T.REGISTRY.get(sp["template"])
    slots = list(sp.get("slots") or [])
    sec = sp.get("section_id")
    note = sp.get("note")
    if spec is None:
        return []
    if spec.pages:
        a, b = spec.pages
        if (sp.get("options") or {}).get("side", "left") == "right":
            a, b = b, a
        n = compose.COMPOSITIONS[a].slots
        out = []
        for comp, ss in ((a, slots[:n]), (b, slots[n:])):
            if compose.COMPOSITIONS[comp].slots and ss:
                out.append(Page(comp, ss, sec, note=note))
        return out
    # Text-and-photo templates: keep the photograph as a full-bleed page.
    # The chapter title is carried as a stamp, so it stays on the photograph.
    text = sp.get("text") or {}
    title = text.get("eyebrow") or text.get("title")
    return [Page("bleed", [s], sec, stamp=title, note=note) for s in slots[:1]]


def _template_for(a: str, b: str) -> tuple[str, dict]:
    """Prefer a catalogue name for the pair, fall back to a generated one."""
    for spec in T.REGISTRY.values():
        if not spec.planner_visible or not spec.pages:
            continue
        if spec.pages == (a, b):
            return spec.name, {"side": "left"}
        if spec.pages == (b, a):
            return spec.name, {"side": "right"}
    return T.pair_name(a, b), {"side": "left"}


def _blank_pair(page: Page) -> tuple[str, dict]:
    """A lone last page: on the left, the right-hand page left empty."""
    for spec in T.REGISTRY.values():
        if spec.planner_visible and spec.pages == (page.comp, "blank"):
            return spec.name, {"side": "left"}
    return T.pair_name(page.comp, "blank"), {"side": "left"}


def _split_one(pages: list[Page]) -> bool:
    """Split the densest composed page into two. False when none can split."""
    best = None
    for i, p in enumerate(pages):
        if p.comp in SPLITS and (best is None or len(p.slots) > len(pages[best].slots)):
            best = i
    if best is None:
        return False
    p = pages[best]
    a, b = SPLITS[p.comp]
    n = compose.COMPOSITIONS[a].slots
    first = Page(a, p.slots[:n], p.section_id, p.stamp, p.note)
    second = Page(b, p.slots[n:], p.section_id, None, p.note)
    pages[best:best + 1] = [first, second]
    return True


# One photograph fewer on a composed page.
LIGHTER = {"quad": "trio-feature", "trio-feature": "duo-stack", "trio-column": "duo-stack",
           "duo-stack": "solo", "duo-offset": "solo"}


def _take_out(pages: list[Page], pid: str) -> None:
    """Remove a photograph from every page, lightening or dropping the page."""
    for i in range(len(pages) - 1, -1, -1):
        p = pages[i]
        if not any(s["photo_id"] == pid for s in p.slots):
            continue
        rest = [s for s in p.slots if s["photo_id"] != pid]
        comp = p.comp
        while rest and compose.COMPOSITIONS[comp].slots > len(rest) and comp in LIGHTER:
            comp = LIGHTER[comp]
        if not rest or compose.COMPOSITIONS[comp].slots != len(rest):
            if p.stamp and i + 1 < len(pages) and not pages[i + 1].stamp:
                pages[i + 1].stamp = p.stamp
            pages.pop(i)
        else:
            p.comp, p.slots = comp, rest


def apply_focal(layout: dict, photos_by_id: dict) -> int:
    """Use the focal point the vision pass measured, not the planner's guess.

    The planner chooses a focal point for every slot whether or not it has
    seen the photograph; the annotation pass looked at it. Where the two
    disagree, the one that looked wins.
    """
    def valid(f) -> bool:
        try:
            return len(f) == 2 and all(0.0 <= float(c) <= 1.0 for c in f)
        except (TypeError, ValueError):
            return False

    n = 0
    for sp in layout.get("spreads", []):
        for s in sp.get("slots") or []:
            ph = photos_by_id.get(s["photo_id"])
            f = getattr(ph, "suggested_focal", None) if ph is not None else None
            # Only a focal point inside the frame replaces the planner's: a
            # bad annotation must never break a layout that validated.
            if f and valid(f) and tuple(s.get("focal") or ()) != tuple(f):
                s["focal"] = [float(f[0]), float(f[1])]
                n += 1
    cov = layout.get("cover") or {}
    ph = photos_by_id.get(cov.get("front_photo_id"))
    f = getattr(ph, "suggested_focal", None) if ph is not None else None
    if f and valid(f):
        cov["front_focal"] = [float(f[0]), float(f[1])]
    return n


def repack(layout: dict, groups, lab: dict | None = None, spare=None,
           taken: dict | None = None, front: str | None = None,
           strength: dict | None = None) -> list[str]:
    """Rebuild the spreads with no empty pages. Returns a log.

    `spare` lists photo ids the layout left out that may still be used, in
    chronological order. They are only brought in when the book would
    otherwise fall below the lab minimum and be padded with blank paper.
    `taken` maps photo id to a sortable time; with it, the photographs of one
    place on one page are put in the order they were taken (the feature stays
    first).

    The opening is always a front page that carries a photograph: `front` is
    the one the user chose; without it, the opening's own photograph, else the
    strongest one in the book (`strength` maps id to 1-5). The photograph is
    taken out of wherever else it was placed, so it is not printed twice."""
    spreads = layout.get("spreads", [])
    if not spreads:
        return []
    pages_cfg = (lab or {}).get("pages", {})
    min_pages = int(pages_cfg.get("min", 0))
    inc = int(pages_cfg.get("increment", 2)) or 2

    before_pages = len(spreads) * 2
    opening = spreads[0] if spreads[0]["template"] in OPENING else None
    rest = spreads[1:] if opening else spreads

    pages: list[Page] = []
    for sp in rest:
        pages.extend(_pages_of(sp))

    # The front page. An opening without a photograph prints a title alone on
    # black or white paper — the empty first spread users kept seeing.
    meta = layout.get("meta") or {}
    own = [s["photo_id"] for s in (opening or {}).get("slots") or []]
    placed = [s["photo_id"] for p in pages for s in p.slots]
    candidates = placed + [x for x in (spare or []) if x not in placed]
    pick = None
    if front and (front in own or front in candidates):
        pick = front
    elif own:
        pick = own[0]
    elif candidates:
        pick = max(candidates, key=lambda x: ((strength or {}).get(x) or 0, -candidates.index(x)))
    if pick is not None:
        old_text = dict((opening or {}).get("text") or {})
        _take_out(pages, pick)
        # Keep the slot as it was (focal point, the user's caption, edge).
        slot = next((dict(s) for sp in spreads for s in sp.get("slots") or []
                     if s["photo_id"] == pick), None) or {
            "photo_id": pick, "focal": [0.5, 0.5], "caption": None,
            "caption_position": "bottom-left"}
        opening = {
            "index": 0, "section_id": (opening or {}).get("section_id"),
            # Title in white on black facing the photograph to the edges: no
            # page of the opening spread is blank paper.
            "template": "section-divider",
            "slots": [slot],
            "text": {"eyebrow": old_text.get("eyebrow"),
                     "title": meta.get("title") or old_text.get("title"),
                     "subtitle": None,
                     "body": meta.get("subtitle") or old_text.get("body")},
            "options": {"side": "left"},
            "note": "front page",
        }

    # Location stamps: the first page carrying each group's first photograph.
    by_photo = {pid: g for g in (groups or []) for pid in g.photo_ids}
    seen: set[str] = set()
    if opening:
        for s in opening.get("slots") or []:
            g = by_photo.get(s["photo_id"])
            if g is not None:
                seen.add(g.id)
        # The opening keeps its own eyebrow for the first location.
        g0 = None
        for s in opening.get("slots") or []:
            g0 = by_photo.get(s["photo_id"])
        if g0 is None and pages:
            g0 = by_photo.get(pages[0].slots[0]["photo_id"])
        # The title page always carries the book's title, even when the
        # model left the text block empty.
        t = dict(opening.get("text") or {})
        if not t.get("title") and (layout.get("meta") or {}).get("title"):
            t["title"] = layout["meta"]["title"]
            opening["text"] = t
        if g0 is not None:
            line = " · ".join(x for x in (g0.place, g0.date_label) if x)
            if line:
                opening["text"] = dict(opening.get("text") or {})
                opening["text"]["eyebrow"] = line
            seen.add(g0.id)
    for p in pages:
        stamp = None
        for s in p.slots:
            g = by_photo.get(s["photo_id"])
            if g is not None and g.id not in seen:
                seen.add(g.id)
                line = " · ".join(x for x in (g.place, g.date_label) if x)
                stamp = stamp or line or None
        if stamp:
            p.stamp = stamp
        elif p.stamp and by_photo:
            # A divider title that does not open a new location adds nothing.
            p.stamp = None

    # Never below the lab minimum, and a whole number of increments.
    def total(n_pages: int) -> int:
        return (2 if opening else 0) + 2 * ((n_pages + 1) // 2)

    # Two framed single photographs of the same moment, one after the other,
    # make one composed page: the book uses its paper instead of setting one
    # small picture in a field of white on every page. Full-bleed pages are
    # left alone — those are meant to be big.
    i = 0
    while i + 1 < len(pages):
        a, b = pages[i], pages[i + 1]
        ga = by_photo.get(a.slots[0]["photo_id"]) if a.slots else None
        gb = by_photo.get(b.slots[0]["photo_id"]) if b.slots else None
        if (a.comp in SINGLES and b.comp in SINGLES and not b.stamp
                and (ga is gb) and total(len(pages) - 1) >= min_pages):
            pages[i:i + 2] = [Page("duo-stack", a.slots + b.slots, a.section_id,
                                   a.stamp, a.note)]
        i += 1

    while (total(len(pages)) < min_pages or total(len(pages)) % inc
           or len(pages) % 2) and _split_one(pages):
        pass

    # Still short: rather than pad with blank paper, bring in the photographs
    # the planner left out, each on its own page right after its place's run.
    added = []
    pool = [pid for pid in (spare or [])
            if pid != pick and pid not in {s["photo_id"] for p in pages for s in p.slots}]
    while pool and total(len(pages)) < min_pages:
        pid = pool.pop(0)
        g = by_photo.get(pid)
        at = len(pages)
        if g is not None:
            same = [i for i, p in enumerate(pages)
                    if any(by_photo.get(s["photo_id"]) is g for s in p.slots)]
            if same:
                at = same[-1] + 1
        sec = pages[at - 1].section_id if at > 0 and pages else None
        pages.insert(at, Page("solo", [{"photo_id": pid, "focal": [0.5, 0.5], "caption": None,
                                        "caption_position": "below"}], sec))
        added.append(pid)
    while (total(len(pages)) % inc or len(pages) % 2) and _split_one(pages):
        pass

    if taken:
        for p in pages:
            if len(p.slots) > 2:
                rest = sorted(p.slots[1:], key=lambda s: taken.get(s["photo_id"]) or "")
                p.slots[1:] = rest

    out: list[dict] = [opening] if opening else []
    for i in range(0, len(pages), 2):
        pair = pages[i:i + 2]
        if len(pair) == 2:
            name, opts = _template_for(pair[0].comp, pair[1].comp)
        else:
            name, opts = _blank_pair(pair[0])
        stamps = {}
        a, b = (pair + [None])[:2]
        if len(pair) == 2:
            if a.stamp:
                stamps["left"] = a.stamp
            if b.stamp:
                stamps["right"] = b.stamp
        elif a.stamp:
            stamps["left"] = a.stamp
        if stamps:
            opts["stamps"] = stamps
        slots = [s for p in pair for s in p.slots]
        out.append({
            "index": 0,
            "section_id": pair[0].section_id,
            "template": name,
            "slots": slots,
            "text": None,
            "options": opts,
            "note": pair[0].note,
        })

    # Still short (nothing left to split): pad with photograph-free spreads at
    # the end, where they read as endpapers rather than gaps.
    while len(out) * 2 < min_pages or (len(out) * 2) % inc:
        out.append({"index": 0, "section_id": None, "template": "text-page", "slots": [],
                    "text": None, "options": {}, "note": "padding to the lab minimum"})

    for i, sp in enumerate(out, 1):
        sp["index"] = i
    layout["spreads"] = out
    layout.setdefault("meta", {})["spread_count"] = len(out)
    lone = sum(1 for sp in out if T.REGISTRY[sp["template"]].pages
               and "blank" in T.REGISTRY[sp["template"]].pages)
    log = [f"repacked {before_pages} pages into {len(out) * 2}"
           + (f", {lone} page(s) left empty" if lone else ", no empty pages")]
    if added:
        log.append(f"{len(added)} photograph(s) the design left out brought in to reach "
                   f"{min_pages} pages: {', '.join(added)}")
    return log
