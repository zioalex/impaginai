#!/usr/bin/env python3
"""Phase 0: layout JSON + photos -> Saal-Digital-spec PDF.

No LLM, no database, no frontend. This script exists to prove one thing before
anything else gets built: that Chromium can produce a file the lab will print
correctly.

    python render.py check   --layout examples/layout_test24.json --photos ./photos
    python render.py proof   --layout examples/layout_test24.json --photos ./photos
    python render.py print   --layout examples/layout_test24.json --photos ./photos
    python render.py cover   --layout examples/layout_test24.json --photos ./photos

Inner pages and cover are separate PDFs, as the lab requires. No crop or
register marks are emitted anywhere.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path
from urllib.parse import quote

import yaml
from PIL import Image, ImageCms, ImageOps

import charts
import imaging  # registers HEIC/HEIF decoders on import
import templates_lib
from geometry import (CROP_KEEP_MULTI, CROP_KEEP_SINGLE, Geometry, Rect, spine_width_mm,
                      MM_PER_INCH, MIN_CROP_KEEP, fit_rect)
from schema import Layout
from validate import PhotoInfo, check, load_photos

HERE = Path(__file__).parent
ASSETS = HERE / "assets"

# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------


def load_lab(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def geometry_for(lab: dict, fmt: str) -> Geometry:
    formats = lab["formats"]
    if fmt not in formats:
        raise SystemExit(f"format {fmt!r} not in lab config; have {list(formats)}")
    f = formats[fmt]
    return Geometry(
        page_w=float(f["page_w_mm"]),
        page_h=float(f["page_h_mm"]),
        bleed=float(f["bleed_mm"]),
        safe_margin=float(lab["safety"]["safe_margin_mm"]),
        fold_band=float(lab["safety"]["fold_band_mm"]),
    )


# --------------------------------------------------------------------------
# image derivatives
# --------------------------------------------------------------------------

SRGB = ImageCms.createProfile("sRGB")
SRGB_BYTES = ImageCms.ImageCmsProfile(SRGB).tobytes()


def to_srgb(im: Image.Image) -> Image.Image:
    """Normalise to sRGB and keep it there. Never CMYK — the labs want RGB."""
    src = getattr(im, "filename", None)
    im = ImageOps.exif_transpose(im)
    # The user's own turn, for photographs whose EXIF orientation is wrong.
    im = imaging.turn(im, imaging.rotation_of(src))
    icc = im.info.get("icc_profile")
    if icc:
        try:
            src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            return ImageCms.profileToProfile(im, src, SRGB, outputMode="RGB")
        except Exception:
            pass
    return im.convert("RGB")


def required_mm(photo_id: str, layout: Layout, g: Geometry, photos: dict[str, PhotoInfo]) -> float:
    """Largest rendered width in mm this photo reaches anywhere in the album."""
    info = photos[photo_id]
    ar = info.width / info.height
    best = 0.0
    for sp in layout.spreads:
        rendered = templates_lib.build(sp.template, g, sp.options)
        for slot, box in zip(sp.slots, rendered.slots):
            if slot.photo_id != photo_id:
                continue
            r: Rect = box.rect
            # object-fit: cover — the rendered width is whichever axis binds.
            w = max(r.w, r.h * ar)
            best = max(best, w)
    return best


def build_derivatives(
    layout: Layout, photos: dict[str, PhotoInfo], g: Geometry, mode: str, out_dir: Path
) -> dict[str, Path]:
    """proof/ = 1200px longest edge q80. print/ = 300 DPI at slot size, q95."""
    dst = out_dir / mode
    dst.mkdir(parents=True, exist_ok=True)
    used = {s.photo_id for sp in layout.spreads for s in sp.slots}
    if layout.cover and layout.cover.front_photo_id:
        used.add(layout.cover.front_photo_id)
    paths: dict[str, Path] = {}

    for pid in sorted(used):
        info = photos.get(pid)
        if info is None:
            continue
        target = dst / f"{pid}.jpg"
        if mode == "proof":
            long_edge, quality = 1200, 80
        else:
            mm = required_mm(pid, layout, g, photos)
            if pid == (layout.cover.front_photo_id if layout.cover else None):
                mm = max(mm, g.page_w + 30)
            need_px = int(round(mm / MM_PER_INCH * 300))
            ar = info.width / info.height
            long_edge = need_px if ar >= 1 else int(round(need_px / ar))
            quality = 95

        with Image.open(info.path) as im:
            im = to_srgb(im)
            if max(im.size) > long_edge:
                im.thumbnail((long_edge, long_edge), Image.LANCZOS)
            im.save(target, "JPEG", quality=quality, subsampling=0 if mode == "print" else 2,
                    icc_profile=SRGB_BYTES, optimize=True)
        paths[pid] = target
    return paths


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------


def src_url(path: Path) -> str:
    """A file path as a URL, safe inside an HTML attribute.

    The page is loaded from a `file://` URI and every `<img>` points at a
    derivative on disk, so the attribute holds a path — and a path is not a
    URL. `Sommer #3.jpg` truncates at the fragment and the photograph renders
    blank, which the browser reports as a 30-second wait for an image that
    never loads; `100%.jpg` is an invalid escape; and a path containing a
    double quote ends the attribute early and injects whatever follows into a
    page Chromium then runs. None of this is exotic: a photograph's id is its
    filename stem (`phase1/photos.py`, `phase0/validate.py`), the album
    directory is named by the user, and both end up in this attribute.

    Percent-encoding leaves `/` and a Windows drive's `:` alone, so an
    ordinary path is unchanged, and removes every character that could end
    the attribute or the URL.
    """
    return quote(path.as_posix(), safe="/:")


def esc(s: str | None) -> str:
    if not s:
        return ""
    return (
        s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


def _covers(r: Rect, window: Rect, eps: float = 0.05) -> bool:
    return (
        r.x <= window.x + eps
        and r.y <= window.y + eps
        and r.x2 >= window.x2 - eps
        and r.y2 >= window.y2 - eps
    )


def rect_style(r: Rect) -> str:
    return f"left:{r.x:.2f}mm;top:{r.y:.2f}mm;width:{r.w:.2f}mm;height:{r.h:.2f}mm;"


def overlay_rect(g: Geometry, box, position: str) -> Rect:
    """Caption laid over a bleeding image, kept inside the safe area.

    The test must be spans_fold, not crosses_fold: a single-page bleed runs one
    bleed PAST the crease by design, so crosses_fold is true for it and the
    caption was being placed on the facing page — which for a one-slot template
    is blank paper.
    """
    if g.spans_fold(box.rect):
        side = "left" if position.endswith("left") else "right"
    else:
        side = "left" if box.rect.x2 <= g.fold_x + g.bleed else "right"
    s = g.safe(side)
    h = 11.0   # room for two lines
    y = s.y if position.startswith("top") else s.y2 - h
    return Rect(s.x, y, s.w, h)


def _side_of(g: Geometry, window: Rect) -> str:
    """Which page a page window shows. The window includes bleed, so its left
    edge alone is not enough: the right page's window starts past the fold."""
    return "left" if (window.x + window.x2) / 2 < g.fold_x else "right"


