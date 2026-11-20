"""Spike: animate a pile of photos flying in to form a face (photomosaic).

    python face_mosaic.py TARGET.jpg PHOTO_DIR out.mp4 [--grid 48] [--size 960]
    python face_mosaic.py --demo out.mp4      # synthetic tiles, no photos needed

Steps:
 1. Cut the target into a grid of cells; describe each cell by a 2x2 grid of
    mean Lab colours (keeps some edge direction, not just one average).
 2. Describe every photo the same way (centre-cropped square).
 3. Assign photos to cells with the Hungarian algorithm, so every photo is
    used about once (more cells than photos -> photos repeat evenly).
 4. Tint each tile part-way toward its cell colour: real photo collections
    are too uniform in colour to draw a face otherwise. This is the knob
    that trades "photos visible" against "face recognisable".
 5. Render frames: tiles start scattered and large, fly in with stagger
    (from the face centre outward), land; then a zoom in to show the photos. Frames are
    piped to ffmpeg.
"""
import argparse
import math
import random
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.optimize import linear_sum_assignment


# ---------- colour features ----------
def to_lab(rgb):
    """sRGB uint8 (...,3) -> CIE Lab float."""
    c = rgb.astype(np.float32) / 255.0
    c = np.where(c > 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)
    m = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
    xyz = c @ m.T / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1)


def feature(img_rgb_small):
    """2x2 mean Lab of a square image -> 12 numbers."""
    lab = to_lab(np.asarray(img_rgb_small.resize((2, 2), Image.BOX)))
    return lab.reshape(-1)


