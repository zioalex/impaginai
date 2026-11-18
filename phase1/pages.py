# SPDX-FileCopyrightText: 2026 Alessandro Surace
# SPDX-License-Identifier: AGPL-3.0-only
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

# A photograph the user set across both pages (`size: spread`). It travels
# through the repacker as one page that is two pages wide: never split, merged
# with another or moved off its place, and always laid out as a spread of its
# own (templates_lib.USER_SPREAD).
SPREAD = "spread"


@dataclass
class Page:
    comp: str
    slots: list[dict]
    section_id: str | None = None
    stamp: str | None = None
    note: str | None = None
    caption_fallback: dict = field(default_factory=dict)
    similar: bool = False        # photographs of one scene, composed on purpose (keep_composed)


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
    if sp["template"] == T.USER_SPREAD:
        return [Page(SPREAD, slots[:1], sec, note=note)] if slots else []
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


# The last resort before an empty page: a composed page gives up one
# photograph and stays composed as long as it can.
SPLITS_GENTLE = {"quad": ("trio-feature", "solo"),
                 "trio-feature": ("duo-stack", "solo"),
                 "trio-column": ("duo-stack", "solo")}


def _split_gentle(pages: list[Page], together: set[str], comps, floor: int) -> bool:
    """Take one photograph off a page of three or four (`comps`), even one of
    similar photographs; a page of three only while more than `floor` pages of
    three or four remain. Pages holding a photograph the user wants composed
    are left alone. False when no page qualifies."""
    big = sum(1 for p in pages if len(p.slots) >= 3)
    for i, p in enumerate(pages):
        if p.comp not in comps or p.comp not in SPLITS_GENTLE or len(p.slots) < 3:
            continue
        if any(s["photo_id"] in together for s in p.slots):
            continue
        a, b = SPLITS_GENTLE[p.comp]
        n = compose.COMPOSITIONS[a].slots
        if len(p.slots) != n + compose.COMPOSITIONS[b].slots:
            continue
        if n < 3 and big <= floor:
            continue
        pages[i:i + 1] = [Page(a, p.slots[:n], p.section_id, p.stamp, p.note,
                               similar=p.similar and n >= 3),
                          Page(b, p.slots[n:], p.section_id, None, p.note)]
        return True
    return False


def _protected(p: Page, together: set[str]) -> bool:
    """A page that was composed on purpose (`keep_composed`): made by
    `_group_similar`, or holding a photograph the user marked "in a
    composition". The repacker never takes it apart."""
    return p.similar or any(s["photo_id"] in together for s in p.slots)


def _split_one(pages: list[Page], together: set[str] = frozenset(), floor: int = 0,
               max_slots: int = 4) -> bool:
    """Split one composed page into two lighter ones. False when none can.

    The lightest composed page goes first: pairs and singles give way before
    a trio or a quad is broken, so the book keeps its compositions. Never
    split: a page composed on purpose (`_protected`), one whose split would
    leave a photograph the user wants composed (`together`) alone, and, while
    `floor` pages of three or four photographs are all the book has, those.
    `max_slots` limits the pages considered by their photograph count."""
    def rule(p: Page):
        return SPLITS.get(p.comp)

    def ok(p: Page) -> bool:
        if rule(p) is None or p.comp == SPREAD or _protected(p, together):
            return False
        if len(p.slots) > max_slots:
            return False
        n = compose.COMPOSITIONS[rule(p)[0]].slots
        for part in (p.slots[:n], p.slots[n:]):
            if len(part) == 1 and part[0]["photo_id"] in together:
                return False
        return True

    big = sum(1 for p in pages if len(p.slots) >= 3)
    best = None
    for i, p in enumerate(pages):
        if not ok(p) or (len(p.slots) >= 3 and big <= floor):
            continue
        if best is None or len(p.slots) < len(pages[best].slots):
            best = i
    if best is None:
        return False
    p = pages[best]
    a, b = rule(p)
    n = compose.COMPOSITIONS[a].slots
    first = Page(a, p.slots[:n], p.section_id, p.stamp, p.note)
    second = Page(b, p.slots[n:], p.section_id, None, p.note)
    pages[best:best + 1] = [first, second]
    return True


# One photograph fewer on a composed page.
LIGHTER = {"quad": "trio-feature", "trio-feature": "duo-stack", "trio-column": "duo-stack",
           "duo-stack": "solo", "duo-offset": "solo"}