_ASPECTS: dict[str, float] = {}


def _aspect(path: Path) -> float:
    """Width / height of a derivative, read from its header and cached."""
    key = str(path)
    if key not in _ASPECTS:
        try:
            with Image.open(path) as im:
                _ASPECTS[key] = im.width / im.height
        except Exception:
            _ASPECTS[key] = 0.0
    return _ASPECTS[key]


def _owns(g: Geometry, window: Rect, r: Rect) -> bool:
    """Whether rectangle `r` belongs on the page this window shows.

    A page window includes the bleed, so it overlaps the facing page's inner
    edge — and a single-page full-bleed photograph deliberately runs one bleed
    past the fold. Drawing everything that merely intersects the window
    therefore printed a strip of the facing page's photograph (or its ink) at
    the gutter. Each element is drawn on its own page only; a photograph that
    genuinely spans the fold (calibration panoramas) is drawn on both.
    """
    if not r.intersects(window):
        return False
    if g.spans_fold(r):
        return True
    return _side_of(g, r) == _side_of(g, window)


def _scrim(r: Rect, page: Rect, depth: float, top: bool = False) -> str:
    """A soft dark gradient over the edge of a photograph, so white text set on
    it reads on snow and sky as well as on shadow. Clipped to the trimmed page."""
    x = max(r.x, page.x - 3)
    x2 = min(r.x2, page.x2 + 3)
    if top:
        y, h = r.y, depth
        cls = "scrim top"
    else:
        y, h = r.y2 - depth, depth
        cls = "scrim"
    return f'<div class="{cls} abs" style="{rect_style(Rect(x, y, x2 - x, h))}"></div>'