def square(img):
    w, h = img.size
    s = min(w, h)
    return img.crop(((w - s) // 2, (h - s) // 2, (w - s) // 2 + s, (h - s) // 2 + s))


# ---------- inputs ----------
def load_photos(folder, tile_px):
    exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
    paths = sorted(p for p in Path(folder).rglob("*") if p.suffix.lower() in exts)
    tiles = []
    for p in paths:
        try:
            im = Image.open(p)
            im.draft("RGB", (tile_px * 4, tile_px * 4))  # fast JPEG downscale
            tiles.append(square(im.convert("RGB")).resize((tile_px, tile_px), Image.LANCZOS))
        except Exception as e:  # skip unreadable files
            print("skip", p, e, file=sys.stderr)
    return tiles


def demo_inputs(n, tile_px):
    """Target = astronaut portrait; 'photos' = random crops of other images."""
    from skimage import data
    target = Image.fromarray(data.astronaut())
    sources = [data.chelsea(), data.coffee(), data.rocket(), data.hubble_deep_field(),
               data.immunohistochemistry(), data.retina(), data.colorwheel(), data.astronaut()]
    sources = [Image.fromarray(s) for s in sources]
    rng = random.Random(1)
    tiles = []
    for _ in range(n):
        s = rng.choice(sources)
        w, h = s.size
        z = rng.randint(min(w, h) // 6, min(w, h) // 2)
        x, y = rng.randint(0, w - z), rng.randint(0, h - z)
        t = s.crop((x, y, x + z, y + z)).resize((tile_px, tile_px), Image.LANCZOS)
        # brightness jitter so the set spans dark..light like a real album
        k = rng.uniform(0.3, 1.6)
        t = Image.fromarray(np.clip(np.asarray(t) * k, 0, 255).astype(np.uint8))
        tiles.append(t)
    return target, tiles


# ---------- mosaic ----------
def build_mosaic(target, tiles, grid, tint):
    tile_px = tiles[0].size[0]
    tgt = square(target.convert("RGB")).resize((grid * tile_px, grid * tile_px), Image.LANCZOS)
    cells, cell_feats = [], []
    for r in range(grid):
        for c in range(grid):
            box = (c * tile_px, r * tile_px, (c + 1) * tile_px, (r + 1) * tile_px)
            cell = tgt.crop(box)
            cells.append(cell)
            cell_feats.append(feature(cell))
    cell_feats = np.array(cell_feats)
    tile_feats = np.array([feature(t) for t in tiles])

    # Every photo is placed once before any photo is placed twice: assign in
    # rounds, each round giving each photo at most one of the still-free cells.
    choice = np.full(len(cells), -1)
    free = np.arange(len(cells))
    while len(free):
        cost = ((cell_feats[free][:, None, :] - tile_feats[None, :, :]) ** 2).sum(-1)
        rows, cols = linear_sum_assignment(cost)
        choice[free[rows]] = cols
        free = np.flatnonzero(choice < 0)

    placed = []
    for i, ti in enumerate(choice):
        t = np.asarray(tiles[ti]).astype(np.float32)
        # Shift the tile's mean colour toward the cell's (keeps photo detail),
        # then blend a little of the cell itself in.
        cm = np.asarray(cells[i]).astype(np.float32)
        t = t + tint * (cm.mean((0, 1)) - t.mean((0, 1)))
        t = (1 - tint * 0.35) * t + tint * 0.35 * cm
        placed.append(Image.fromarray(np.clip(t, 0, 255).astype(np.uint8)))
    print(f"cells={len(cells)} photos={len(tiles)} distinct used={len(set(choice))}")
    return placed, tgt


# ---------- animation ----------
def ease_out_cubic(x):
    return 1 - (1 - x) ** 3


def render(placed, grid, size, out, fps=30, fly_s=6.0, hold_s=1.5, zoom_s=2.5, seed=0):
    rng = random.Random(seed)
    cell = size / grid
    n = len(placed)
    centre = (grid - 1) / 2
    # order: centre of the face first, spreading outward, with some noise
    dist = [math.hypot(i % grid - centre, i // grid - centre) + rng.uniform(0, 6) for i in range(n)]
    order = np.argsort(np.argsort(dist)) / n  # 0..1 rank
    fly_frames = int(fly_s * fps)
    travel = 0.35  # each tile spends this fraction of the fly phase moving
    starts = []
    for i in range(n):
        ang = rng.uniform(0, 2 * math.pi)
        rad = size * rng.uniform(0.75, 1.1)
        starts.append((size / 2 + rad * math.cos(ang), size / 2 + rad * math.sin(ang), rng.uniform(2.5, 5)))

    # pre-scaled versions so we don't resize 2000 tiles every frame
    scales = [1.0, 1.5, 2.2, 3.2, 4.6]
    cache = [[t.resize((max(1, round(cell * s)),) * 2, Image.BILINEAR) for s in scales] for t in placed]
    final = Image.new("RGB", (size, size))
    for i, t in enumerate(cache):
        final.paste(t[0], (round(i % grid * cell), round(i // grid * cell)))
    tp = placed[0].size[0]  # full-resolution mosaic for a crisp zoom
    big = Image.new("RGB", (grid * tp, grid * tp))
    for i, t in enumerate(placed):
        big.paste(t, (i % grid * tp, i // grid * tp))

    ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                           "-s", f"{size}x{size}", "-r", str(fps), "-i", "-", "-c:v", "libx264",
                           "-pix_fmt", "yuv420p", "-crf", "20", out], stdin=subprocess.PIPE)
    bg = (12, 12, 16)
    for f in range(fly_frames):
        p = f / (fly_frames - 1)
        frame = Image.new("RGB", (size, size), bg)
        flying = []
        for i in range(n):
            t0 = order[i] * (1 - travel)
            k = (p - t0) / travel
            if k <= 0:
                continue
            tx, ty = i % grid * cell, i // grid * cell
            if k >= 1:
                frame.paste(cache[i][0], (round(tx), round(ty)))
                continue
            e = ease_out_cubic(k)
            sx, sy, s0 = starts[i]
            s = s0 + (1 - s0) * e
            x = sx + (tx - sx) * e - (s - 1) * cell / 2
            y = sy + (ty - sy) * e - (s - 1) * cell / 2
            flying.append((s, i, x, y))
        for s, i, x, y in sorted(flying, reverse=False):  # big (far) ones on top
            lvl = min(range(len(scales)), key=lambda j: abs(scales[j] - s))
            frame.paste(cache[i][lvl], (round(x), round(y)))
        ff.stdin.write(frame.tobytes())
    for _ in range(int(hold_s * fps)):
        ff.stdin.write(final.tobytes())
    # Zoom into the centre of the full-resolution mosaic until the single
    # photos are visible.
    zf = int(zoom_s * fps)
    for f in range(zf):
        z = 1 + (grid / 4 - 1) * ease_out_cubic(f / (zf - 1))  # zoom in to ~4 cells wide
        w = big.size[0] / z
        c = big.size[0] / 2
        crop = big.crop((c - w / 2, c - w / 2, c + w / 2, c + w / 2)).resize((size, size), Image.BILINEAR)
        ff.stdin.write(crop.tobytes())
    ff.stdin.close()
    ff.wait()
    return big


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("args", nargs="*")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--grid", type=int, default=48)
    ap.add_argument("--size", type=int, default=960)
    ap.add_argument("--tint", type=float, default=0.55)
    ap.add_argument("--photos", type=int, default=1500, help="demo only")
    a = ap.parse_args()
    tile_px = 64
    if a.demo:
        target, tiles = demo_inputs(a.photos, tile_px)
        out = a.args[0]
    else:
        target_path, folder, out = a.args
        target, tiles = Image.open(target_path), load_photos(folder, tile_px)
    placed, _ = build_mosaic(target, tiles, a.grid, a.tint)
    final = render(placed, a.grid, a.size, out)
    final.save(Path(out).with_suffix(".png"))
    print("wrote", out)


if __name__ == "__main__":
    main()
