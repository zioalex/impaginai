#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Alessandro Surace
# SPDX-License-Identifier: AGPL-3.0-only
"""Synthetic 'photographs' with real EXIF, for demonstrating the pipeline.

These are not pretty pictures and are not meant to be. They exist so the demo
exercises the parts that matter: varied orientations and pixel sizes, real
DateTimeOriginal and GPS tags so the clustering has something to cluster, and
enough visual difference between scenes that a rendered spread can be judged
for rhythm rather than content.

Four locations over four days, mirroring how a trip actually gets shot: a burst
of frames around an event, then hours of nothing.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta
from pathlib import Path

import piexif
from PIL import Image, ImageDraw, ImageFilter

random.seed(7)

# (place, lat, lon, day, start hour)
SITES = [
    ("Cala Goloritze", 40.1150, 9.6900, 0, 9),
    ("Porto Vecchio", 40.1230, 9.7200, 1, 7),
    ("Santa Maria", 40.1800, 9.6100, 2, 11),
    ("Capo Monte", 40.2400, 9.5500, 3, 18),
]

BASE = datetime(2026, 6, 8, 0, 0)


def grad(w, h, top, bottom):
    im = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(im)
    for y in range(h):
        t = y / max(1, h - 1)
        d.line([(0, y), (w, y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    return im


def seascape(w, h, warm=False):
    sky = ((248, 196, 136), (236, 128, 110)) if warm else ((150, 196, 226), (214, 232, 240))
    im = grad(w, h, *sky)
    d = ImageDraw.Draw(im)
    hz = int(h * random.uniform(0.52, 0.62))
    sea = grad(w, h - hz, (36, 92, 116) if not warm else (120, 70, 92), (18, 54, 74) if not warm else (54, 36, 58))
    im.paste(sea, (0, hz))
    sx, sy = int(w * random.uniform(0.25, 0.75)), int(hz * random.uniform(0.3, 0.7))
    r = int(min(w, h) * 0.05)
    d.ellipse([sx - r, sy - r, sx + r, sy + r], fill=(255, 244, 214))
    for i in range(30):
        y = hz + int((h - hz) * (i / 30) ** 1.6)
        ln = int(w * random.uniform(0.05, 0.3))
        x = random.randint(0, w - ln)
        d.line([(x, y), (x + ln, y)], fill=(255, 255, 255), width=max(1, h // 600))
    # headland
    pts = [(0, hz)] + [(int(w * x / 10), hz - int(h * 0.05 * math.sin(x))) for x in range(11)] + [(w, hz)]
    d.polygon(pts + [(w, hz + 2), (0, hz + 2)], fill=(58, 72, 58))
    return im


def architecture(w, h):
    im = grad(w, h, (226, 216, 198), (198, 182, 158))
    d = ImageDraw.Draw(im)
    base = int(h * 0.92)
    for i in range(random.randint(3, 6)):
        bw = int(w * random.uniform(0.12, 0.26))
        x = int(w * i / 6) + random.randint(-20, 20)
        bh = int(h * random.uniform(0.35, 0.75))
        tone = random.randint(150, 215)
        d.rectangle([x, base - bh, x + bw, base], fill=(tone, tone - 14, tone - 34))
        for row in range(3):
            for col in range(2):
                wx = x + int(bw * (0.22 + 0.42 * col))
                wy = base - bh + int(bh * (0.2 + 0.24 * row))
                d.rectangle([wx, wy, wx + bw // 7, wy + bh // 12], fill=(62, 54, 48))
    d.rectangle([0, base, w, h], fill=(120, 104, 88))
    return im


def interior_detail(w, h):
    im = grad(w, h, (72, 62, 54), (34, 28, 26))
    d = ImageDraw.Draw(im)
    cx, cy = w // 2, h // 2
    r = int(min(w, h) * 0.34)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(196, 150, 96))
    for k in range(12):
        a = k * math.pi / 6
        d.line([(cx, cy), (cx + int(r * 0.9 * math.cos(a)), cy + int(r * 0.9 * math.sin(a)))],
               fill=(96, 70, 44), width=max(2, w // 300))
    return im.filter(ImageFilter.GaussianBlur(max(1, w // 900)))


def portrait_figure(w, h):
    im = grad(w, h, (238, 224, 196), (176, 196, 186))
    d = ImageDraw.Draw(im)
    cx = int(w * random.uniform(0.38, 0.62))
    hy = int(h * 0.30)
    hr = int(min(w, h) * 0.11)
    d.ellipse([cx - hr, hy - hr, cx + hr, hy + hr], fill=(64, 58, 56))
    d.polygon([(cx - hr * 1.9, h), (cx - hr * 1.1, hy + hr), (cx + hr * 1.1, hy + hr), (cx + hr * 1.9, h)],
              fill=(58, 54, 58))
    return im


SPECS = [
    # id, w, h, scene, site index, minute offset
    ("p001", 6000, 2200, "sea", 0, 0),
    ("p002", 4000, 5000, "portrait", 0, 12),
    ("p003", 5400, 3600, "sea", 0, 25),
    ("p004", 3600, 4500, "detail", 0, 38),
    ("p005", 5200, 3500, "sea", 0, 51),
    ("p006", 4800, 3200, "arch", 1, 0),
    ("p007", 3600, 4800, "arch", 1, 9),
    ("p008", 4200, 4200, "detail", 1, 17),
    ("p009", 5000, 3300, "arch", 1, 26),
    ("p010", 3800, 4700, "portrait", 1, 34),
    ("p011", 5600, 3700, "arch", 2, 0),
    ("p012", 3400, 3400, "detail", 2, 11),
    ("p013", 3400, 3400, "detail", 2, 19),
    ("p014", 4600, 3100, "arch", 2, 28),
    ("p015", 3200, 4000, "portrait", 2, 40),
    ("p016", 900, 900, "detail", 2, 44),      # too small on purpose
    ("p017", 6000, 2400, "sea", 3, 0),
    ("p018", 5200, 3500, "sea", 3, 8),
    ("p019", 4000, 5000, "portrait", 3, 15),
    ("p020", 4400, 2900, "sea", 3, 23),
    ("p021", 3600, 3600, "detail", 3, 31),
    ("p022", 5600, 3700, "sea", 3, 39),
]

MAKERS = {"sea": seascape, "arch": architecture, "detail": interior_detail, "portrait": portrait_figure}


def deg_to_dms(v):
    v = abs(v)
    d = int(v)
    m = int((v - d) * 60)
    s = round((v - d - m / 60) * 3600 * 100)
    return ((d, 1), (m, 1), (s, 100))


def main():
    out = Path(__file__).resolve().parents[1] / "demo_photos"
    out.mkdir(exist_ok=True)
    for pid, w, h, scene, site_i, mins in SPECS:
        place, lat, lon, day, hour = SITES[site_i]
        fn = MAKERS[scene]
        im = fn(w, h, warm=True) if scene == "sea" and site_i == 3 else fn(w, h)
        when = BASE + timedelta(days=day, hours=hour, minutes=mins)
        exif = {
            "0th": {piexif.ImageIFD.Make: b"DemoCam", piexif.ImageIFD.Model: b"Synthetic"},
            "Exif": {piexif.ExifIFD.DateTimeOriginal: when.strftime("%Y:%m:%d %H:%M:%S").encode()},
            "GPS": {
                piexif.GPSIFD.GPSLatitudeRef: b"N",
                piexif.GPSIFD.GPSLatitude: deg_to_dms(lat),
                piexif.GPSIFD.GPSLongitudeRef: b"E",
                piexif.GPSIFD.GPSLongitude: deg_to_dms(lon),
            },
            "1st": {}, "thumbnail": None,
        }
        im.save(out / f"{pid}.jpg", "JPEG", quality=92, exif=piexif.dump(exif))
    print(f"wrote {len(SPECS)} demo photos to {out}")


if __name__ == "__main__":
    main()
