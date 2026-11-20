# SPDX-FileCopyrightText: 2026 Alessandro Surace
# SPDX-License-Identifier: AGPL-3.0-only
"""Image formats: what counts as a photograph, and teaching Pillow to open it.

Importing this module registers every decoder the project needs. Six modules
open images and all of them import this first, because registration is global
but only happens when someone asks for it — and a half-registered process
fails with `UnidentifiedImageError` on the one file format phones actually
produce.

HEIC is the default on every iPhone since 2017 and on much of Android, so it is
not an edge case; it is the common case. Pillow cannot read it on its own and
needs `pillow-heif`. Without that package the project does not pretend the file
does not exist: it says plainly what is missing and how to install it, because
"cannot identify image file" does not tell anyone to run pip.
"""

from __future__ import annotations

import os

from PIL import Image

# Pillow refuses an image of more than twice this many pixels as a
# decompression bomb: a few kilobytes that decode to gigabytes. The limit was
# switched off entirely. 300 megapixels leaves room for any camera and for
# stitched panoramas; raise it with ALBUM_MAX_MEGAPIXELS if a real photograph
# is refused.
Image.MAX_IMAGE_PIXELS = int(float(os.environ.get("ALBUM_MAX_MEGAPIXELS", "300")) * 1_000_000)

# Extensions treated as photographs, in one place so the reader and the
# validator cannot disagree about what is in an album. They disagreed once:
# .heic was listed by the loader but not the validator, so every HEIC
# photograph loaded fine and was then reported as a missing file.
EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp",
    ".heic", ".heif", ".avif",
}

HEIF_EXTENSIONS = {".heic", ".heif", ".avif"}

# Videos that may be photographs taken with "live" or "motion" on. Not loaded
# as photographs yet: phase1/clips.py probes them, and the frame picker that
# turns one into a photograph is the next step of docs/plans/clips-and-fuller-pages.md.
CLIP_EXTENSIONS = {".mp4", ".mov", ".m4v", ".3gp"}

_registered = False
_heif_ok = False


def register() -> bool:
    """Register extra decoders with Pillow. Idempotent. True if HEIF works."""
    global _registered, _heif_ok
    if _registered:
        return _heif_ok
    _registered = True
    try:
        from pillow_heif import register_heif_opener

        register_heif_opener()
        _heif_ok = True
    except ImportError:
        _heif_ok = False
    return _heif_ok


def heif_available() -> bool:
    register()
    return _heif_ok


def check(paths) -> str | None:
    """Return an explanation if any of these files cannot be opened, else None.

    Called before a long run so the failure arrives as a sentence rather than
    as a traceback from inside a worker forty seconds later.
    """
    register()
    if _heif_ok:
        return None
    heic = [p for p in paths if p.suffix.lower() in HEIF_EXTENSIONS]
    if not heic:
        return None
    return (
        f"{len(heic)} of these photographs are HEIC/HEIF, which Pillow cannot read "
        f"on its own (first: {heic[0].name}).\n"
        f"    pip install pillow-heif\n"
        f"and run this again. HEIC is the default format on iPhones, so this is "
        f"worth installing even if only some of your photographs use it."
    )


# Registering on import is what makes `import imaging` enough at every call
# site. The cost is a few milliseconds and one optional import.
register()


# --- the user's own rotation ----------------------------------------------
#
# The EXIF orientation tag is honoured everywhere (ImageOps.exif_transpose).
# Some photographs carry a wrong one or none at all — a phone held sideways,
# a scan, a picture passed through a messenger that dropped the tag. For
# those the user turns the photograph by hand, and that turn is kept in
# `rotations.yaml` beside the photographs (like captions.yaml), as degrees
# clockwise, applied after the EXIF orientation:
#
#     p007: 90
#     p012: 270
#
# It is a property of the photograph, so every reader of the file sees it:
# the proof, the print, the thumbnails and what the vision model is shown.

ROTATIONS_FILE = "rotations.yaml"
_ROT_CACHE: dict[str, tuple[float, dict[str, int]]] = {}


def rotations(photo_dir) -> dict[str, int]:
    """Photo id -> degrees clockwise, for one directory of photographs."""
    from pathlib import Path

    f = Path(photo_dir) / ROTATIONS_FILE
    key = str(f.resolve())
    try:
        mtime = f.stat().st_mtime
    except OSError:
        _ROT_CACHE.pop(key, None)
        return {}
    hit = _ROT_CACHE.get(key)
    if hit and hit[0] == mtime:
        return hit[1]
    import yaml

    raw = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    out = {}
    for pid, deg in raw.items():
        try:
            d = int(deg) % 360
        except (TypeError, ValueError):
            continue
        if d in (90, 180, 270):
            out[str(pid)] = d
    _ROT_CACHE[key] = (mtime, out)
    return out


def set_rotation(photo_dir, pid: str, degrees: int) -> int:
    """Store the turn for one photograph; returns the normalised degrees."""
    from pathlib import Path

    import yaml

    d = int(degrees) % 360
    if d not in (0, 90, 180, 270):
        raise ValueError("rotation must be a multiple of 90 degrees")
    cur = dict(rotations(photo_dir))
    if d:
        cur[pid] = d
    else:
        cur.pop(pid, None)
    f = Path(photo_dir) / ROTATIONS_FILE
    if cur:
        f.write_text(yaml.safe_dump(dict(sorted(cur.items())), sort_keys=True), encoding="utf-8")
    else:
        f.unlink(missing_ok=True)
    _ROT_CACHE.pop(str(f.resolve()), None)
    return d


def rotation_of(path) -> int:
    """The user's turn for the photograph at `path` (0 when none)."""
    if not path:
        return 0
    from pathlib import Path

    p = Path(path)
    return rotations(p.parent).get(p.stem, 0)


def turn(im, degrees: int):
    """Rotate an upright (EXIF-applied) image clockwise by the given degrees."""
    from PIL import Image

    return {90: lambda: im.transpose(Image.Transpose.ROTATE_270),
            180: lambda: im.transpose(Image.Transpose.ROTATE_180),
            270: lambda: im.transpose(Image.Transpose.ROTATE_90)}.get(degrees, lambda: im)()


def turn_point(f, degrees: int):
    """A normalised point on a photograph, after turning it `degrees` clockwise."""
    x, y = float(f[0]), float(f[1])
    return {90: (1 - y, x), 180: (1 - x, 1 - y), 270: (y, 1 - x)}.get(int(degrees) % 360, (x, y))


def turn_box(box, degrees: int):
    """A normalised box [x0, y0, x1, y1] after turning the photograph clockwise."""
    if not box:
        return box
    a = turn_point((box[0], box[1]), degrees)
    b = turn_point((box[2], box[3]), degrees)
    return [round(min(a[0], b[0]), 4), round(min(a[1], b[1]), 4),
            round(max(a[0], b[0]), 4), round(max(a[1], b[1]), 4)]
