"""Photo ingest for the planning call.

Two jobs: read what the camera recorded, and work out what each photo is
physically capable of. The second one matters more than it sounds. Telling the
planner "p013 cannot fill a full-bleed page" up front prevents a class of
revision round-trips that would otherwise be discovered only by the validator,
after a render.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml
from PIL import Image, ExifTags

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "phase0"))

import imaging  # noqa: E402  (registers HEIC/HEIF decoders on import)
import templates_lib  # noqa: E402
from geometry import Geometry, effective_dpi  # noqa: E402

Image.MAX_IMAGE_PIXELS = None

_EXIF = {v: k for k, v in ExifTags.TAGS.items()}
_TAG_DATETIME = _EXIF.get("DateTimeOriginal", 36867)
_TAG_DATETIME_DIGITIZED = _EXIF.get("DateTimeDigitized", 36868)
_TAG_GPS = _EXIF.get("GPSInfo", 34853)
_IFD_EXIF = 34665          # ExifOffset — holds DateTimeOriginal
_IFD_GPS = 34853           # GPSInfo


@dataclass
class Photo:
    id: str
    path: Path
    width: int
    height: int
    taken_at: datetime | None = None
    gps: tuple[float, float] | None = None
    place: str | None = None
    caption: str = ""
    upload_order: int = 0
    avoid: list[str] = field(default_factory=list)
    group: str | None = None                       # from cluster.assign
    # Which of the UI's size choices this photograph actually has the pixels
    # for: {"full-page": "ok"|"soft"|"no", ...}. Computed, never guessed, so the
    # editor can refuse a choice before the user makes it rather than failing at
    # export when the book is otherwise finished.
    can: dict = field(default_factory=dict)
    annotation: str = ""                           # from annotate.run
    strength: int = 0                              # 1-5, from annotate.run
    suggested_focal: tuple[float, float] | None = None

    unusable: bool = False

    @property
    def orientation(self) -> str:
        r = self.width / self.height
        return "landscape" if r > 1.05 else "portrait" if r < 0.95 else "square"

    def prompt_line(self) -> str:
        bits = [
            self.id,
            self.group or "g?",
            f"{self.width}x{self.height}",
            self.orientation,
            self.taken_at.strftime("%Y-%m-%d %H:%M") if self.taken_at else "no date",
            self.place or ("gps" if self.gps else "no place"),
        ]
        if self.annotation:
            bits.append(self.annotation)
        if self.suggested_focal:
            bits.append(f"focal {self.suggested_focal[0]:.2f},{self.suggested_focal[1]:.2f}")
        if self.unusable:
            bits.append("TOO SMALL TO PRINT — leave it out")
        elif self.avoid:
            bits.append("too small for: " + ", ".join(self.avoid))
        return " | ".join(bits) + " | " + (self.caption or "(no caption)")


def _ratio(v) -> float:
    try:
        return float(v)
    except Exception:
        return 0.0


def _gps(raw: dict) -> tuple[float, float] | None:
    """`raw` is the GPS IFD itself: {1: ref, 2: (d,m,s), 3: ref, 4: (d,m,s)}."""
    if not raw:
        return None
    def dms(vals, ref, neg):
        d = _ratio(vals[0]) + _ratio(vals[1]) / 60 + _ratio(vals[2]) / 3600
        return -d if ref in neg else d
    try:
        lat = dms(raw[2], raw[1], ("S",))
        lon = dms(raw[4], raw[3], ("W",))
        return round(lat, 6), round(lon, 6)
    except Exception:
        return None


def read_dir(photo_dir: Path) -> list[Photo]:
    exts = imaging.EXTENSIONS
    candidates = [p for p in sorted(photo_dir.iterdir()) if p.suffix.lower() in exts]
    if (problem := imaging.check(candidates)):
        raise SystemExit(problem)
    out: list[Photo] = []
    for order, p in enumerate(candidates):
        with Image.open(p) as im:
            w, h = im.size
            # DateTimeOriginal and GPS live in their own sub-IFDs, NOT in the
            # top-level one. Reading only `getexif()` finds the orientation tag
            # and nothing else, which looks like it works — every photo simply
            # comes back undated, and the clustering silently degrades to
            # upload order instead of failing loudly.
            exif, gps_ifd = {}, {}
            try:
                top = im.getexif()
                exif = dict(top)
                exif.update(top.get_ifd(_IFD_EXIF))
                gps_ifd = dict(top.get_ifd(_IFD_GPS))
            except Exception:
                pass
            if exif.get(274, 1) in (5, 6, 7, 8):
                w, h = h, w
        taken = None
        raw = exif.get(_TAG_DATETIME) or exif.get(_TAG_DATETIME_DIGITIZED)
        if raw:
            try:
                taken = datetime.strptime(str(raw).strip(), "%Y:%m:%d %H:%M:%S")
            except ValueError:
                pass
        out.append(Photo(p.stem, p, w, h, taken, _gps(gps_ifd), None, "", order))
    return out


def apply_captions(photos: list[Photo], captions_file: Path) -> None:
    """captions.yaml is `photo_id: caption`, optionally with an `order:` list.

    The user's order is a hint to the planner, not an instruction — the whole
    premise is that the AI may resequence.
    """
    data = yaml.safe_load(captions_file.read_text()) or {}
    order = data.pop("order", None) if isinstance(data, dict) else None
    by_id = {p.id: p for p in photos}
    for pid, caption in (data or {}).items():
        if pid in by_id:
            by_id[pid].caption = str(caption or "").strip()
    if order:
        rank = {pid: i for i, pid in enumerate(order)}
        for p in photos:
            p.upload_order = rank.get(p.id, len(rank) + p.upload_order)
        photos.sort(key=lambda p: p.upload_order)


def size_capability(photos: list[Photo], g: Geometry, warn_dpi: float, block_dpi: float) -> None:
    """What each photograph can physically be printed as.

    Measured against the real slot rectangles, not a rule of thumb:
      full-page -> the full-bleed rectangle
      large     -> the feature slot of a composed page
      small     -> the smallest slot in the library
    """
    import compose

    full = g.full_bleed("right")
    large = templates_lib.build("single-centered", g, {}).slots[0].rect
    smalls = []
    for name, spec in templates_lib.REGISTRY.items():
        if not spec.planner_visible or spec.slot_count == 0:
            continue
        for b in templates_lib.build(name, g, {}).slots:
            smalls.append(b.rect)
    smallest = min(smalls, key=lambda r: r.w * r.h)

    for p in photos:
        p.can = {}
        for key, rect in (("full-page", full), ("large", large), ("small", smallest)):
            dpi = effective_dpi(p.width, p.height, rect)
            p.can[key] = "ok" if dpi >= warn_dpi else ("soft" if dpi >= block_dpi else "no")
            p.can[key + "_dpi"] = round(dpi)
        p.can["auto"] = "ok"


def annotate_limits(photos: list[Photo], g: Geometry, warn_dpi: float) -> None:
    """Mark which templates — or which slots of them — each photo is too small for.

    Every slot is measured, not just the largest by area. Cover-cropping makes
    the slot's shape matter as much as its size: a landscape frame in a tall
    column is scaled by its height and loses most of its width, so the tall
    column can need far more pixels than a bigger, squarer slot beside it.

    `avoid` holds a template name when the photo fits none of its slots, and
    "template slot N" (1-based, the order photographs are listed) when it fits
    some positions but not that one.
    """
    rendered = {
        spec.name: templates_lib.build(spec.name, g, {})
        for spec in templates_lib.REGISTRY.values()
        if spec.planner_visible and spec.slot_count > 0
    }
    biggest = {n: max(b.rect.w * b.rect.h for b in r.slots) for n, r in rendered.items()}
    for p in photos:
        avoid, whole = [], 0
        for name in sorted(rendered, key=lambda n: -biggest[n]):
            bad = [i for i, b in enumerate(rendered[name].slots, 1)
                   if effective_dpi(p.width, p.height, b.rect) < warn_dpi]
            if not bad:
                continue
            if len(bad) == len(rendered[name].slots):
                avoid.append(name)
                whole += 1
            else:
                avoid.extend(f"{name} slot {i}" for i in bad)
        p.avoid = avoid
        p.unusable = whole == len(rendered)


def load(photo_dir: Path, captions_file: Path | None, g: Geometry, warn_dpi: float,
         block_dpi: float = 150.0) -> list[Photo]:
    photos = read_dir(photo_dir)
    if captions_file and captions_file.exists():
        apply_captions(photos, captions_file)
    annotate_limits(photos, g, warn_dpi)
    size_capability(photos, g, warn_dpi, block_dpi)
    return photos
