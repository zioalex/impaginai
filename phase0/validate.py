# SPDX-FileCopyrightText: 2026 Alessandro Surace
# SPDX-License-Identifier: AGPL-3.0-only
"""Print constraints as executable checks.

Everything in here exists because a prompt cannot be trusted to enforce it.
The model is asked politely to keep faces out of the fold; this file is what
actually stops a book going to the lab at 140 DPI.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image

import imaging  # registers HEIC/HEIF decoders on import
import templates_lib
from geometry import Geometry, effective_dpi
from schema import Layout

# The pixel limit is set once, in imaging.py.

LEVELS = ("error", "warning", "info")


@dataclass
class Issue:
    level: str
    code: str
    where: str
    message: str

    def __str__(self) -> str:
        tag = {"error": "ERROR", "warning": "WARN ", "info": "info "}[self.level]
        return f"{tag} [{self.code}] {self.where}: {self.message}"


@dataclass
class PhotoInfo:
    id: str
    path: Path
    width: int
    height: int
    taken_at: object = None     # datetime | None, for the chronology check

    @property
    def orientation(self) -> str:
        r = self.width / self.height
        if r > 1.05:
            return "landscape"
        if r < 0.95:
            return "portrait"
        return "square"


def load_photos(photo_dir: Path) -> dict[str, PhotoInfo]:
    """Index a directory of originals by filename stem."""
    exts = imaging.EXTENSIONS
    out: dict[str, PhotoInfo] = {}
    for p in sorted(photo_dir.iterdir()):
        if p.suffix.lower() not in exts:
            continue
        taken = None
        with Image.open(p) as im:
            w, h = im.size
            # EXIF orientation 5-8 means the stored buffer is rotated.
            try:
                ex = im.getexif()
                orient = ex.get(274, 1)
                # DateTimeOriginal lives in the Exif sub-IFD, not the top level.
                raw = ex.get_ifd(34665).get(36867)
                if raw:
                    taken = datetime.strptime(str(raw).strip(), "%Y:%m:%d %H:%M:%S")
            except Exception:
                orient = 1
            if orient in (5, 6, 7, 8):
                w, h = h, w
            if imaging.rotation_of(p) in (90, 270):
                w, h = h, w
        out[p.stem] = PhotoInfo(p.stem, p, w, h, taken)
    return out


@dataclass
class Report:
    issues: list[Issue]
    min_dpi: float
    page_count: int
    empty_pages: list[int] = None       # pages with no photograph at all
    bare_pages: list[int] = None        # pages with neither photograph nor text

    @property
    def blocking(self) -> bool:
        return any(i.level == "error" for i in self.issues)

    def print_report(self) -> None:
        for level in LEVELS:
            for i in self.issues:
                if i.level == level:
                    print(i)
        empty = self.empty_pages or []
        bare = self.bare_pages or []
        pct = 100 * len(empty) / self.page_count if self.page_count else 0
        print(
            f"\n{self.page_count} pages · lowest effective resolution "
            f"{self.min_dpi:.0f} DPI · {len(empty)} page(s) without a photograph "
            f"({pct:.0f}%), {len(bare)} carrying nothing at all · "
            + ("BLOCKED" if self.blocking else "ready for export")
        )
        if bare:
            print(f"  pages carrying nothing: {bare}")


def check(
    layout: Layout,
    photos: dict[str, PhotoInfo],
    g: Geometry,
    lab: dict,
    groups: dict[str, str] | None = None,
) -> Report:
    """`groups` maps photo_id to a moment/place cluster id. When supplied, a
    spread that mixes clusters is flagged: it usually means the album jumped
    location or day mid-page, which reads as clutter however well it is laid out."""
    issues: list[Issue] = []
    # Composed pages are laid out for the photographs' real shapes. Measured
    # here, from the same files the renderer reads, so the DPI checked is the
    # DPI printed (render.py calls this before it draws anything).
    templates_lib.attach_aspects(layout.spreads, {k: (v.width, v.height) for k, v in photos.items()})
    res = lab["resolution"]
    pages_cfg = lab["pages"]
    allow_fold = bool(lab.get("safety", {}).get("allow_fold_crossing", False))
    min_dpi_seen = float("inf")

    # --- page count ---------------------------------------------------
    pc = layout.page_count
    if pc < pages_cfg["min"]:
        issues.append(Issue("error", "page-count", "book", f"{pc} pages, lab minimum is {pages_cfg['min']}"))
    if pc > pages_cfg["max"]:
        issues.append(Issue("error", "page-count", "book", f"{pc} pages, lab maximum is {pages_cfg['max']}"))
    if pc % pages_cfg["increment"]:
        issues.append(Issue("error", "page-count", "book", f"{pc} pages is not a multiple of {pages_cfg['increment']}"))

    # --- per spread -----------------------------------------------------
    for sp in layout.spreads:
        where = f"spread {sp.index} ({sp.template})"
        rendered = templates_lib.build(sp.template, g, sp.options)
        spec = templates_lib.REGISTRY[sp.template]

        for i, (slot, box) in enumerate(zip(sp.slots, rendered.slots)):
            slot_where = f"{where} slot {i + 1}"
            photo = photos.get(slot.photo_id)
            if photo is None:
                issues.append(Issue("error", "missing-photo", slot_where, f"no file for photo id {slot.photo_id!r}"))
                continue

            dpi = effective_dpi(photo.width, photo.height, box.rect)
            min_dpi_seen = min(min_dpi_seen, dpi)
            size = f"{box.rect.w:.0f}x{box.rect.h:.0f}mm"
            if dpi < res["block_below_dpi"]:
                issues.append(Issue("error", "resolution", slot_where, f"{photo.id} is {dpi:.0f} DPI at {size} (block below {res['block_below_dpi']})"))
            elif dpi < res["warn_below_dpi"]:
                issues.append(Issue("warning", "resolution", slot_where, f"{photo.id} is {dpi:.0f} DPI at {size} (target {res['target_dpi']})"))

            want = box.aspect
            if want != "any" and photo.orientation != want:
                issues.append(Issue("info", "aspect", slot_where, f"{photo.id} is {photo.orientation}, template prefers {want} — cover-crop will discard a lot"))

            # The fold. A single-page bleed running one bleed past the crease is
            # fine — it is trimmed away. A picture with real content on both
            # sides of the crease is not, because the paper bends there and the
            # subject goes with it.
            if g.spans_fold(box.rect):
                if sp.template == templates_lib.USER_SPREAD:
                    # The user chose "two pages" for this photograph; the fold
                    # is theirs to accept. Only the subject is checked: a
                    # keep-box astride the crease is folded in two.
                    keeps = (sp.options or {}).get("keeps") or []
                    kb = keeps[0] if keeps and keeps[0] else None
                    if kb:
                        lo = box.rect.x + kb[0] * box.rect.w
                        hi = box.rect.x + kb[2] * box.rect.w
                        slack = 0.03 * box.rect.w
                        if lo - slack < g.fold_x < hi + slack:
                            issues.append(Issue("warning", "fold", slot_where, f"the subject of {photo.id} sits on the fold of its two-page spread"))
                elif not allow_fold:
                    issues.append(Issue("error", "fold-crossing", slot_where, f"{photo.id} is printed across the fold; allow_fold_crossing is off"))
                else:
                    focal_x_mm = box.rect.x + slot.focal[0] * box.rect.w
                    if abs(focal_x_mm - g.fold_x) < g.fold_band:
                        issues.append(Issue("warning", "fold", slot_where, f"focal point sits {abs(focal_x_mm - g.fold_x):.1f}mm from the fold"))

            if getattr(slot, "treatment", "none") != "none" and box.bleeds:
                issues.append(Issue("warning", "treatment", slot_where, f"{photo.id} has a '{slot.treatment}' treatment but bleeds off the page — the effect will be trimmed away"))

            if box.caption_style == "overlay" and slot.caption:
                issues.append(Issue("info", "caption", slot_where, "caption overlays the photo; check legibility on the proof"))

            # Partial bleed: an edge that lands inside the bleed but not on the
            # media edge trims to a white sliver.
            for edge, dist in (("left", box.rect.x), ("top", box.rect.y),
                               ("right", g.canvas_w - box.rect.x2), ("bottom", g.canvas_h - box.rect.y2)):
                if 0 < dist < g.bleed - 0.01:
                    issues.append(Issue("error", "partial-bleed", slot_where, f"{edge} edge is {dist:.2f}mm from the media edge — inside the bleed, will trim to a sliver"))

        if groups and len(sp.slots) > 1:
            seen = {groups.get(s.photo_id) for s in sp.slots if groups.get(s.photo_id)}
            if len(seen) > 1:
                issues.append(Issue("warning", "cohesion", where, "mixes photos from " + ", ".join(sorted(seen)) + " — different moment or place on one spread"))

        if spec.slot_count == 0 and not (rendered.texts or rendered.chart):
            issues.append(Issue("info", "empty", where, "no slots and no text"))

    # --- chronological order ---------------------------------------------
    # A travel album runs forward in time unless there is a reason not to.
    # The planner is told this; here it is checked, because an album that
    # jumps back and forth reads as shuffled rather than designed.
    # A spread is seen all at once, and a composed page puts its feature
    # first whatever its time: only the order across spreads is a sequence.
    seq = []
    for sp in layout.spreads:
        here = []
        for slot in sp.slots:
            info = photos.get(slot.photo_id)
            if info is not None and getattr(info, "taken_at", None):
                here.append((sp.index, slot.photo_id, info.taken_at))
        seq += sorted(here, key=lambda x: x[2])
    inversions = [
        (a, b) for (si, a, ta), (sj, b, tb) in zip(seq, seq[1:]) if tb < ta
    ]
    if inversions:
        shown = ", ".join(f"{a} before {b}" for a, b in inversions[:4])
        more = f" (+{len(inversions) - 4} more)" if len(inversions) > 4 else ""
        issues.append(Issue("warning", "chronology", "book", f"{len(inversions)} photo(s) run backwards in time: {shown}{more}"))

    # --- page density ---------------------------------------------------
    # A page that carries no photograph is a design choice; a book made mostly
    # of them is a slideshow with the lights off. Measured rather than eyeballed,
    # because the cost only becomes obvious once it is printed and paid for.
    has_photo: set[int] = set()
    has_text: set[int] = set()
    page_no = 0
    for sp in layout.spreads:
        rendered = templates_lib.build(sp.template, g, sp.options)
        text = sp.text
        for side in ("left", "right"):
            page_no += 1
            window = g.page_window(side)
            for slot, box in zip(sp.slots, rendered.slots):
                if slot.photo_id in photos and box.rect.intersects(window):
                    has_photo.add(page_no)
                    break
            if rendered.chart:
                # Calibration pages carry rulers and tone targets, not slots.
                has_text.add(page_no)
                continue
            for tb in rendered.texts:
                if tb.rect.intersects(window) and text and getattr(text, tb.key, None):
                    has_text.add(page_no)
                    break
    all_pages = set(range(1, page_no + 1))
    empty = sorted(all_pages - has_photo)
    bare = sorted(all_pages - has_photo - has_text)
    if pc and len(empty) / pc > 0.30:
        issues.append(Issue("warning", "density", "book", f"{len(empty)} of {pc} pages carry no photograph ({100 * len(empty) / pc:.0f}%) — the album is mostly empty paper"))
    if pc and len(bare) / pc > 0.15:
        issues.append(Issue("warning", "density", "book", f"{len(bare)} of {pc} pages carry neither photograph nor text: {bare}"))

    if min_dpi_seen == float("inf"):
        min_dpi_seen = 0.0
    return Report(issues, min_dpi_seen, pc, empty, bare)