def stamp_html(g: Geometry, window: Rect, photo: Rect | None, text: str) -> str:
    """Place and date for the first page of a location, set on the photograph's
    own page rather than on a divider page of its own.

    Above the photograph, in the top margin, when there is room for it there;
    otherwise laid over the top of the photograph in white, on a scrim. It never
    overlaps a photograph in grey type, which is what made it unreadable.
    """
    side = _side_of(g, window)
    safe = g.safe(side)
    trim = g.trim(side)
    col = g.text_column(side)
    h = 4.2
    room = (photo.y - safe.y) if photo is not None else col.y - safe.y
    if photo is not None and room < h + 2.0:
        r = Rect(max(photo.x, safe.x) + 3, max(photo.y, safe.y) + 3.5,
                 min(photo.x2, safe.x2) - max(photo.x, safe.x) - 6, h)
        scrim = _scrim(Rect(photo.x, photo.y, photo.w, photo.h), trim,
                       min(22.0, photo.h * 0.35), top=True)
        return scrim + f'<div class="stamp overlay abs" style="{rect_style(r)}">{esc(text)}</div>'
    top = photo.y if photo is not None else col.y
    x = max(photo.x, safe.x) if photo is not None else col.x
    r = Rect(x, round(max(safe.y, top - h - 1.8), 2), safe.x2 - x, h)
    return f'<div class="stamp abs" style="{rect_style(r)}">{esc(text)}</div>'


def _visible(rect: Rect, aspect: float) -> tuple[float, float]:
    """Fractions of a photograph's width and height left visible when it
    covers `rect` (object-fit: cover)."""
    r = rect.w / rect.h if rect.h else 1.0
    if aspect <= 0:
        return 1.0, 1.0
    return (r / aspect, 1.0) if aspect > r else (1.0, aspect / r)


def place_photo(slot_rect: Rect, aspect: float, focal, keep, bleeds: bool,
                min_keep: float = MIN_CROP_KEEP, fallback: Rect | None = None):
    """Where a photograph goes in its slot and which part of it shows:
    (rect, x%, y%) for object-position.

    The crop is centred on the focal point, then moved just enough that the
    keep-box (people, the main subject) stays wholly inside. If the box cannot
    fit at all, a framed slot shows the whole photograph instead — smaller, but
    nobody cut in half. A page set to the edges is decided upstream
    (pages.protect_subjects) and keeps its crop; should its keep-box still not
    fit (a bleed slot the upstream pass did not see), it too falls back to the
    whole photograph inside the slot rather than cutting the subject.

    `min_keep` is how much of the photograph the slot may cut away: a slot
    sized by compose.adaptive passes the allowance it was sized with, so the
    render does not shrink again what the compositor already fitted."""
    rect = slot_rect if bleeds else fit_rect(slot_rect, aspect, min_keep)
    vx, vy = _visible(rect, aspect)
    if keep and (keep[2] - keep[0] > vx + 0.01 or keep[3] - keep[1] > vy + 0.01):
        if not bleeds:
            rect = fit_rect(slot_rect, aspect, min_keep=1.0)
        elif fallback is not None:
            rect = fit_rect(fallback, aspect, min_keep=1.0)    # inside the margins
        vx, vy = _visible(rect, aspect)

    def axis(f, lo, hi, v):
        if v >= 0.999:
            return 50.0
        start = f - v / 2
        if lo is not None:
            start = min(start, lo)            # the box's start must show
            start = max(start, hi - v)        # and so must its end
        start = min(max(start, 0.0), 1.0 - v)
        return start / (1.0 - v) * 100

    fx, fy = focal
    px = axis(fx, keep[0] if keep else None, keep[2] if keep else None, vx)
    py = axis(fy, keep[1] if keep else None, keep[3] if keep else None, vy)
    return rect, px, py


