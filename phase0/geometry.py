"""Page geometry for a layflat book exported as single pages.

The whole renderer rests on one idea: templates are authored on a SPREAD CANVAS,
and the canvas is sliced into two single media pages at export time.

    canvas_w = 2 * page_w + 2 * bleed
    canvas_h = page_h + 2 * bleed

    x=0            x=bleed                    fold                     x=canvas_w
    |---- bleed ----|-------- left trim -------|------- right trim -----|-- bleed --|

Left media page  shows canvas x in [0, page_w + 2*bleed]     (offset  0)
Right media page shows canvas x in [page_w, 2*page_w + 2*bleed] (offset -page_w)

Both windows are page_w + 2*bleed wide, which is exactly the media size Saal
expects per page. Note the overlap: each page's INNER bleed strip duplicates
3mm of the neighbouring page's trim area. That is deliberate and it is what
Saal's "bleed applies per page" rule requires — after trimming, the duplicated
strip disappears into the fold, and no white sliver can appear at the seam.

A pleasant consequence: a panorama is not a special case. It is just a slot
whose rectangle happens to cover the whole canvas.
"""

from __future__ import annotations

from dataclasses import dataclass

MM_PER_INCH = 25.4
PT_PER_MM = 72.0 / 25.4


@dataclass(frozen=True)
class Rect:
    """A rectangle in canvas millimetres, origin top-left."""

    x: float
    y: float
    w: float
    h: float

    @property
    def x2(self) -> float:
        return self.x + self.w

    @property
    def y2(self) -> float:
        return self.y + self.h

    def intersects(self, other: "Rect") -> bool:
        return not (
            self.x2 <= other.x
            or other.x2 <= self.x
            or self.y2 <= other.y
            or other.y2 <= self.y
        )


@dataclass(frozen=True)
class Geometry:
    page_w: float
    page_h: float
    bleed: float
    safe_margin: float = 5.0
    fold_band: float = 8.0

    # --- canvas ---------------------------------------------------------
    @property
    def canvas_w(self) -> float:
        return 2 * self.page_w + 2 * self.bleed

    @property
    def canvas_h(self) -> float:
        return self.page_h + 2 * self.bleed

    # --- media page (what the PDF page actually measures) ---------------
    @property
    def media_w(self) -> float:
        return self.page_w + 2 * self.bleed

    @property
    def media_h(self) -> float:
        return self.page_h + 2 * self.bleed

    def page_offset(self, side: str) -> float:
        """Canvas x-coordinate of the left edge of a media page window."""
        return 0.0 if side == "left" else self.page_w

    def page_window(self, side: str) -> Rect:
        return Rect(self.page_offset(side), 0.0, self.media_w, self.media_h)

    # --- landmarks ------------------------------------------------------
    @property
    def fold_x(self) -> float:
        return self.bleed + self.page_w

    def trim(self, side: str) -> Rect:
        """The visible (post-trim) area of one page, in canvas coordinates."""
        x = self.bleed if side == "left" else self.fold_x
        return Rect(x, self.bleed, self.page_w, self.page_h)

    def safe(self, side: str) -> Rect:
        t = self.trim(side)
        m = self.safe_margin
        return Rect(t.x + m, t.y + m, t.w - 2 * m, t.h - 2 * m)

    def full_bleed(self, side: str) -> Rect:
        """One page, bleeding on all four sides.

        The inner edge deliberately runs 1 bleed past the fold so that a
        mis-trim cannot open a white gap at the seam.
        """
        x = 0.0 if side == "left" else self.page_w
        return Rect(x, 0.0, self.media_w, self.canvas_h)

    @property
    def spread_bleed(self) -> Rect:
        return Rect(0.0, 0.0, self.canvas_w, self.canvas_h)

    # --- typographic margins -------------------------------------------
    # Outer margins are wider than gutter margins: the book opens flat, so the
    # gutter needs less relief than the thumb edge. Proportional to page width
    # so the same templates work at 21x21 and 30x30. Kept tight: the
    # photographs are the design, and wide margins read as wasted paper.
    @property
    def margin_outer(self) -> float:
        return round(self.page_w * 0.04, 2)

    @property
    def margin_gutter(self) -> float:
        return round(self.page_w * 0.042, 2)

    @property
    def margin_top(self) -> float:
        return round(self.page_h * 0.04, 2)

    @property
    def margin_bottom(self) -> float:
        return round(self.page_h * 0.05, 2)

    def text_column(self, side: str) -> Rect:
        """Inset content area of one page, respecting asymmetric margins."""
        t = self.trim(side)
        left = self.margin_outer if side == "left" else self.margin_gutter
        right = self.margin_gutter if side == "left" else self.margin_outer
        return Rect(
            t.x + left,
            t.y + self.margin_top,
            t.w - left - right,
            t.h - self.margin_top - self.margin_bottom,
        )

    def crosses_fold(self, r: Rect) -> bool:
        return r.x < self.fold_x < r.x2

    def spans_fold(self, r: Rect) -> bool:
        """True only if the image is genuinely readable on BOTH pages.

        A single-page full-bleed slot deliberately runs one bleed past the fold
        so a mis-trim cannot open a white gap. That is not spanning — after
        trimming, none of it is visible on the facing page. Spanning means real
        picture content lands on both sides of the fold, which is what puts a
        subject in the crease.
        """
        left = min(r.x2, self.fold_x) - max(r.x, self.trim("left").x)
        right = min(r.x2, self.trim("right").x2) - max(r.x, self.fold_x)
        eps = 0.01
        return left > self.bleed + eps and right > self.bleed + eps


