#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Alessandro Surace
# SPDX-License-Identifier: AGPL-3.0-only
"""Find the smallest image size that still annotates your photographs correctly.

Vision cost is driven by image DIMENSIONS — Anthropic charges roughly
(w x h) / 750 tokens, OpenAI-compatible endpoints tile at 512px — so shrinking
what you send is the single largest saving available in this project. JPEG
quality changes the upload and not the bill.

The question is how small is too small, and that depends on your photographs,
not on a table. Holiday snapshots of landscapes survive aggressive downscaling;
group shots where the model must count faces do not. So measure it:

    python phase1/tools/bench_vision.py --photos ~/Pictures/sardinien \
           --base-url ollama --model qwen3-vl:32b \
           --sizes 1200 768 512 384 --sample 12

The largest size is treated as the reference, and each smaller one is scored on
how often it agrees with it:

    SUBJECT   does it describe the same thing
    PEOPLE    does it count the same number of faces  <- usually the first to break
    SCENE     does it pick the same scene type
    FOCAL     does the focal point land within 10% of the frame
    STRENGTH  is the 1-5 rating within one point

Drop to the smallest size where PEOPLE and FOCAL still hold. Those two decide
cropping and page prominence; SUBJECT and SCENE drift harmlessly.
"""

from __future__ import annotations

import argparse
import math
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT.parent / "phase0"))
sys.path.insert(0, str(ROOT))

import annotate
import photos as PH
import providers
import render as R


def image_tokens(d: Path, ids) -> tuple[int, int, float]:
    """(anthropic tokens, openai tokens, megabytes) for one size's copies."""
    from PIL import Image

    a = o = 0
    b = 0
    for pid in ids:
        f = d / f"{pid}.jpg"
        if not f.exists():
            continue
        b += f.stat().st_size
        with Image.open(f) as im:
            w, h = im.size
        a += (w * h) // 750
        sc = min(1.0, 768 / max(w, h))
        tw, th = int(w * sc), int(h * sc)
        o += 85 + 170 * (((tw + 511) // 512) * ((th + 511) // 512))
    return a, o, b / 1e6


def agreement(ref: annotate.Annotation, got: annotate.Annotation) -> dict[str, bool]:
    ref_words = set(ref.subject.lower().split())
    got_words = set(got.subject.lower().split())
    overlap = len(ref_words & got_words) / max(1, len(ref_words))
    return {
        "SUBJECT": overlap >= 0.4,
        "PEOPLE": ref.people == got.people,
        "SCENE": ref.scene == got.scene,
        "FOCAL": math.dist(ref.focal, got.focal) <= 0.10,
        "STRENGTH": abs(ref.strength - got.strength) <= 1,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--photos", type=Path, required=True)
    ap.add_argument("--album", type=Path, default=Path("/tmp/bench-vision"))
    ap.add_argument("--lab", type=Path, default=ROOT.parent / "phase0" / "labs" / "saal.yaml")
    ap.add_argument("--format", default="21x21")
    ap.add_argument("--sizes", type=int, nargs="+", default=[1200, 768, 512, 384])
    ap.add_argument("--sample", type=int, default=12,
                    help="how many photographs to test (they are paid for at every size)")
    ap.add_argument("--seed", type=int, default=1)
    providers.add_arguments(ap)
    args = ap.parse_args()

    sizes = sorted(set(args.sizes), reverse=True)
    args.album.mkdir(parents=True, exist_ok=True)
    lab = R.load_lab(args.lab)
    g = R.geometry_for(args.lab and lab, args.format)
    ph = PH.load(args.photos, None, g, lab["resolution"]["warn_below_dpi"],
                 lab["resolution"]["block_below_dpi"])
    random.Random(args.seed).shuffle(ph)
    ph = ph[: args.sample]
    if not ph:
        raise SystemExit("no photographs found")

    prov = providers.resolve_role("annotate", args)
    if not prov.usable:
        raise SystemExit("no API key for the annotate role")
    if not prov.vision:
        raise SystemExit("this model has no vision; nothing to measure")
    print(f"{len(ph)} photographs · {prov.describe()}")
    print(f"reference size: {sizes[0]}px\n")

    complete = providers.make_completer(prov)
    results: dict[int, annotate.Annotations] = {}
    costs: dict[int, tuple[int, int, float]] = {}

    for px in sizes:
        d = annotate.build_vision_copies(ph, args.album / f"vision{px}", px)
        costs[px] = image_tokens(d, [p.id for p in ph])
        cache = args.album / f"ann{px}.json"
        if cache.exists():
            cache.unlink()
        anns, usage = annotate.run(ph, d, cache, complete, force=True)
        results[px] = anns
        a, o, mb = costs[px]
        print(f"  {px:>4d}px  {len(anns.by_id):>2d}/{len(ph)} annotated  "
              f"{a:>6,} anthropic tok  {o:>6,} openai tok  {mb:.2f} MB  "
              f"{usage.line(priced=False)}")

    ref_px = sizes[0]
    ref = results[ref_px]
    keys = ["SUBJECT", "PEOPLE", "SCENE", "FOCAL", "STRENGTH"]
    print(f"\nagreement with {ref_px}px:")
    print(f"  {'size':>6s} {'tok vs ref':>11s} " + " ".join(f"{k:>8s}" for k in keys))
    base_a = costs[ref_px][0] or 1
    for px in sizes:
        if px == ref_px:
            continue
        got = results[px]
        tally = {k: [0, 0] for k in keys}
        for pid, r in ref.by_id.items():
            gv = got.by_id.get(pid)
            if gv is None:
                continue
            for k, ok in agreement(r, gv).items():
                tally[k][1] += 1
                tally[k][0] += int(ok)
        cells = []
        for k in keys:
            hit, n = tally[k]
            cells.append(f"{(100 * hit // n) if n else 0:>7d}%")
        ratio = costs[px][0] / base_a
        print(f"  {px:>6d} {ratio:>10.2f}x " + " ".join(cells))

    print("\nPEOPLE and FOCAL are the ones that matter: they decide cropping and how\n"
          "prominently a photograph is placed. Take the smallest size where both\n"
          "still hold, and set it with --vision-px (or VISION_PX in annotate.py).")
    print("Note: JPEG quality changes the upload, not the token bill. Only pixels count.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