def canvas_html(sp, rendered, g: Geometry, photo_paths: dict[str, Path], window: Rect) -> str:
    parts: list[str] = []
    side = _side_of(g, window)
    page = g.trim(side)

    if rendered.chart:
        parts.append(charts.RENDERERS[rendered.chart](g))

    first_on_page: Rect | None = None
    overlays: list[str] = []      # captions and scrims go above every photograph
    band: list[str] = []          # captions set in the band under the page's photos
    keeps = list((sp.options or {}).get("keeps") or [])
    # Slots sized by compose.adaptive (the spread carries the photographs'
    # shapes) were fitted with an allowance: render with the same one.
    adapted = bool((sp.options or {}).get("aspects"))
    on_page = sum(1 for b in rendered.slots if _owns(g, window, b.rect))
    allow = (CROP_KEEP_SINGLE if on_page <= 1 else CROP_KEEP_MULTI) - 0.02 if adapted else MIN_CROP_KEEP
    for n, (slot, box) in enumerate(zip(sp.slots, rendered.slots)):
        if not _owns(g, window, box.rect):
            continue
        src = photo_paths.get(slot.photo_id)
        if src is None:
            continue
        fx, fy = slot.focal
        treat = getattr(slot, "treatment", "none")
        cls = "slot abs" + (f" t-{treat}" if treat and treat != "none" else "")
        # A bleeding photograph always fills its page — cropped around the
        # focal point, never shrunk into white. Elsewhere, never cut away more
        # of a photograph than geometry.MIN_CROP_KEEP allows: past that the
        # slot shrinks to the photograph's own shape.
        keep = keeps[n] if n < len(keeps) else None
        rect, posx, posy = place_photo(box.rect, _aspect(src), (fx, fy), keep, box.bleeds, allow,
                                        g.text_column(side))
        fitted = rect is not box.rect
        if first_on_page is None or (rect.y, rect.x) < (first_on_page.y, first_on_page.x):
            first_on_page = rect
        parts.append(
            f'<img class="{cls}" src="{src_url(src)}" '
            f'style="{rect_style(rect)}object-position:{posx:.1f}% {posy:.1f}%;">'
        )
        if not slot.caption or box.caption_style == "none" or slot.caption_position == "none":
            continue
        align = box.caption_align
        extra = f" {align}" if align in ("right", "center") else ""
        if box.caption_style == "overlay" and not fitted:
            # On a bleeding photograph: white type at the foot of the safe
            # area, on a scrim deep enough for two lines.
            cr = overlay_rect(g, box, slot.caption_position)
            top = slot.caption_position.startswith("top")
            vis = Rect(max(rect.x, page.x), rect.y, min(rect.x2, page.x2) - max(rect.x, page.x), rect.h)
            overlays.append(_scrim(vis, page, 34.0, top=top))
            overlays.append(f'<div class="caption overlay{extra} abs" style="{rect_style(cr)}">'
                            f'{esc(slot.caption)}</div>')
        elif box.caption_style == "inset":
            # A supporting photograph's own caption, inside its frame.
            pad = 2.2
            cr = Rect(rect.x + pad, rect.y2 - 9.0 - pad, rect.w - 2 * pad, 9.0)
            overlays.append(_scrim(rect, page, min(18.0, rect.h * 0.45)))
            overlays.append(f'<div class="caption overlay small{extra} abs" '
                            f'style="{rect_style(cr)}">{esc(slot.caption)}</div>')
        else:
            if box.caption_rect is not None and not fitted:
                cr = box.caption_rect
            else:
                safe = g.safe(_side_of(g, rect))
                x = max(rect.x, safe.x)
                cr = Rect(x, rect.y2 + 2.5, min(rect.x2, safe.x2) - x, 9)
            band.append(f'<div class="caption{extra} abs" style="{rect_style(cr)}">'
                        f'{esc(slot.caption)}</div>')

    parts += overlays + band

    stamp = (sp.options or {}).get("stamps", {}).get(side)
    if stamp:
        parts.append(stamp_html(g, window, first_on_page, stamp))

    text = sp.text
    for tb in rendered.texts:
        value = getattr(text, tb.key, None) if text else None
        if not value:
            continue
        if not _owns(g, window, tb.rect):
            continue
        if getattr(tb, "scrim", None) is not None and _owns(g, window, tb.scrim):
            r = tb.scrim
            parts.append(f'<div class="scrim abs" style="{rect_style(r)}"></div>')
        cls = f"tb {tb.role}"
        if tb.invert or rendered.background == "ink" or rendered.background == f"ink-{side}":
            cls += " invert"
        style = rect_style(tb.rect) + f"text-align:{tb.align};"
        parts.append(f'<div class="{cls} abs" style="{style}">{esc(value)}</div>')

    return "".join(parts)


