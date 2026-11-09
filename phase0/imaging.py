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

# Extensions treated as photographs, in one place so the reader and the
# validator cannot disagree about what is in an album. They disagreed once:
# .heic was listed by the loader but not the validator, so every HEIC
# photograph loaded fine and was then reported as a missing file.
EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp",
    ".heic", ".heif", ".avif",
}

HEIF_EXTENSIONS = {".heic", ".heif", ".avif"}

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
