# SPDX-FileCopyrightText: 2026 Alessandro Surace
# SPDX-License-Identifier: AGPL-3.0-only
"""Calibration pages for the Phase 0 test book.

These pages are the whole point of spending money at the lab. A proof PDF on
screen cannot tell you how much the trimmer actually took off, whether the two
halves of a spread print to the same density, or how small type survives on
Fuji Crystal Archive. Each chart is designed to be MEASURED with a ruler when
the book arrives, not admired.
"""

from __future__ import annotations

from geometry import Geometry


def _box(x, y, w, h, style="") -> str:
    return (
        f'<div class="abs" style="left:{x:.2f}mm;top:{y:.2f}mm;'
        f'width:{w:.2f}mm;height:{h:.2f}mm;{style}"></div>'
    )


def _label(x, y, text, size=2.2, colour="#111", align="left", extra="") -> str:
    return (
        f'<div class="abs chart-label" style="left:{x:.2f}mm;top:{y:.2f}mm;'
        f'font-size:{size}mm;color:{colour};text-align:{align};{extra}">{text}</div>'
    )


def trim_ruler(g: Geometry) -> str:
    """Left page: measure exactly where the blade fell.

    A ruler running inward from the media edge, plus rules on the trim line and
    the 5mm safe line. When the book arrives: find the lowest tick still
    visible, and that number is your real trim tolerance.
    """
    out = [_box(0, 0, g.media_w, g.canvas_h, "background:#fff;")]
    trim = g.trim("left")

    # Rules on the two lines that matter.
    out.append(_box(trim.x, trim.y, trim.w, 0.25, "background:#c0392b;"))
    out.append(_box(trim.x, trim.y, 0.25, trim.h, "background:#c0392b;"))
    safe = g.safe("left")
    out.append(_box(safe.x, safe.y, safe.w, 0.15, "background:#2980b9;"))
    out.append(_box(safe.x, safe.y, 0.15, safe.h, "background:#2980b9;"))

    out.append(_label(trim.x + 3, trim.y + 2, "TRIM LINE (red) — should be cut away", 2.4, "#c0392b"))
    out.append(_label(safe.x + 3, safe.y + 3.5, "5 mm SAFE LINE (blue) — must survive", 2.4, "#2980b9"))

    # Ruler inward from the top media edge: ticks every 1mm for 12mm.
    x0 = trim.x + 20
    for i in range(0, 13):
        h = 6 if i % 5 == 0 else 3
        out.append(_box(x0 + i * 4, 0, 0.2, h, "background:#111;"))
        if i % 5 == 0:
            out.append(_label(x0 + i * 4 + 0.8, h + 0.5, f"{i}", 2.2))
    out.append(_label(x0, 14, "millimetres from the media edge, measured downward", 2.4))

    # Same ruler on the outer (left) edge, running inward.
    y0 = trim.y + 40
    for i in range(0, 13):
        w = 6 if i % 5 == 0 else 3
        out.append(_box(0, y0 + i * 4, w, 0.2, "background:#111;"))
        if i % 5 == 0:
            out.append(_label(w + 1, y0 + i * 4 - 1, f"{i}", 2.2))

    out.append(
        _label(
            trim.x + 8,
            trim.y + trim.h - 30,
            "Measure the lowest visible tick on each edge. That is the real trim "
            "tolerance for this lab and format. Update bleed_mm and safe_margin_mm "
            "in labs/saal.yaml accordingly.",
            2.6,
            extra=f"width:{trim.w - 30:.1f}mm;line-height:1.5;",
        )
    )
    return "".join(out)