def build_inner_html(
    layout: Layout, g: Geometry, photo_paths: dict[str, Path], css: str
) -> tuple[str, list[int]]:
    """Returns the document and the 1-based page numbers that bleed on all four
    sides. Those pages are what verify_pdf() measures to prove nothing scaled."""
    sheets: list[str] = []
    full_bleed_pages: list[int] = []
    for sp in layout.spreads:
        rendered = templates_lib.build(sp.template, g, sp.options)
        for side in ("left", "right"):
            window = g.page_window(side)
            for slot, box in zip(sp.slots, rendered.slots):
                if slot.photo_id in photo_paths and _covers(box.rect, window):
                    full_bleed_pages.append(len(sheets) + 1)
                    break
            offset = g.page_offset(side)
            body = canvas_html(sp, rendered, g, photo_paths, window)
            # "ink" inks the whole spread; "ink-left"/"ink-right" ink only that
            # page, so a chapter title can sit on black facing a photograph.
            bg = rendered.background
            ink = " ink" if (bg == "ink" or bg == f"ink-{side}") else ""
            sheets.append(
                f'<div class="sheet" style="width:{g.media_w:.2f}mm;height:{g.media_h:.2f}mm;">'
                f'<div class="canvas{ink}" style="left:{-offset:.2f}mm;'
                f'width:{g.canvas_w:.2f}mm;height:{g.canvas_h:.2f}mm;">{body}</div>'
                f"</div>"
            )
    page_rule = (
        f"@page {{ size: {g.media_w:.2f}mm {g.media_h:.2f}mm; margin: 0; }}"
    )
    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<style>{css}\n{page_rule}</style></head><body>"
        + "".join(sheets)
        + "</body></html>"
    )
    return html, full_bleed_pages


