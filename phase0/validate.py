"""Print constraints as executable checks.

Everything in here exists because a prompt cannot be trusted to enforce it.
The model is asked politely to keep faces out of the fold; this file is what
actually stops a book going to the lab at 140 DPI.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image

import templates_lib
from geometry import Geometry, Rect, effective_dpi
from schema import Layout

Image.MAX_IMAGE_PIXELS = None

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
    exts = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}
    out: dict[str, PhotoInfo] = {}
    for p in sorted(photo_dir.iterdir()):
        if p.suffix.lower() not in exts:
            continue
        with Image.open(p) as im:
            w, h = im.size
            # EXIF orientation 5-8 means the stored buffer is rotated.
            try:
                orient = im.getexif().get(274, 1)
            except Exception:
                orient = 1
            if orient in (5, 6, 7, 8):
                w, h = h, w
        out[p.stem] = PhotoInfo(p.stem, p, w, h)
    return out


@dataclass
class Report:
    issues: list[Issue]
    min_dpi: float
    page_count: int

    @property
    def blocking(self) -> bool:
        return any(i.level == "error" for i in self.issues)

    def print_report(self) -> None:
        for level in LEVELS:
            for i in self.issues:
                if i.level == level:
                    print(i)
        print(
            f"\n{self.page_count} pages · lowest effective resolution "
            f"{self.min_dpi:.0f} DPI · "
            + ("BLOCKED" if self.blocking else "ready for export")
        )


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
                if not allow_fold:
                    issues.append(Issue("error", "fold-crossing", slot_where, f"{photo.id} is printed across the fold; allow_fold_crossing is off"))
                else:
                    focal_x_mm = box.rect.x + slot.focal[0] * box.rect.w
                    if abs(focal_x_mm - g.fold_x) < g.fold_band:
                        issues.append(Issue("warning", "fold", slot_where, f"focal point sits {abs(focal_x_mm - g.fold_x):.1f}mm from the fold"))

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

    if min_dpi_seen == float("inf"):
        min_dpi_seen = 0.0
    return Report(issues, min_dpi_seen, pc)