# One photograph more on a page.
HEAVIER = {"bleed": "duo-stack", "solo": "duo-stack", "solo-air": "duo-stack",
           "duo-stack": "trio-feature", "duo-offset": "trio-feature",
           "trio-feature": "quad", "trio-column": "quad"}


def _compose_flagged(pages: list[Page], together: set[str], by_photo: dict,
                     alone: set[str] = frozenset()) -> list[str]:  # noqa: C901
    """Put every photograph the user marked "in a composition" on a page with
    others: a page carrying it alone is folded into the neighbouring page,
    preferring one of the same place and moment, then the lighter one. This
    wins over the page count; the caller splits other pages to make it up.

    Any neighbouring photograph may be composed with it unless the user's own
    settings for that photograph say otherwise: one marked full-page (`alone`)
    keeps its page."""
    moved = []
    # Three or four marked photographs next to each other make one trio or
    # quad directly, not a pair that a neighbour then grows by one.
    i = 0
    while i < len(pages):
        run, n = [], 0
        while (i + len(run) < len(pages)):
            q = pages[i + len(run)]
            if (q.comp not in GROUPABLE and q.comp != "bleed") or not q.slots \
                    or any(s["photo_id"] not in together or s["photo_id"] in alone for s in q.slots) \
                    or n + len(q.slots) > 4:
                break
            run.append(q)
            n += len(q.slots)
        if n >= 3 and len(run) > 1:
            slots = [s for q in run for s in q.slots]
            pages[i:i + len(run)] = [Page("trio-feature" if n == 3 else "quad", slots,
                                          run[0].section_id,
                                          next((q.stamp for q in run if q.stamp), None),
                                          run[0].note)]
            moved += [s["photo_id"] for s in slots]
        i += 1
    i = 0
    while i < len(pages):
        p = pages[i]
        if len(p.slots) != 1 or p.slots[0]["photo_id"] not in together:
            i += 1
            continue
        g = by_photo.get(p.slots[0]["photo_id"])
        options = []
        for j in (i - 1, i + 1):
            if (0 <= j < len(pages) and pages[j].comp in HEAVIER and pages[j].slots
                    and not any(s["photo_id"] in alone for s in pages[j].slots)):
                q = pages[j]
                same = any(by_photo.get(s["photo_id"]) is g for s in q.slots) if g else False
                options.append((not same, len(q.slots), j != i - 1, j))
        if not options:
            i += 1
            continue
        j = min(options)[3]
        q = pages[j]
        # Reading order is kept: the earlier page's photographs come first,
        # so the neighbour's feature stays the feature unless it came later.
        slots = q.slots + p.slots if j < i else p.slots + q.slots
        merged = Page(HEAVIER[q.comp], slots, q.section_id if j < i else p.section_id,
                      (q.stamp if j < i else p.stamp) or (p.stamp if j < i else q.stamp),
                      q.note)
        lo = min(i, j)
        pages[lo:lo + 2] = [merged]
        moved.append(p.slots[0]["photo_id"])
        i = lo  # the merged page may itself hold another flagged single
        if len(merged.slots) == 1:
            i += 1
    return moved


# Pages that may be folded into a page of similar photographs. Full-bleed
# pages are left alone: those are meant to be big.
GROUPABLE = {"solo", "solo-air", "duo-stack", "duo-offset", "trio-feature", "trio-column"}
SIMILAR_WITHIN_S = 45 * 60
_STOP = {"with", "from", "over", "under", "near", "into", "their", "there", "this", "that",
         "photo", "photograph", "view", "shot", "scene", "image", "some", "other", "very"}


def _words(text: str) -> set[str]:
    import re as _re

    return {w for w in _re.findall(r"[a-zà-ÿ]{4,}", (text or "").lower()) if w not in _STOP}


def similar(a: str, b: str, features: dict, by_photo: dict, taken: dict) -> bool:
    """Two photographs of the same scene: the same place and moment (group),
    and the vision pass saw the same kind of scene, a shared subject, or the
    same people. (They used to need to be within 45 minutes of each other too;
    a group is already one place on one stretch of the trip, and the time
    limit kept almost every trio from forming.)"""
    ga, gb = by_photo.get(a), by_photo.get(b)
    if ga is None or ga is not gb:
        return False
    fa, fb = features.get(a) or {}, features.get(b) or {}
    if fa.get("who") and fa.get("who") == fb.get("who"):
        return True
    if _words(fa.get("subject", "")) & _words(fb.get("subject", "")):
        return True
    return bool(fa.get("scene")) and fa.get("scene") == fb.get("scene")


