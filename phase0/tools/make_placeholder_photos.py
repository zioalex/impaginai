#!/usr/bin/env python3
"""Generate stand-in photos so the print chain can be exercised end to end.

Each image carries an edge band and corner markers, so any cover-crop is
immediately visible on the proof: if the band is gone on one side, the layout
is throwing away that part of the frame. p013 is deliberately small — it exists
to prove the resolution validator actually blocks a bad export.

Replace these with real photographs before ordering. Keep the ids.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# A bold sans from wherever this system keeps one. The path used to be the
# Linux one only, so the script (and every test that needs these photographs)
# failed on macOS and Windows. Pillow's own built-in font is the last resort.
FONTS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
)


def font(size: int):
    for path in FONTS:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size)

SPECS = [
    ("p001", 6000, 2400, (38, 70, 92)),     # panorama
    ("p002", 4000, 5000, (96, 78, 62)),
    ("p003", 4000, 5000, (60, 88, 74)),
    ("p004", 5600, 3700, (120, 74, 60)),
    ("p005", 4500, 4500, (70, 70, 96)),
    ("p006", 5200, 3500, (44, 60, 70)),
    ("p007", 3600, 4800, (110, 96, 66)),
    ("p008", 4000, 2700, (52, 84, 88)),
    ("p009", 4000, 2700, (88, 62, 74)),
    ("p010", 4000, 2700, (66, 90, 62)),
    ("p011", 3000, 3000, (100, 84, 70)),
    ("p012", 3000, 3000, (56, 72, 100)),
    ("p013", 700, 700, (120, 60, 60)),       # too small on purpose
    ("p014", 3000, 3000, (78, 92, 84)),
    ("p015", 5000, 3300, (48, 58, 66)),
]


def make(pid: str, w: int, h: int, rgb: tuple[int, int, int], out: Path) -> None:
    im = Image.new("RGB", (w, h), rgb)
    d = ImageDraw.Draw(im)
    # gradient wash so focal-point shifts are visible
    for i in range(0, h, 4):
        t = i / h
        d.rectangle([0, i, w, i + 4], fill=tuple(int(c * (0.75 + 0.5 * t)) for c in rgb))

    band = int(min(w, h) * 0.045)
    d.rectangle([band, band, w - band, h - band], outline=(255, 255, 255), width=max(2, band // 8))
    for x, y in ((0, 0), (w, 0), (0, h), (w, h)):
        d.ellipse([x - band, y - band, x + band, y + band], fill=(255, 255, 255))

    f_big = font(int(min(w, h) * 0.3))
    f_small = font(int(min(w, h) * 0.045))
    d.text((w / 2, h / 2), pid, font=f_big, fill=(255, 255, 255), anchor="mm")
    d.text((w / 2, h * 0.72), f"{w}x{h}", font=f_small, fill=(255, 255, 255), anchor="mm")
    d.line([(w / 2, 0), (w / 2, h)], fill=(255, 255, 255, 90), width=2)
    d.line([(0, h / 2), (w, h / 2)], fill=(255, 255, 255, 90), width=2)

    im.save(out / f"{pid}.jpg", "JPEG", quality=92)


if __name__ == "__main__":
    out = Path(__file__).resolve().parents[1] / "photos"
    out.mkdir(exist_ok=True)
    for spec in SPECS:
        make(*spec, out)
    print(f"wrote {len(SPECS)} placeholder photos to {out}")