def build_cover_html(layout: Layout, g: Geometry, lab: dict, photo_paths: dict[str, Path], css: str) -> tuple[str, dict]:
    cfg = lab["cover"]
    wrap = float(cfg["wrap_mm"])
    overhang = float(cfg.get("overhang_mm", 0.0))
    spine = spine_width_mm(layout.page_count, float(cfg["leaf_thickness_mm"]), float(cfg["board_mm"]))
    panel_w = g.page_w + overhang
    panel_h = g.page_h + 2 * overhang
    total_w = 2 * wrap + 2 * panel_w + spine
    total_h = 2 * wrap + panel_h

    back_x = wrap
    spine_x = wrap + panel_w
    front_x = spine_x + spine

    cover = layout.cover
    parts = [
        # Ink covers back panel and spine, up to the front hinge. The boundary
        # is the measurement: if the computed spine is wrong, this edge will
        # not land on the fold.
        f'<div class="cover-panel cover-ink" style="left:0;top:0;width:{front_x:.2f}mm;height:{total_h:.2f}mm;"></div>'
    ]
    if cover and cover.front_photo_id and cover.front_photo_id in photo_paths:
        # The crop is centred on the focal point and moved just enough that
        # the people (the keep-box) stay in view, as on the inner pages.
        # The box rides on the opening spread (options.keeps, slot 0 is the
        # front photograph) so the layout schema needs no new field.
        first = layout.spreads[0] if layout.spreads else None
        fkeeps = list(((first.options or {}).get("keeps") or [])) if first else []
        fkeep = (fkeeps[0] if fkeeps and first.slots
                 and first.slots[0].photo_id == cover.front_photo_id else None)
        area = Rect(front_x, 0.0, total_w - front_x, total_h)
        _, cx, cy = place_photo(area, _aspect(photo_paths[cover.front_photo_id]),
                                tuple(cover.front_focal), fkeep, True)
        parts.append(
            f'<img class="slot abs" src="{src_url(photo_paths[cover.front_photo_id])}" '
            f'style="left:{front_x:.2f}mm;top:0;width:{total_w - front_x:.2f}mm;height:{total_h:.2f}mm;'
            f"object-position:{cx:.1f}% {cy:.1f}%;\">"
        )
    if cover and cover.title:
        parts.append(
            f'<div class="tb title invert abs" style="left:{front_x + g.margin_outer:.2f}mm;'
            f'top:{total_h - wrap - g.margin_bottom - 20:.2f}mm;width:{panel_w - 2 * g.margin_outer:.2f}mm;">'
            f"{esc(cover.title)}</div>"
        )
    if cover and cover.subtitle:
        parts.append(
            f'<div class="tb subtitle invert abs" style="left:{front_x + g.margin_outer:.2f}mm;'
            f'top:{total_h - wrap - g.margin_bottom - 6:.2f}mm;width:{panel_w - 2 * g.margin_outer:.2f}mm;">'
            f"{esc(cover.subtitle)}</div>"
        )
    if cover and cover.spine_title and spine >= 6:
        parts.append(
            f'<div class="spine-text abs" style="left:{spine_x + spine / 2 + 1.6:.2f}mm;'
            f'top:{wrap + 20:.2f}mm;">{esc(cover.spine_title)}</div>'
        )
    if cover and cover.back_text:
        parts.append(
            f'<div class="tb body invert abs" style="left:{back_x + g.margin_outer:.2f}mm;'
            f'top:{total_h - wrap - g.margin_bottom:.2f}mm;width:{panel_w * 0.7:.2f}mm;font-size:3mm;">'
            f"{esc(cover.back_text)}</div>"
        )

    page_rule = f"@page {{ size: {total_w:.2f}mm {total_h:.2f}mm; margin: 0; }}"
    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<style>{css}\n{page_rule}</style></head><body>"
        f'<div class="sheet" style="width:{total_w:.2f}mm;height:{total_h:.2f}mm;">'
        + "".join(parts)
        + "</div></body></html>"
    )
    info = {
        "spine_mm": spine,
        "total_w_mm": round(total_w, 2),
        "total_h_mm": round(total_h, 2),
        "wrap_mm": wrap,
        "front_hinge_x_mm": round(front_x, 2),
        "pages": layout.page_count,
    }
    return html, info


# --------------------------------------------------------------------------
# PDF
# --------------------------------------------------------------------------


BROWSER_MISSING = (
    "The page renderer's browser is missing or out of date. Run\n\n"
    "    playwright install chromium\n\n"
    "in the same environment, then try again. (This is needed once after "
    "installing, and again after every update: each Playwright version uses "
    "its own browser download.)")


def launch_browser(p):
    """Chromium, or a sentence saying how to get it. The raw error was a
    forty-line traceback ending in a box that says 'playwright install',
    which reads as if the project had not been set up at all."""
    try:
        return p.chromium.launch(args=["--font-render-hinting=none"])
    except Exception as exc:
        if "Executable doesn't exist" in str(exc) or "playwright install" in str(exc):
            raise RuntimeError(BROWSER_MISSING) from None
        raise


def browser_ready() -> bool:
    """Whether the renderer can start, without rendering anything."""
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as p:
            return Path(p.chromium.executable_path).exists()
    except Exception:
        return False


def render_pdf(html: str, html_path: Path, pdf_path: Path) -> None:
    from playwright.sync_api import sync_playwright

    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(html, encoding="utf-8")
    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = launch_browser(p)
        page = browser.new_page()
        page.goto(html_path.resolve().as_uri(), wait_until="load")
        page.wait_for_function(
            "Array.from(document.images).every(i => i.complete && i.naturalWidth > 0)"
        )
        page.pdf(
            path=str(pdf_path),
            print_background=True,
            prefer_css_page_size=True,   # honour @page, do not letterbox onto A4
            margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
        )
        browser.close()