def _group_similar(pages: list[Page], features: dict, by_photo: dict, taken: dict,
                   strength: dict, alone: set[str], room) -> list[int]:
    """Fold runs of pages showing the same scene into one page of three or
    four photographs, the strongest as the feature. `room(n)` says whether a
    book of n pages is still long enough. Returns where pages were made."""
    made = []
    i = 0
    while i < len(pages):
        p = pages[i]
        ids = [s["photo_id"] for s in p.slots]
        if p.comp not in GROUPABLE or any(x in alone for x in ids):
            i += 1
            continue
        j = i + 1
        while j < len(pages):
            q = pages[j]
            qids = [s["photo_id"] for s in q.slots]
            if (q.comp not in GROUPABLE or q.stamp or len(ids) + len(qids) > 4
                    or any(x in alone for x in qids)
                    or not all(similar(x, y, features, by_photo, taken)
                               for x in ids for y in qids)):
                break
            ids += qids
            j += 1
        if j - i > 1 and len(ids) >= 3 and room(len(pages) - (j - i - 1)):
            slots = [s for pg in pages[i:j] for s in pg.slots]
            slots.sort(key=lambda s: (-(strength.get(s["photo_id"]) or 0),
                                      taken.get(s["photo_id"]) or ""))
            pages[i:j] = [Page("trio-feature" if len(slots) == 3 else "quad", slots,
                               p.section_id, p.stamp, p.note, similar=True)]
            made.append(i)
        i += 1
    return made


def fallback_keep(focal, people: int) -> list[float] | None:
    """A keep-box for a photograph the vision pass saw people in but drew no
    box around: centred on its focal point, sized by how many people there
    are (one person is a narrow tall box, a group a wide one). Without it such
    a photograph is cropped by its focal point alone and the person at the
    edge of the frame is cut in half."""
    if not people or people < 1:
        return None
    try:
        fx, fy = float(focal[0]), float(focal[1])
    except (TypeError, ValueError, IndexError):
        fx, fy = 0.5, 0.5
    w = min(0.95, 0.28 + 0.14 * people)
    h = min(0.9, 0.5 + 0.1 * people)
    x0 = min(max(fx - w / 2, 0.0), 1.0 - w)
    y0 = min(max(fy - h / 2, 0.0), 1.0 - h)
    return [round(x0, 3), round(y0, 3), round(x0 + w, 3), round(y0 + h, 3)]


def derive_keeps(photos, keeps: dict | None = None) -> dict:
    """Every photograph's keep-box: the vision pass's own where it drew one,
    else `fallback_keep` for a photograph with people in it."""
    out = dict(keeps or {})
    for p in photos:
        if p.id in out and out[p.id]:
            continue
        box = getattr(p, "keep", None) or fallback_keep(
            getattr(p, "suggested_focal", None) or (0.5, 0.5), getattr(p, "people", 0) or 0)
        if box:
            out[p.id] = box
    return out


def protect_subjects(layout: dict, keeps: dict, aspects: dict, page_aspect: float) -> list[str]:
    """A photograph set to the edges is cropped to the page's shape. When that
    crop would cut the people or the main subject (its keep-box is wider or
    taller than what stays visible), the page holds it inside the margins
    instead — whole, never cut."""
    notes = []
    for sp in layout.get("spreads", []):
        name = sp.get("template", "")
        spec = T.REGISTRY.get(name)
        if spec is None or not spec.pages or "bleed" not in spec.pages:
            continue
        a, b = spec.pages
        opts = sp.get("options") or {}
        right_first = opts.get("side", "left") == "right" and not name.startswith("front")
        left, right = (b, a) if right_first else (a, b)
        n_left = compose.COMPOSITIONS[left].slots
        changed = False
        for side, comp in (("left", left), ("right", right)):
            if comp != "bleed":
                continue
            idx = 0 if side == "left" else n_left
            slots = sp.get("slots") or []
            if idx >= len(slots):
                continue
            pid = slots[idx]["photo_id"]
            box, asp = keeps.get(pid), aspects.get(pid)
            if not box or not asp:
                continue
            vx, vy = (page_aspect / asp, 1.0) if asp > page_aspect else (1.0, asp / page_aspect)
            if box[2] - box[0] <= vx + 0.01 and box[3] - box[1] <= vy + 0.01:
                continue
            if side == "left":
                left = "solo"
            else:
                right = "solo"
            changed = True
            notes.append(f"{pid}: kept inside the margins so its subject is not cut")
        if not changed:
            continue
        if name.startswith("front:"):
            sp["template"] = T.front_framed_name(right)
            continue
        new, o = _template_for(left, right)
        sp["template"] = new
        sp["options"] = {**opts, **o}
    return notes


