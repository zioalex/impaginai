#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Alessandro Surace
# SPDX-License-Identifier: AGPL-3.0-only
"""Short videos that are really photographs.

A phone with the "live" or "motion" option on saves three seconds of video
where the owner meant to take a picture. This module is where those become
photographs: find the clips, read what they carry, and (in later steps) pick
their best frame. See docs/plans/clips-and-fuller-pages.md.

What exists today is the first step, the probe:

    python phase1/clips.py probe ~/Pictures/engadin

It changes nothing. It lists every video in the folder with what decides how
it can be used — length, size, codec, rotation, whether it is HDR, the date and
place it carries, and whether a photograph of the same name sits beside it —
and ends with a summary. Phones differ a great deal in all of these, and the
frame picker is built against what the real files turn out to be.
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "phase0"))

import imaging  # noqa: E402

# A video this short is a photograph taken with motion on. Longer ones are
# videos, and an album has no use for them.
MAX_CLIP_SECONDS = 6.0

# Transfer characteristics that mean HDR (ITU-T H.273): PQ and HLG. Decoded
# as if they were ordinary video, these frames come out grey and flat.
HDR_TRANSFERS = {16: "PQ (HDR10 / Dolby Vision)", 18: "HLG"}

_NAME_DATES = (
    (re.compile(r"(20\d{2})(\d{2})(\d{2})[_-]?(\d{2})(\d{2})(\d{2})"), True),   # 20260805_091233
    (re.compile(r"(20\d{2})(\d{2})(\d{2})"), False),                            # VID-20260805-WA0019
)


def parse_location(text: str | None) -> tuple[float, float] | None:
    """ISO 6709, as QuickTime and Android write it: "+46.4100+009.9400/"."""
    m = re.match(r"^([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)", (text or "").strip())
    if not m:
        return None
    lat, lon = float(m.group(1)), float(m.group(2))
    return (lat, lon) if -90 <= lat <= 90 and -180 <= lon <= 180 else None


def date_from_name(name: str) -> datetime | None:
    for rx, has_time in _NAME_DATES:
        m = rx.search(name)
        if not m:
            continue
        try:
            parts = [int(x) for x in m.groups()]
            return datetime(*parts) if has_time else datetime(*parts, 12, 0, 0)
        except ValueError:
            continue
    return None


def taken_at(meta: dict, name: str) -> tuple[datetime | None, str]:
    """When the clip was taken, in the local time a photograph's EXIF would
    show, and how sure that is.

    Photographs carry local time with no zone. Video containers carry UTC.
    Mixing the two puts a clip hours away from the photographs taken beside
    it, so the source is reported and the frame picker can correct for it."""
    apple = meta.get("com.apple.quicktime.creationdate")
    if apple:
        try:
            return datetime.fromisoformat(apple.replace("Z", "+00:00")).replace(tzinfo=None), \
                "local time, from the phone (reliable)"
        except ValueError:
            pass
    named = date_from_name(name)
    created = meta.get("creation_time")
    if created:
        try:
            utc = datetime.fromisoformat(created.replace("Z", "+00:00"))
            if utc.year > 1971:                     # some apps write the epoch
                utc = utc.astimezone(timezone.utc).replace(tzinfo=None)
                if named and abs(named - utc) < timedelta(hours=15):
                    # The file name was written in local time: trust its clock.
                    return named, "local time, from the file name"
                return utc, "UTC — may be hours off the photographs beside it"
        except ValueError:
            pass
    if named:
        return named, "date from the file name" + ("" if named.hour != 12 else " (no time of day)")
    return None, "no date"


def probe(path: Path) -> dict:
    """What one video file carries. Never raises: a file that cannot be read
    is reported with the reason."""
    out: dict = {"file": path.name, "ok": False}
    stem = path.stem.lower()
    partner = next((p.name for p in sorted(path.parent.iterdir())
                    if p.stem.lower() == stem and p.suffix.lower() in imaging.EXTENSIONS), None)
    out["photo_beside"] = partner
    try:
        import av
    except ImportError:
        out["error"] = "PyAV is not installed (pip install av)"
        return out
    try:
        with av.open(str(path)) as c:
            if not c.streams.video:
                out["error"] = "no video stream"
                return out
            v = c.streams.video[0]
            cc = v.codec_context
            meta = {**dict(v.metadata), **dict(c.metadata)}
            seconds = float(c.duration) / av.time_base if c.duration else (
                float(v.duration * v.time_base) if v.duration else 0.0)
            frame = next(c.decode(v), None)
            rotation = int(getattr(frame, "rotation", 0) or 0) % 360 if frame is not None else 0
            w, h = v.width, v.height
            if rotation in (90, 270):
                w, h = h, w
            trc = getattr(cc, "color_trc", None)
            when, how = taken_at(meta, path.name)
            out.update(
                ok=True, seconds=round(seconds, 2),
                clip=0 < seconds <= MAX_CLIP_SECONDS,
                width=w, height=h, megapixels=round(w * h / 1e6, 1),
                fps=round(float(v.average_rate), 1) if v.average_rate else None,
                frames=v.frames or None, codec=cc.name, rotation=rotation,
                hdr=HDR_TRANSFERS.get(trc), bits=getattr(cc.format, "bits_per_pixel", None)
                and max((p.bits for p in cc.format.components), default=8),
                taken_at=when.isoformat(sep=" ", timespec="seconds") if when else None,
                date_source=how,
                gps=parse_location(meta.get("com.apple.quicktime.location.ISO6709")
                                   or meta.get("location")),
                decodes=frame is not None,
            )
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
    return out


def find(folder: Path) -> list[Path]:
    return sorted(p for p in Path(folder).iterdir()
                  if p.is_file() and p.suffix.lower() in imaging.CLIP_EXTENSIONS
                  and not p.name.startswith("."))


def print_cm(w: int, h: int, dpi: float) -> str:
    return f"{w / dpi * 2.54:.0f} × {h / dpi * 2.54:.0f} cm"


def report(rows: list[dict], warn_dpi: float = 200.0) -> str:
    """The probe's findings as text: one line per file, then what they mean."""
    if not rows:
        return "No videos here (looked for " + ", ".join(sorted(imaging.CLIP_EXTENSIONS)) + ")."
    lines = []
    for r in rows:
        if not r["ok"]:
            lines.append(f"{r['file']}: cannot be read — {r.get('error')}")
            continue
        bits = [f"{r['seconds']:.1f}s", f"{r['width']}×{r['height']}", r["codec"]]
        if r["fps"]:
            bits.append(f"{r['fps']:g} fps")
        if r["rotation"]:
            bits.append(f"turned {r['rotation']}°")
        if r["hdr"]:
            bits.append(f"HDR {r['hdr']}")
        bits.append(r["taken_at"] or "no date")
        bits.append("GPS" if r["gps"] else "no GPS")
        if r["photo_beside"]:
            bits.append(f"photo beside it: {r['photo_beside']}")
        if not r["clip"]:
            bits.append(f"NOT a clip (longer than {MAX_CLIP_SECONDS:g}s)")
        lines.append(f"{r['file']}: " + " · ".join(bits))
    ok = [r for r in rows if r["ok"]]
    clips = [r for r in ok if r["clip"]]
    n = len(clips)
    out = lines + ["", f"{len(rows)} video file(s): {n} short enough to be a photograph, "
                       f"{len(ok) - n} longer, {len(rows) - len(ok)} unreadable."]
    if not clips:
        return "\n".join(out)
    paired = sum(1 for r in clips if r["photo_beside"])
    out.append(f"  {paired} have a photograph of the same name beside them (the photograph is used; "
               f"the clip offers other moments); {n - paired} stand alone (a frame becomes the photograph).")
    sizes: dict[tuple[int, int], int] = {}
    for r in clips:
        sizes[(r["width"], r["height"])] = sizes.get((r["width"], r["height"]), 0) + 1
    for (w, h), k in sorted(sizes.items(), key=lambda kv: -kv[1]):
        out.append(f"  {k} × {w}×{h} ({w * h / 1e6:.1f} MP): prints sharp up to "
                   f"{print_cm(w, h, warn_dpi)} at {warn_dpi:g} DPI.")
    hdr = sum(1 for r in clips if r["hdr"])
    if hdr:
        out.append(f"  {hdr} are HDR: their frames need tone-mapping or they print grey and flat.")
    sources: dict[str, int] = {}
    for r in clips:
        sources[r["date_source"]] = sources.get(r["date_source"], 0) + 1
    for s, k in sorted(sources.items(), key=lambda kv: -kv[1]):
        out.append(f"  date for {k}: {s}.")
    out.append(f"  {sum(1 for r in clips if r['gps'])} of {n} carry a place (GPS).")
    bad = [r["file"] for r in clips if not r["decodes"]]
    if bad:
        out.append(f"  could not decode a frame from: {', '.join(bad[:6])}")
    return "\n".join(out)


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "probe":
        folder = Path(sys.argv[2]).expanduser()
        if not folder.is_dir():
            print(f"{folder} is not a folder")
            return 2
        print(report([probe(p) for p in find(folder)]))
        return 0
    print("usage: python phase1/clips.py probe <folder>")
    return 2


if __name__ == "__main__":
    sys.exit(main())
