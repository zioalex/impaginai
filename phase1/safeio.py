"""Writing the files a user cannot afford to lose.

`Path.write_text` empties the file before it writes. A crash, a full disk or a
power cut in between leaves it empty or half written, and for `overrides.yaml`
or `versions.json` that is every choice the user made, or an album that no
longer opens. Here the new content goes to a temporary file beside the real
one, is flushed to disk, and replaces the old file in one step: a reader sees
the old content or the new, never part of either.
"""

from __future__ import annotations

import os
from pathlib import Path


def write_text(path: Path, text: str) -> None:
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()
