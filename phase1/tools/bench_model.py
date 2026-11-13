#!/usr/bin/env python3
"""Score a model on the job this project actually needs doing.

Published benchmarks measure OCR, chart reading and exam questions. None of
them measure "obeys a 9,000-character layout brief", which is the only
capability that decides whether a cheap model can replace an expensive one
here. So measure it directly.

    # one model
    python phase1/tools/bench_model.py --photos phase1/demo_photos \
           --base-url openrouter --model z-ai/glm-5.3-flash

    # several, compared
    python phase1/tools/bench_model.py --photos phase1/demo_photos \
           --base-url openrouter \
           --compare anthropic/claude-haiku-5.5 deepseek/deepseek-v4.1-flash \
                     z-ai/glm-5.3-flash qwen/qwen3.6-35b-a3b

    # a local model
    python phase1/tools/bench_model.py --photos phase1/demo_photos \
           --base-url ollama --model qwen3-vl:32b

What is scored, in the order that matters:

  SCHEMA      did it return JSON this project can load at all
  PRINTABLE   does the validator pass it for print (resolution, bleed, pages)
  FOLD        did it keep every photograph off the gutter
  DENSITY     did it stay inside the empty-page budget
  CHRONOLOGY  does the album run forwards in time
  GROUPING    is each moment kept in one run, not scattered through the book
  COHESION    does each spread draw from one moment/place group
  VARIETY     did it use more than one or two templates
  SOFTNESS    is every photograph printed sharp enough for the lab
  COVER       did it design a cover at all

A model can score SCHEMA and fail everything else — valid JSON that ignores the
brief. That is the failure mode worth catching, because retrying never fixes it
and the result is a book that looks arbitrary.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT.parent / "phase0"))
sys.path.insert(0, str(ROOT))

import cluster
import geocode
import photos as PH
import planner
import providers
import render as R
from schema import Layout
from validate import check, load_photos


@dataclass
class Score:
    model: str
    ok: bool = False
    error: str = ""
    seconds: float = 0.0
    attempts: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    spreads: int = 0
    pages: int = 0
    checks: dict[str, tuple[bool, str]] = field(default_factory=dict)

    @property
    def passed(self) -> int:
        return sum(1 for ok, _ in self.checks.values() if ok)

    @property
    def total(self) -> int:
        return len(self.checks)


def evaluate(layout: Layout, ph, g, lab, groups) -> dict[str, tuple[bool, str]]:
    out: dict[str, tuple[bool, str]] = {}
    by_photo = {pid: grp.id for grp in groups for pid in grp.photo_ids}
    rep = check(layout, load_photos(Path(PHOTO_DIR)), g, lab, by_photo)

    errs = [i for i in rep.issues if i.level == "error"]
    out["PRINTABLE"] = (not errs, f"{len(errs)} blocking error(s)" if errs
                        else f"{rep.page_count}pp, min {rep.min_dpi:.0f} DPI")

    fold = [i for i in rep.issues if i.code == "fold-crossing"]
    out["FOLD"] = (not fold, f"{len(fold)} across the gutter" if fold else "none in the gutter")

    empty = len(rep.empty_pages or [])
    budget = max(2, rep.page_count // 6)
    out["DENSITY"] = (empty <= budget,
                      f"{empty} empty page(s), budget {budget}")

    chron = [i for i in rep.issues if i.code == "chronology"]
    out["CHRONOLOGY"] = (not chron, chron[0].message[:42] if chron else "runs forwards")

    coh = [i for i in rep.issues if i.code == "cohesion"]
    out["COHESION"] = (not coh, f"{len(coh)} mixed spread(s)" if coh else "one group per spread")

    used = {sp.template for sp in layout.spreads}
    out["VARIETY"] = (len(used) >= 4, f"{len(used)} template(s)")

    # Photographs printed softer than the lab's warn threshold. The prompt
    # tells the model which templates each frame is too small for; ignoring
    # that produces a book that is valid and slightly blurry.
    soft = [i for i in rep.issues if i.code == "resolution"]
    out["SOFTNESS"] = (not soft, f"{len(soft)} photo(s) below target DPI" if soft
                       else "every photograph sharp enough")

    # "Groups stay in one run" is an explicit instruction. A model that
    # scatters a moment through the book breaks the album's narrative even
    # when every individual spread is cohesive.
    seq = [by_photo.get(s.photo_id) for sp in layout.spreads for s in sp.slots]
    seq = [x for x in seq if x]
    runs, seen, broken = [], set(), []
    for gid in seq:
        if not runs or runs[-1] != gid:
            if gid in seen:
                broken.append(gid)
            runs.append(gid)
            seen.add(gid)
    out["GROUPING"] = (not broken,
                       f"{len(set(broken))} group(s) split across the book" if broken
                       else f"{len(runs)} group(s) in one run each")

    out["COVER"] = (bool(layout.cover and layout.cover.front_photo_id),
                    "designed" if layout.cover and layout.cover.front_photo_id else "missing")
    return out


PHOTO_DIR = ""


def run_one(model: str, args, ph, g, lab, groups) -> Score:
    sc = Score(model=model)
    prov = providers.resolve(provider=args.provider, model=model,
                             base_url=args.base_url, api_key=args.api_key,
                             vision=False, json_mode=args.json_mode)
    if not prov.usable:
        sc.error = "no API key"
        return sc
    t0 = time.time()
    try:
        res = planner.plan(title=args.title, subtitle=None, fmt=args.format,
                           language=args.language, photos=ph, g=g, lab=lab,
                           complete=providers.make_completer(prov),
                           target_spreads=args.spreads, groups=groups)
    except Exception as exc:
        sc.seconds = time.time() - t0
        sc.error = f"{type(exc).__name__}: {str(exc)[:120]}"
        sc.checks["SCHEMA"] = (False, "never produced valid JSON")
        return sc
    sc.seconds = time.time() - t0
    sc.attempts = res.usage.attempts
    sc.tokens_in, sc.tokens_out = res.usage.input_tokens, res.usage.output_tokens
    sc.spreads, sc.pages = len(res.layout.spreads), res.layout.page_count
    sc.checks["SCHEMA"] = (True, f"valid after {res.usage.attempts} attempt(s)")
    sc.checks.update(evaluate(res.layout, ph, g, lab, groups))
    sc.ok = True
    if args.save:
        out = Path(args.save) / f"{model.replace('/', '_')}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(res.layout.model_dump(mode="json"),
                                  ensure_ascii=False, indent=1))
    return sc


def report(scores: list[Score]) -> None:
    names = [k for k in ("SCHEMA", "PRINTABLE", "FOLD", "DENSITY", "CHRONOLOGY",
                         "GROUPING", "COHESION", "VARIETY", "SOFTNESS", "COVER")]
    w = max(len(s.model) for s in scores) + 1
    print()
    print(f"{'model':{w}s} {'score':>6s} {'try':>4s} {'sec':>6s} {'tok in/out':>14s}  "
          + " ".join(n[:4] for n in names))
    print("-" * (w + 40 + 5 * len(names)))
    for s in sorted(scores, key=lambda x: (-x.passed, x.seconds)):
        if not s.checks:
            print(f"{s.model:{w}s} {'-':>6s} {'-':>4s} {s.seconds:6.1f} {'-':>14s}  {s.error}")
            continue
        marks = " ".join(("  ok" if s.checks.get(n, (False, ""))[0] else "FAIL")
                         for n in names)
        print(f"{s.model:{w}s} {s.passed:>3d}/{s.total:<2d} {s.attempts:>4d} "
              f"{s.seconds:6.1f} {s.tokens_in:>6,}/{s.tokens_out:<6,}  {marks}")

    print("\nwhere they failed:")
    for s in sorted(scores, key=lambda x: -x.passed):
        bad = [(n, why) for n, (ok, why) in s.checks.items() if not ok]
        if s.error and not s.checks:
            print(f"  {s.model}: {s.error}")
        elif bad:
            print(f"  {s.model}:")
            for n, why in bad:
                print(f"      {n:11s} {why}")
        else:
            print(f"  {s.model}: nothing — {s.spreads} spreads / {s.pages} pages")


def main() -> int:
    global PHOTO_DIR
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--photos", type=Path, required=True)
    ap.add_argument("--captions", type=Path, default=None)
    ap.add_argument("--album", type=Path, default=Path("/tmp/bench-album"))
    ap.add_argument("--lab", type=Path, default=ROOT.parent / "phase0" / "labs" / "saal.yaml")
    ap.add_argument("--format", default="21x21")
    ap.add_argument("--language", default="de")
    ap.add_argument("--title", default="Benchmark")
    ap.add_argument("--spreads", type=int, default=None)
    ap.add_argument("--compare", nargs="+", default=None, metavar="MODEL",
                    help="several models, scored side by side")
    ap.add_argument("--save", default=None, metavar="DIR",
                    help="write each model's layout here, to look at afterwards")
    providers.add_arguments(ap)
    args = ap.parse_args()

    PHOTO_DIR = str(args.photos)
    args.album.mkdir(parents=True, exist_ok=True)
    lab = R.load_lab(args.lab)
    g = R.geometry_for(lab, args.format)
    caps = args.captions or (args.photos / "captions.yaml")
    ph = PH.load(args.photos, caps, g, lab["resolution"]["warn_below_dpi"],
                 lab["resolution"]["block_below_dpi"])
    geocode.apply(ph, args.album)
    groups = cluster.assign(ph)

    # Annotations are reused if the album has them, so the comparison is fair:
    # every model sees identical input.
    import annotate
    anns = annotate.Annotations.load(args.album / "annotations.json")
    annotate.apply(ph, anns)

    print(f"{len(ph)} photographs · {len(groups)} groups · {args.format} · "
          f"{len(anns.by_id)} annotation(s) available")
    print(f"planning prompt: ~{len(__import__('prompt').system_prompt(g, lab, args.language)) // 4:,} "
          f"tokens of instructions")

    models = args.compare or [args.model or providers.DEFAULT_MODELS[
        providers.resolve(provider=args.provider, base_url=args.base_url).name]]
    scores = []
    for m in models:
        print(f"\n--- {m} ---")
        sc = run_one(m, args, ph, g, lab, groups)
        if sc.error:
            print(f"    {sc.error}")
        else:
            print(f"    {sc.passed}/{sc.total} checks, {sc.seconds:.1f}s, "
                  f"{sc.attempts} attempt(s)")
        scores.append(sc)

    report(scores)
    best = max(scores, key=lambda s: s.passed, default=None)
    if best and best.checks:
        print(f"\nbest here: {best.model} ({best.passed}/{best.total})")
    print("\nA model scoring SCHEMA and little else returns valid JSON that ignores\n"
          "the brief. Retrying will not fix that; pick a different model.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