def render_jpegs(html: str, html_path: Path, out_dir: Path, dpi: int = 150,
                 thumb_px: int | None = None) -> list[Path]:
    """One JPEG per page, from the same HTML as the PDF. With `thumb_px`, small
    page images of that width instead (the editor's page previews)."""
    from playwright.sync_api import sync_playwright

    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(html, encoding="utf-8")
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("page-*.jpg"):
        old.unlink()
    files: list[Path] = []
    with sync_playwright() as p:
        browser = launch_browser(p)
        if thumb_px:
            scale = 1.0
        else:
            scale = dpi / 96.0
        page = browser.new_page(device_scale_factor=scale)
        page.goto(html_path.resolve().as_uri(), wait_until="load")
        page.wait_for_function(
            "Array.from(document.images).every(i => i.complete && i.naturalWidth > 0)"
        )
        page.add_style_tag(content="body{margin:0} .sheet{margin:0}")
        sheets = page.query_selector_all(".sheet")
        if thumb_px and sheets:
            w = sheets[0].bounding_box()["width"]
            browser_scale = thumb_px / w
            page.close()
            page = browser.new_page(device_scale_factor=browser_scale)
            page.goto(html_path.resolve().as_uri(), wait_until="load")
            page.wait_for_function(
                "Array.from(document.images).every(i => i.complete && i.naturalWidth > 0)"
            )
            sheets = page.query_selector_all(".sheet")
        for i, el in enumerate(sheets, 1):
            f = out_dir / f"page-{i:03d}.jpg"
            el.screenshot(path=str(f), type="jpeg", quality=88 if thumb_px else 92)
            files.append(f)
        browser.close()
    return files


def drawn_widths_pt(page) -> list[float]:
    """Horizontal scale of every image placement on a page, in points.

    Chromium writes each image as a `w 0 0 h x y cm` matrix before drawing the
    XObject, so `w` is the drawn width. Reading it back is the only way to prove
    the PDF contains what we intended rather than a silently scaled copy.
    """
    import re

    try:
        data = page.get_contents().get_data().decode("latin-1", "ignore")
    except Exception:
        return []
    pat = re.compile(r"([-\d.]+)\s+0\s+0\s+([-\d.]+)\s+[-\d.]+\s+[-\d.]+\s+cm")
    return [abs(float(m.group(1))) for m in pat.finditer(data)]