def test_chart(g: Geometry) -> str:
    """Right page: tone, colour, hairlines and small type."""
    c = g.text_column("right")
    out = [_box(g.page_w, 0, g.media_w, g.canvas_h, "background:#fff;")]
    y = c.y

    out.append(_label(c.x, y, "REPRODUCTION TEST — Professional Line, silk", 3.0))
    y += 7

    # 11-step grey wedge.
    steps = 11
    sw = c.w / steps
    for i in range(steps):
        v = round(255 * (1 - i / (steps - 1)))
        out.append(_box(c.x + i * sw, y, sw, 14, f"background:rgb({v},{v},{v});"))
        out.append(_label(c.x + i * sw + 0.5, y + 15, f"{int(i / (steps - 1) * 100)}", 2.0))
    out.append(_label(c.x, y + 19, "grey wedge, 0–100% — count how many steps stay separable", 2.4))
    y += 25

    # Colour patches, including two skin tones (where colour casts show first).
    swatches = [
        ("R", "#ff0000"), ("G", "#00ff00"), ("B", "#0000ff"),
        ("C", "#00ffff"), ("M", "#ff00ff"), ("Y", "#ffff00"),
        ("sk1", "#f0c8a0"), ("sk2", "#8d5524"),
    ]
    pw = c.w / len(swatches)
    for i, (name, col) in enumerate(swatches):
        out.append(_box(c.x + i * pw, y, pw, 12, f"background:{col};"))
        out.append(_label(c.x + i * pw + 0.5, y + 13, name, 2.0))
    y += 19

    # Deep blacks — where layflat books usually disappoint.
    blacks = [("100%", "#000000"), ("95%", "#0d0d0d"), ("90%", "#1a1a1a"), ("50%", "#808080")]
    bw = c.w / len(blacks)
    for i, (name, col) in enumerate(blacks):
        out.append(_box(c.x + i * bw, y, bw, 12, f"background:{col};"))
        out.append(_label(c.x + i * bw + 0.5, y + 13, name, 2.0, "#111"))
    y += 19

    # Hairlines: the finest rule that still prints tells you the real detail floor.
    for i, t in enumerate([0.05, 0.1, 0.15, 0.25, 0.4, 0.6]):
        out.append(_box(c.x, y + i * 3, c.w * 0.55, t, "background:#000;"))
        out.append(_label(c.x + c.w * 0.58, y + i * 3 - 0.8, f"{t} mm", 2.0))
    y += 21

    # Type specimens.
    out.append(_label(c.x, y, "Type at print size — read these under normal light", 2.4))
    y += 4
    for pt in (5, 6, 7, 8, 9):
        out.append(
            _label(
                c.x, y,
                f"{pt}pt — Der erste Blick auf die Bucht · 0123456789",
                pt * 0.3528,
                extra="font-family:var(--font-caption);white-space:nowrap;",
            )
        )
        y += pt * 0.42 + 1.4
    return "".join(out)


def gutter_target(g: Geometry) -> str:
    """A spread whose only job is to measure the fold.

    Rules and a circle cross the fold. Because the two halves are separate PDF
    pages, any mismatch in trimming or mounting shows up as a step in the rule
    and an offset in the circle. The colour bands check that both halves print
    to the same density.
    """
    out = [_box(0, 0, g.canvas_w, g.canvas_h, "background:#fff;")]
    f = g.fold_x
    mid = g.canvas_h / 2

    # Continuous rule across the whole opening.
    out.append(_box(g.bleed + 20, mid, g.canvas_w - 2 * g.bleed - 40, 0.3, "background:#111;"))

    # Circle centred exactly on the fold.
    d = 40.0
    out.append(
        f'<div class="abs" style="left:{f - d / 2:.2f}mm;top:{mid - d / 2:.2f}mm;'
        f'width:{d:.2f}mm;height:{d:.2f}mm;border:0.3mm solid #111;border-radius:50%;"></div>'
    )
    out.append(_box(f, mid - d / 2 - 8, 0.3, d + 16, "background:#c0392b;"))

    # Ticks every 5mm either side of the fold, labelled.
    for k in range(-4, 5):
        if k == 0:
            continue
        x = f + k * 5
        out.append(_box(x, mid + 30, 0.2, 5, "background:#111;"))
        out.append(_label(x - 2, mid + 36, f"{k * 5:+d}", 2.2, align="center", extra="width:4mm;"))
    out.append(_label(f - 40, mid + 42, "the +0 rule must land in the fold; read the offset off the ticks", 2.4, extra="width:80mm;text-align:center;"))

    # Density bands crossing the fold: left and right pages are printed
    # separately, so a shift shows as a visible seam here.
    bands = [("#808080", "50% grey"), ("#f0c8a0", "skin"), ("#1f3a5f", "deep blue")]
    bw = 22.0
    y0 = g.bleed + 18
    for i, (col, name) in enumerate(bands):
        out.append(_box(f - 70, y0 + i * (bw + 4), 140, bw, f"background:{col};"))
        out.append(_label(f - 70, y0 + i * (bw + 4) + bw + 1, name, 2.2))
    out.append(_label(f - 70, y0 - 5, "look for a seam in these bands — that is a density mismatch between pages", 2.4, extra="width:140mm;"))
    return "".join(out)


RENDERERS = {
    "test_chart": lambda g: trim_ruler(g) + test_chart(g),
    "gutter_target": gutter_target,
}
