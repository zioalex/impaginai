# SPDX-FileCopyrightText: 2026 Alessandro Surace
# SPDX-License-Identifier: AGPL-3.0-only
"""How much of each page is empty, and why.

"Some pages still carry a lot of white" is an impression until it is a number.
This measures, for every page of a layout, the share of the trimmed page that
no photograph and no text covers, and names the main cause, so that a remedy
can be chosen for the album at hand and its effect checked afterwards.

The rectangles are the ones the renderer draws (`render.photo_rects` follows
`render.canvas_html`), so what is measured is what is printed.

    photo    share of the page under photographs
    text     share under text blocks that are not set on a photograph
    empty    the rest
    fitted   lost because a photograph is shown whole inside a slot of another
             shape (its subject would have been cut)
    small    lost because a photograph has too few pixels for its slot and is
             printed smaller
"""

from __future__ import annotations

from geometry import Geometry, Rect

# A page with ordinary margins is about a third empty; a page past this is one
# a reader would call bare.
FLAG_ABOVE = 0.50
SLIVER = 0.03           # less of the page than this is a bleed overlap, not a photograph
NOTABLE = 0.08          # a cause smaller than this share of the page is not named

CAUSES = {
    "nothing": "nothing on the page",
    "text": "text only, no photograph",
    "fitted": "a photograph shown whole inside a slot of another shape",
    "small": "a photograph with too few pixels, printed smaller",
    "layout": "margins and spacing of the page's layout",
}


def _overlap(a: Rect, b: Rect) -> float:
    w = min(a.x2, b.x2) - max(a.x, b.x)
    h = min(a.y2, b.y2) - max(a.y, b.y)
    return w * h if w > 0 and h > 0 else 0.0


def _clip(r: Rect, to: Rect) -> Rect | None:
    x, y = max(r.x, to.x), max(r.y, to.y)
    w, h = min(r.x2, to.x2) - x, min(r.y2, to.y2) - y
    return Rect(x, y, w, h) if w > 0 and h > 0 else None


def _union(rects: list[Rect]) -> float:
    """Area covered by any of the rectangles: overlaps counted once."""
    xs = sorted({v for r in rects for v in (r.x, r.x2)})
    ys = sorted({v for r in rects for v in (r.y, r.y2)})
    total = 0.0
    for x0, x1 in zip(xs, xs[1:]):
        for y0, y1 in zip(ys, ys[1:]):
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            if any(r.x <= cx <= r.x2 and r.y <= cy <= r.y2 for r in rects):
                total += (x1 - x0) * (y1 - y0)
    return total


def measure(layout, dims: dict, g: Geometry) -> list[dict]:
    """One entry per page, in page order. `dims` maps photo id to
    (width, height) in pixels; `layout` is a schema.Layout."""
    import render as R
    import templates_lib

    out: list[dict] = []
    page_no = 0
    for sp in layout.spreads:
        opts = dict(sp.options or {})
        rendered = templates_lib.build(sp.template, g, opts)
        try:
            ideal = templates_lib.build(sp.template, g,
                                        {k: v for k, v in opts.items() if k != "pixels"})
        except Exception:
            ideal = rendered
        text = sp.text
        # Every photograph of the spread, wherever it is drawn: one set across
        # both pages covers both, though only one page "owns" it.
        drawn: dict[int, Rect] = {}
        for side in ("left", "right"):
            drawn.update(R.photo_rects(sp, rendered, g, g.page_window(side), dims))
        for side in ("left", "right"):
            page_no += 1
            trim = g.trim(side)
            area = trim.w * trim.h
            # The few millimetres a facing bleed runs past the fold are not a
            # photograph on this page.
            shown = [c for c in (_clip(r, trim) for r in drawn.values()) if c]
            photo = _union(shown)
            placed = [(n, r) for n, r in sorted(drawn.items()) if _overlap(r, trim) > SLIVER * area]
            slot = sum(_overlap(rendered.slots[n].rect, trim) for n, _ in placed)
            want = sum(_overlap(ideal.slots[n].rect, trim) for n, _ in placed
                       if n < len(ideal.slots))
            blocks = [c for c in (_clip(tb.rect, trim) for tb in rendered.texts
                                  if text and getattr(text, tb.key, None)) if c]
            covered = _union(shown + blocks)      # text set on a photograph counts once
            if rendered.chart:
                covered = area                    # calibration pages are full by design
            words = covered - photo
            fitted = max(0.0, slot - photo) / area
            small = max(0.0, want - slot) / area
            if not placed and not words:
                cause = "nothing"
            elif not placed:
                cause = "text"
            elif max(fitted, small) >= NOTABLE:
                cause = "fitted" if fitted >= small else "small"
            else:
                cause = "layout"
            out.append({
                "page": page_no,
                "photo": round(photo / area, 3),
                "text": round(words / area, 3),
                "empty": round(1.0 - covered / area, 3),
                "fitted": round(fitted, 3),
                "small": round(small, 3),
                "cause": cause,
                "dark": rendered.background == "ink",
            })
    return out


def flagged(pages: list[dict]) -> list[dict]:
    """Pages a reader would call bare. A dark page is a designed page, and a
    page carrying only text is counted elsewhere (validate's empty pages)."""
    return [p for p in pages if p["empty"] > FLAG_ABOVE and not p["dark"]]


def summary(pages: list[dict]) -> str:
    if not pages:
        return ""
    avg = 100 * sum(p["empty"] for p in pages) / len(pages)
    bare = flagged(pages)
    line = f"on average {avg:.0f}% of a page is empty"
    if not bare:
        return line + "; no page is more than half empty"
    by_cause: dict[str, list[int]] = {}
    for p in bare:
        by_cause.setdefault(p["cause"], []).append(p["page"])
    parts = [f"{CAUSES[c]}: page{'s' if len(ps) > 1 else ''} "
             f"{', '.join(map(str, ps[:8]))}{'…' if len(ps) > 8 else ''}"
             for c, ps in sorted(by_cause.items(), key=lambda kv: -len(kv[1]))]
    return (line + f"; {len(bare)} page{'s' if len(bare) > 1 else ''} more than half empty\n  "
            + "\n  ".join(parts))