def verify_pdf(
    pdf_path: Path,
    expect_w: float,
    expect_h: float,
    expect_pages: int | None,
    full_bleed_pages: list[int] | None = None,
) -> bool:
    from pypdf import PdfReader

    reader = PdfReader(str(pdf_path))
    ok = True
    n = len(reader.pages)
    if expect_pages is not None and n != expect_pages:
        print(f"  FAIL page count: {n}, expected {expect_pages}")
        ok = False
    else:
        print(f"  pages: {n}")
    for i, pg in enumerate(reader.pages):
        w = float(pg.mediabox.width) / 72 * MM_PER_INCH
        h = float(pg.mediabox.height) / 72 * MM_PER_INCH
        if abs(w - expect_w) > 0.2 or abs(h - expect_h) > 0.2:
            print(f"  FAIL page {i + 1} is {w:.2f}x{h:.2f}mm, expected {expect_w:.2f}x{expect_h:.2f}mm")
            ok = False
    if ok:
        print(f"  every page {expect_w:.2f} x {expect_h:.2f} mm")

    # Scale assertion. A full-bleed image must be drawn at least as wide as the
    # media box; anything less means Chromium shrink-to-fitted the document.
    if full_bleed_pages:
        want_pt = expect_w / MM_PER_INCH * 72
        worst = None
        for n in full_bleed_pages:
            widths = drawn_widths_pt(reader.pages[n - 1])
            if not widths:
                continue
            biggest = max(widths)
            if worst is None or biggest < worst[1]:
                worst = (n, biggest)
        if worst and worst[1] < want_pt * 0.995:
            print(f"  FAIL page {worst[0]}: full-bleed image drawn {worst[1] / 72 * MM_PER_INCH:.1f}mm "
                  f"wide, page is {expect_w:.1f}mm — the document was scaled to "
                  f"{worst[1] / want_pt:.3f}. Check `contain` on .sheet.")
            ok = False
        elif worst:
            print(f"  bleed reaches the media edge on all {len(full_bleed_pages)} full-bleed pages")
    size_mb = pdf_path.stat().st_size / 1e6
    print(f"  {pdf_path} — {size_mb:.1f} MB")
    return ok


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["check", "proof", "print", "cover", "jpeg"])
    ap.add_argument("--dpi", type=int, default=150,
                    help="jpeg only: resolution of the page images (150 preview, 300 print)")
    ap.add_argument("--layout", type=Path, required=True)
    ap.add_argument("--photos", type=Path, required=True)
    ap.add_argument("--lab", type=Path, default=HERE / "labs" / "saal.yaml")
    ap.add_argument("--out", type=Path, default=HERE / "out")
    ap.add_argument("--format", default=None, help="override meta.format")
    ap.add_argument("--allow-fold", action="store_true",
                    help="permit photographs printed across the fold. The Phase 0 "
                         "calibration book uses this deliberately, to measure how bad "
                         "the crease actually is. Production albums should not.")
    args = ap.parse_args()

    lab = load_lab(args.lab)
    if args.allow_fold:
        lab.setdefault("safety", {})["allow_fold_crossing"] = True
        print("fold crossing permitted for this render (calibration only)\n")
    raw = json.loads(args.layout.read_text(encoding="utf-8"))
    layout = Layout.model_validate(raw)          # schema failure = hard stop
    fmt = args.format or layout.meta.format
    g = geometry_for(lab, fmt)
    photos = load_photos(args.photos)

    print(f"{layout.meta.title} — {fmt}, {len(layout.spreads)} spreads / {layout.page_count} pages")
    print(f"page {g.page_w:.0f}x{g.page_h:.0f}mm + {g.bleed}mm bleed → media {g.media_w:.0f}x{g.media_h:.0f}mm, canvas {g.canvas_w:.0f}x{g.canvas_h:.0f}mm\n")

    report = check(layout, photos, g, lab)
    report.print_report()
    if args.command == "check":
        return 1 if report.blocking else 0
    if report.blocking:
        print("\nrefusing to render: fix the errors above")
        return 1

    css = (ASSETS / "style.css").read_text(encoding="utf-8")
    mode = "proof" if args.command == "proof" or (args.command == "jpeg" and args.dpi <= 150) else "print"
    print(f"\nbuilding {mode} derivatives...")
    photo_paths = build_derivatives(layout, photos, g, mode, args.out.resolve() / "derivatives")

    if args.command == "jpeg":
        html, _ = build_inner_html(layout, g, photo_paths, css)
        files = render_jpegs(html, args.out / "inner-jpeg.html", args.out / "pages", args.dpi)
        print(f"  {len(files)} page images at {args.dpi} DPI in {args.out / 'pages'}")
        return 0

    if args.command in ("proof", "print"):
        html, bleed_pages = build_inner_html(layout, g, photo_paths, css)
        pdf = args.out / f"inner-{mode}.pdf"
        render_pdf(html, args.out / f"inner-{mode}.html", pdf)
        print("verifying:")
        ok = verify_pdf(pdf, g.media_w, g.media_h, layout.page_count, bleed_pages)
        return 0 if ok else 1

    if layout.cover is None or not (layout.cover.front_photo_id or layout.cover.title):
        print("\nrefusing to render a cover: this layout has no `cover` section, so the "
              "result would be a blank wrap.\nAdd one to the layout JSON, for example:\n"
              '  "cover": {"front_photo_id": "p001", "front_focal": [0.5, 0.45],\n'
              '            "title": "Sardinien 2026", "subtitle": "Zehn Tage am Meer",\n'
              '            "spine_title": "Sardinien 2026"}')
        return 1

    html, info = build_cover_html(layout, g, lab, photo_paths, css)
    pdf = args.out / "cover.pdf"
    render_pdf(html, args.out / "cover.html", pdf)
    print(f"\nspine {info['spine_mm']}mm for {info['pages']} pages "
          f"(leaf {lab['cover']['leaf_thickness_mm']}mm x {info['pages'] // 2} leaves + 2 boards)")
    print("verifying:")
    ok = verify_pdf(pdf, info["total_w_mm"], info["total_h_mm"], 1, [1] if layout.cover and layout.cover.front_photo_id else None)
    print("  NOTE: spine constants are unverified. Compare against Saal's cover "
          "template for this format and page count before ordering.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
