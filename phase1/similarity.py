"""Photographs that look alike, measured on the pixels.

The vision pass labels each photograph with a scene, a subject and who is in
it, and `pages.similar` has always trusted those words. Words miss a burst of
near-identical frames when the model words them differently, and match two
unrelated seascapes because both are "seascape". This adds the other signal: a
64-bit difference hash of a tiny greyscale copy, compared by Hamming distance.
It costs one small decode per photograph and needs no model.
"""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "phase0"))

import imaging  # noqa: E402,F401  (registers HEIC/HEIF decoders on import)

# Distance out of 64 bits. Frames of one burst differ by a few bits; the same
# scene from a step to the side by a dozen; unrelated photographs by ~30.
DUPLICATE_BITS = 6
SIMILAR_BITS = 12


@lru_cache(maxsize=4096)
def _dhash(path: str, mtime: float, size: int) -> int | None:
    try:
        with Image.open(path) as im:
            im.draft("L", (64, 64))          # JPEG: decode at a fraction of the size
            im = ImageOps.exif_transpose(im).convert("L").resize((9, 8), Image.LANCZOS)
            px = list(im.getdata())
    except Exception:
        return None
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (px[row * 9 + col] > px[row * 9 + col + 1])
    return bits


def dhash(path) -> int | None:
    """The photograph's difference hash, or None if it cannot be read."""
    p = Path(path)
    try:
        st = p.stat()
    except OSError:
        return None
    return _dhash(str(p), st.st_mtime, st.st_size)


def distance(a: int | None, b: int | None) -> int | None:
    """Bits that differ, or None when either hash is unknown."""
    if a is None or b is None:
        return None
    return (a ^ b).bit_count()


def features_for(photos) -> dict:
    """What `pages.repack` needs to judge two photographs alike: the words the
    vision pass gave them, and the hash of the pixels."""
    return {p.id: {"scene": p.scene, "subject": p.subject, "who": p.who,
                   "hash": dhash(p.path)} for p in photos}