def attach_keeps(layout: dict, keeps: dict) -> None:
    """Each slot's keep-box, for the renderer (options.keeps, in slot order)."""
    for sp in layout.get("spreads", []):
        slots = sp.get("slots") or []
        if not slots:
            continue
        ks = [keeps.get(s["photo_id"]) for s in slots]
        if any(ks):
            sp.setdefault("options", {})["keeps"] = ks
        else:
            (sp.get("options") or {}).pop("keeps", None)


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
           strength: dict | None = None, together: set[str] | None = None,
           alone: set[str] | None = None, no_bleed: set[str] | None = None,
           density: str = "balanced", use_all: bool = False, place_names: bool = True,
           split_short: bool = True, include: set[str] | None = None,
           features: dict | None = None) -> list[str]:
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
    taken out of wherever else it was placed, so it is not printed twice.

    The front photograph fills page 1 to the edges with the title set on it;
    page 2 is the first ordinary page of the book. No page of the opening is
    title on black.

    `together` lists photographs the user wants on a page with others: they
    are brought into the book if the design left them out, never left alone
    on a page, and merging them wins over the lab's page count. Photographs
    in `alone` (marked full-page) are never composed with them.

    A front photograph in `no_bleed` (too few pixels to fill a page) is set
    inside the margins with the title beneath it instead of to the edges.

    The editor's settings: `density` "airy" never folds single photographs
    together, "dense" also folds a single into a neighbouring pair of the same
    moment; `use_all` brings every photograph the design left out into the
    book; `place_names` False prints no place names; `split_short` False keeps the
    compositions and pads a short book with blank pages instead.

    `include` lists photographs that must be in the book although the design
    left them out — the ones added after the model last saw the album.

    `features` maps photo id to what the vision pass saw ({"scene", "subject",
    "who"}): runs of three or four photographs of the same scene, moment and
    place are then composed on one page."""
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
    if spreads[0]["template"].startswith(("front:", "front-framed:")):
        # A revision of a finished layout: the front photograph stays the
        # front, its facing page goes back into the book.
        sp0 = spreads[0]
        right = sp0["template"].split(":", 1)[1]
        opening = {**sp0, "slots": list(sp0.get("slots") or [])[:1]}
        if compose.COMPOSITIONS.get(right) and compose.COMPOSITIONS[right].slots:
            pages.append(Page(right, list(sp0.get("slots") or [])[1:], sp0.get("section_id")))
        rest = spreads[1:]
    for sp in rest:
        pages.extend(_pages_of(sp))

    # The front page. An opening without a photograph prints a title alone on
    # black or white paper — the empty first spread users kept seeing.
    meta = layout.get("meta") or {}
    front_page = False
    together = set(together or ()) - ({front} if front else set())
    own = [s["photo_id"] for s in (opening or {}).get("slots") or []]
    placed = [s["photo_id"] for p in pages if p.comp != SPREAD for s in p.slots]
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
        # The title is set at the foot of this photograph; its caption (the
        # user's words are kept) goes to the top, where the two cannot meet.
        slot["caption_position"] = "top-left" if slot.get("caption") else "none"
        front_page = True
        opening = {
            "index": 0, "section_id": (opening or {}).get("section_id"),
            # Assembled with the first page of the book further down: the
            # photograph fills page 1 with the title on it, page 2 is photos.
            "template": "section-divider",
            "slots": [slot],
            "text": {"eyebrow": old_text.get("eyebrow"),
                     "title": meta.get("title") or old_text.get("title"),
                     "subtitle": None,
                     "body": meta.get("subtitle") or old_text.get("body")},
            "options": {"side": "left"},
            "note": "front page",
        }

    by_photo = {pid: g for g in (groups or []) for pid in g.photo_ids}

    # Within one place, pages run in the order they were taken: the planner
    # orders by feeling and often swaps two frames minutes apart. The groups
    # themselves stay in the planner's order.
    if taken:
        def first_time(p: Page):
            ts = [taken.get(s["photo_id"]) for s in p.slots]
            ts = [t for t in ts if t]
            return min(ts) if ts else None

        i = 0
        while i < len(pages):
            g = by_photo.get(pages[i].slots[0]["photo_id"]) if pages[i].slots else None
            j = i + 1
            while (g is not None and j < len(pages) and pages[j].slots
                   and by_photo.get(pages[j].slots[0]["photo_id"]) is g):
                j += 1
            run = pages[i:j]
            if len(run) > 1 and all(first_time(p) for p in run):
                pages[i:j] = sorted(run, key=first_time)
            i = j

    # Location stamps: the first page carrying each group's first photograph.
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
    if not place_names:
        for p in pages:
            p.stamp = None
        if opening and opening.get("text"):
            opening["text"] = {**opening["text"], "eyebrow": None}

    # Never below the lab minimum, and a whole number of increments. The
    # front page is one page: the first ordinary page faces it.
    lead = 1 if front_page else 0

    def npg() -> int:
        """Pages in the list; a two-page spread counts for two."""
        return len(pages) + sum(1 for p in pages if p.comp == SPREAD)

    def total(n_pages: int) -> int:
        if front_page:
            return 2 * ((n_pages + lead + 1) // 2)
        return (2 if opening else 0) + 2 * ((n_pages + 1) // 2)

    def odd(n_pages: int) -> bool:
        return bool((n_pages + lead) % 2)

    def insert_solo(pid: str) -> None:
        """A page of its own right after its place's run of pages."""
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

    # A photograph the user wants composed is in the book, whatever the
    # design decided.
    on_pages = {s["photo_id"] for p in pages for s in p.slots}
    brought = [pid for pid in (spare or [])
               if (use_all or pid in together or pid in (include or ()))
               and pid not in on_pages and pid != pick]
    for pid in brought:
        insert_solo(pid)

    # Two framed single photographs of the same moment, one after the other,
    # make one composed page: the book uses its paper instead of setting one
    # small picture in a field of white on every page. Full-bleed pages are
    # left alone — those are meant to be big.
    i = 0
    while density != "airy" and i + 1 < len(pages):
        a, b = pages[i], pages[i + 1]
        ga = by_photo.get(a.slots[0]["photo_id"]) if a.slots else None
        gb = by_photo.get(b.slots[0]["photo_id"]) if b.slots else None
        if (a.comp in SINGLES and b.comp in SINGLES and not b.stamp
                and (ga is gb) and total(npg() - 1) >= min_pages):
            pages[i:i + 2] = [Page("duo-stack", a.slots + b.slots, a.section_id,
                                   a.stamp, a.note)]
        i += 1

    # Dense: a single photograph next to a pair of the same moment joins it.
    i = 0
    while density == "dense" and i + 1 < len(pages):
        a, b = pages[i], pages[i + 1]
        ga = by_photo.get(a.slots[0]["photo_id"]) if a.slots else None
        gb = by_photo.get(b.slots[0]["photo_id"]) if b.slots else None
        pair = {"duo-stack", "duo-offset"}
        if (ga is gb and ga is not None and not b.stamp
                and total(npg() - 1) >= min_pages
                and ((a.comp in pair and b.comp in SINGLES)
                     or (a.comp in SINGLES and b.comp in pair))):
            pages[i:i + 2] = [Page("trio-feature", a.slots + b.slots, a.section_id,
                                   a.stamp, a.note)]
        i += 1

    grouped = []
    if features and density != "airy":
        # Pages of similar photographs are composed even when that leaves the
        # book short of the lab minimum: they are kept whole (`_protected`),
        # and the photographs the design left out make up the difference; only
        # when there are none does such a page give up a photograph again
        # (`_split_gentle`), so the book does not end in empty pages. Refusing them whenever the book was short is why
        # no trio or quad ever survived the 24-page minimum.
        grouped = _group_similar(pages, features, by_photo, taken or {}, strength or {},
                                 set(alone or ()), lambda n: True)

    composed = _compose_flagged(pages, together, by_photo, set(alone or ()) - together)

    # Reaching the lab minimum and an even page count. The book gives up its
    # compositions last: lighten the pairs first, then bring in photographs
    # the design left out (each on a page of its own, right after its place),
    # and only then break a trio or a quad, and never the last few of them or
    # a page composed on purpose (the user's, or one of similar photographs).
    floor = max(1, len(pages) // 8)

    def short() -> bool:
        return split_short and total(npg()) < min_pages

    def misaligned() -> bool:
        # A two-page spread starts on a left page, so the ordinary pages
        # (not the spreads) must pair up, or one is left facing blank paper.
        return bool(total(npg()) % inc
                    or odd(sum(1 for p in pages if p.comp != SPREAD)))

    while (short() or misaligned()) and _split_one(pages, together, floor, max_slots=2):
        pass

    added = []
    pool = [pid for pid in (spare or [])
            if pid != pick and pid not in {s["photo_id"] for p in pages for s in p.slots}]
    while pool and total(npg()) < min_pages:
        pid = pool.pop(0)
        insert_solo(pid)
        added.append(pid)

    while (short() or misaligned()) and _split_one(pages, together, floor):
        pass
    # An odd page left over: one more photograph from the pool rather than a
    # blank page, if there is one.
    while misaligned() and pool:
        pid = pool.pop(0)
        insert_solo(pid)
        added.append(pid)
    # Still short, or one page over: an empty page is the worst outcome of
    # all, so the pages of similar photographs give way too, one photograph
    # at a time — a quad becomes a trio first (the composition stays), then
    # the pairs that leaves are taken apart, and only then a trio, never the
    # last `floor` of them — unless taking those apart too fills the book
    # completely: no empty page beats keeping one more trio. When the book
    # cannot be filled whatever is split (too few photographs), the floor
    # stays, since empty pages are left either way. The user's own
    # compositions are never touched.
    give = sum(len(p.slots) - 1 for p in pages
               if p.comp in SPLITS_GENTLE or p.comp in SPLITS
               if not any(s["photo_id"] in together for s in p.slots))
    need = 0
    while split_short and need < 256 and total(npg() + need) < min_pages:
        need += 1
    last = 0 if give >= need else floor
    while short() or misaligned():
        if not (_split_gentle(pages, together, ("quad",), last)
                or _split_one(pages, together, 0, max_slots=2)
                or _split_gentle(pages, together, ("trio-feature", "trio-column"), last)):
            break

    if taken:
        for p in pages:
            if len(p.slots) > 2:
                rest = sorted(p.slots[1:], key=lambda s: taken.get(s["photo_id"]) or "")
                p.slots[1:] = rest

    out: list[dict] = []
    rest_pages = pages
    if front_page:
        # The page facing the front is an ordinary one, never a two-page spread.
        k = next((i for i, p in enumerate(pages) if p.comp != SPREAD), None)
        if k:
            pages.insert(0, pages.pop(k))
        first = pages[0] if pages and pages[0].comp != SPREAD else None
        opts: dict = {"side": "left"}
        if first is not None and first.stamp:
            opts["stamps"] = {"right": first.stamp}
        out.append({**opening,
                    "template": (T.front_framed_name if pick in (no_bleed or ())
                                 else T.front_name)(first.comp if first else "blank"),
                    "slots": opening["slots"] + (first.slots if first else []),
                    "options": opts})
        rest_pages = pages[1:] if first is not None else pages
    elif opening:
        out.append(opening)
    queue = list(rest_pages)
    while queue:
        a = queue.pop(0)
        if a.comp == SPREAD:
            out.append({"index": 0, "section_id": a.section_id, "template": T.USER_SPREAD,
                        "slots": a.slots, "text": None,
                        "options": ({"stamps": {"left": a.stamp}} if a.stamp else {}),
                        "note": a.note or "two pages, on request"})
            continue
        b = None
        if queue and queue[0].comp != SPREAD:
            b = queue.pop(0)
        elif queue:
            # A spread comes next: the first ordinary page after it comes
            # forward to face this one, rather than leaving this one alone.
            k = next((i for i, q in enumerate(queue) if q.comp != SPREAD), None)
            if k is not None:
                b = queue.pop(k)
        if b is not None:
            name, opts = _template_for(a.comp, b.comp)
        else:
            name, opts = _blank_pair(a)
        stamps = {}
        if a.stamp:
            stamps["left"] = a.stamp
        if b is not None and b.stamp:
            stamps["right"] = b.stamp
        if stamps:
            opts["stamps"] = stamps
        slots = list(a.slots) + (list(b.slots) if b is not None else [])
        out.append({
            "index": 0,
            "section_id": a.section_id,
            "template": name,
            "slots": slots,
            "text": None,
            "options": opts,
            "note": a.note,
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
    if brought:
        log.append(f"brought into the book (your choice): {', '.join(brought)}")
    if grouped:
        log.append(f"{len(grouped)} page(s) of similar photographs composed together")
    if composed:
        log.append(f"composed with other photographs (your choice): {', '.join(composed)}")
    if added:
        log.append(f"{len(added)} photograph(s) the design left out brought in to reach "
                   f"{min_pages} pages: {', '.join(added)}")
    return log