def mm_to_px(mm: float, dpi: float) -> int:
    return int(round(mm / MM_PER_INCH * dpi))


def effective_dpi(px_w: int, px_h: int, slot: Rect) -> float:
    """DPI of an image scaled with object-fit: cover into a slot.

    Cover scales uniformly until both axes are filled, so the rendered size is
    driven by whichever axis is proportionally tighter.
    """
    if px_w <= 0 or px_h <= 0 or slot.w <= 0 or slot.h <= 0:
        return 0.0
    ar_img = px_w / px_h
    ar_slot = slot.w / slot.h
    if ar_img > ar_slot:
        # image is wider than the slot: height is the binding constraint
        rendered_h_mm = slot.h
        return px_h / (rendered_h_mm / MM_PER_INCH)
    rendered_w_mm = slot.w
    return px_w / (rendered_w_mm / MM_PER_INCH)


def spine_width_mm(
    page_count: int, leaf_thickness_mm: float, board_mm: float
) -> float:
    """Spine = leaves x leaf thickness + two boards.

    Layflat mounts two printed pages back-to-back into one leaf, so leaves are
    page_count / 2. Computed, never guessed — but the constants come from the
    lab config and must be verified against a physical book.
    """
    if page_count % 2:
        raise ValueError("page_count must be even")
    leaves = page_count // 2
    return round(leaves * leaf_thickness_mm + 2 * board_mm, 2)


# How much of a photograph a slot may cut away before the photograph is fitted
# inside the slot instead. object-fit: cover on a 4:3 picture in a square slot
# discards a quarter of the frame, and that quarter is very often the person at
# the edge of a group. Below this fraction the slot shrinks to the photograph's
# own shape, centred, so nothing is lost and the page keeps its white space.
MIN_CROP_KEEP = 0.82

# What the page compositor (compose.adaptive) lets itself cut away when it
# sizes a slot, and what the renderer must therefore allow too: the two used
# to disagree (0.75 sized, 0.82 re-fitted), so a portrait sized to fill its
# page was shrunk back inside white at render time. A photograph alone on its
# page may lose a quarter of its long side; one of several, 14%.
CROP_KEEP_SINGLE = 0.75
CROP_KEEP_MULTI = 0.86


def crop_keep(slot: Rect, aspect: float) -> float:
    """Fraction of the photograph's long cropped axis that survives a cover fit."""
    slot_ar = slot.w / slot.h
    return min(slot_ar, aspect) / max(slot_ar, aspect)


def fit_rect(slot: Rect, aspect: float, min_keep: float = MIN_CROP_KEEP) -> Rect:
    """The rectangle a photograph of `aspect` (w/h) should actually occupy.

    The slot itself when a cover crop keeps at least `min_keep` of the frame;
    otherwise the largest rectangle of the photograph's own shape inside the
    slot, centred.
    """
    if aspect <= 0 or crop_keep(slot, aspect) >= min_keep:
        return slot
    if aspect > slot.w / slot.h:
        w, h = slot.w, slot.w / aspect
    else:
        w, h = slot.h * aspect, slot.h
    return Rect(round(slot.x + (slot.w - w) / 2, 2), round(slot.y + (slot.h - h) / 2, 2),
                round(w, 2), round(h, 2))
