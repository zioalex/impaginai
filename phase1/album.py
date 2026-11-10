#!/usr/bin/env python3
"""Phase 1 — the core loop, CLI only.

    export ANTHROPIC_API_KEY=...

    python album.py plan    --photos ./photos --captions captions.yaml \
                            --title "Sardinien 2026" --album ./albums/sardinien
    python album.py proof   --album ./albums/sardinien --photos ./photos
    python album.py revise  --album ./albums/sardinien --photos ./photos \
                            --comment "Seite 4 ist überladen, und die Kirchenbilder früher"
    python album.py check   [model flags]    # one tiny request per model
    python album.py history --album ./albums/sardinien
    python album.py rollback --album ./albums/sardinien --to 2

`plan` sends thumbnails; `revise` never does. `--dry-run` writes the prompt to
the album directory and stops, which is how you iterate on wording without
spending anything.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "phase0"))
sys.path.insert(0, str(HERE))

import render as R  # noqa: E402  (phase 0 renderer)
from schema import Layout  # noqa: E402
from validate import check, load_photos  # noqa: E402

import annotate  # noqa: E402
import cluster  # noqa: E402
import geocode  # noqa: E402
import overrides as OV  # noqa: E402
import pages as PG  # noqa: E402
import photos as PH  # noqa: E402
import planner  # noqa: E402
import providers  # noqa: E402
import prompt as P  # noqa: E402
from versions import Store  # noqa: E402

LAB = HERE.parent / "phase0" / "labs" / "saal.yaml"


def setup(args):
    lab = R.load_lab(args.lab)
    fmt = args.format or "21x21"
    g = R.geometry_for(lab, fmt)
    warn = float(lab["resolution"]["warn_below_dpi"])
    captions = args.captions if getattr(args, "captions", None) else args.photos / "captions.yaml"
    ph = PH.load(args.photos, captions, g, warn)
    if not ph:
        raise SystemExit(f"no photos found in {args.photos}")
    # Place names before clustering: the group label carries the location.
    named = geocode.apply(ph, args.album, offline_db=not getattr(args, "no_geocode", False))
    ov = OV.Overrides.load(args.album)
    ph = OV.apply_to_photos(ph, ov)
    groups = cluster.assign(ph, args.gap_hours, args.radius_m)
    anns = annotate.Annotations.load(args.album / "annotations.json")
    annotate.apply(ph, anns)
    if named:
        print(f"{named} photo(s) located", end="")
        print(f", {len(ov.by_id)} with custom settings" if ov.by_id else "")
    return lab, g, fmt, ph, groups, anns, ov


def make_thumbnails(ph, dst: Path, long_edge: int = 1200) -> Path:
    """1200px sRGB JPEGs for the vision path. Same derivatives the proof render
    uses, so nothing is generated twice."""
    from PIL import Image

    dst.mkdir(parents=True, exist_ok=True)
    for p in ph:
        out = dst / f"{p.id}.jpg"
        if out.exists():
            continue
        with Image.open(p.path) as im:
            im = R.to_srgb(im)
            im.thumbnail((long_edge, long_edge), Image.LANCZOS)
            im.save(out, "JPEG", quality=80, icc_profile=R.SRGB_BYTES, optimize=True)
    return dst


def do_annotate(args) -> int:
    lab, g, fmt, ph, groups, _, ov = setup(args)
    roles = providers.resolve_all(args, getattr(args, "models", None))
    prov = roles["annotate"]
    print(f"annotate: {prov.describe()}")
    if not prov.vision:
        print("this model cannot see images — nothing to annotate")
        return 0
    px = args.vision_px
    thumbs = annotate.build_vision_copies(
        ph, args.album / "derivatives" / f"vision{px}", px)
    est = annotate.estimate_tokens(ph, thumbs)
    print(f"looking at {len(ph)} photographs at {px}px, "
          f"{annotate.BATCH} per call (~{est:,} image tokens)")
    anns, usage = annotate.run(ph, thumbs, args.album / "annotations.json",
                               providers.make_completer(prov), force=args.force)
    print(f"  {usage.line(priced=prov.name == 'anthropic' and not prov.base_url)}")
    print(f"  {len(anns.by_id)} annotations cached — planning and every revision from "
          f"here on is text only")
    return 0


def do_plan(args) -> int:
    lab, g, fmt, ph, groups, anns, ov = setup(args)
    store = Store(args.album)
    roles = providers.resolve_all(args, getattr(args, "models", None))
    prov = roles["plan"]
    print(f"{len(ph)} photos, {sum(1 for p in ph if p.caption)} captioned, format {fmt}")
    print(providers.describe_roles(roles))
    print(f"{len(groups)} moment/place groups:")
    for grp in groups[:8]:
        print(f"  {grp.id}: {grp.label}")
    if len(groups) > 8:
        print(f"  ... and {len(groups) - 8} more")
    if not anns.by_id:
        print("no annotations cached — run `annotate` first for subject and people "
              "grouping, or continue with captions and timestamps alone")
    limited = [p for p in ph if p.avoid]
    if limited:
        print(f"{len(limited)} photo(s) too small for the largest templates — the planner is told")

    if args.dry_run:
        sysmsg = P.system_prompt(g, lab, args.language)
        user = P.plan_user_content(args.title or args.album.name, args.subtitle, fmt,
                                   args.language, ph, args.spreads, groups)
        out = args.album / "prompt-plan.txt"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(f"=== SYSTEM ===\n{sysmsg}\n\n=== USER ===\n{user}\n")
        print(f"dry run — prompt written to {out} ({len(sysmsg) + len(user):,} chars, "
              f"{'thumbnails attached' if args.with_thumbnails else 'text only'})")
        return 0

    min_spreads = lab["pages"]["min"] // 2
    if args.spreads and args.spreads < min_spreads:
        print(f"--spreads {args.spreads} is below the lab minimum; asking for {min_spreads}")
        args.spreads = min_spreads

    thumb_dir = None
    if args.with_thumbnails and not prov.vision:
        print("--with-thumbnails ignored: this model cannot see images")
    elif args.with_thumbnails:
        thumb_dir = make_thumbnails(ph, args.album / "derivatives" / "proof")
        print(f"attaching {len(list(thumb_dir.glob('*.jpg')))} thumbnails — this pays "
              f"again on every retry; `annotate` is usually the better spend")

    print("planning the whole album in one call...")
    res = planner.plan(
        title=args.title or args.album.name, subtitle=args.subtitle, fmt=fmt,
        language=args.language, photos=ph, g=g, lab=lab,
        complete=providers.make_completer(prov),
        fallback=providers.make_fallback_completer(prov),
        target_spreads=args.spreads, thumb_dir=thumb_dir, groups=groups,
        log_dir=args.album / "logs",
    )
    data = finish(res.layout.model_dump(mode="json", exclude_none=False), ov, groups, ph, lab)
    v = store.add(data, parent=None, change_summary="initial plan")
    print(f"  {res.usage.line(priced=prov.name == 'anthropic' and not prov.base_url)}")
    print(f"  wrote v{v.version_no:03d} — {len(res.layout.spreads)} spreads / {res.layout.page_count} pages")
    return report(res.layout, args, g, lab, groups)


def do_revise(args) -> int:
    lab, g, fmt, ph, groups, _, ov = setup(args)
    store = Store(args.album)
    parent, current = store.get(args.from_version)
    print(f"revising v{parent.version_no:03d} (text only, no images)")
    roles = providers.resolve_all(args, getattr(args, "models", None))
    prov = roles["revise"]
    print(f"revise: {prov.describe(mention_vision=False)}")
    res = planner.revise(
        current=current, comment=args.comment, photos=ph, g=g, lab=lab,
        language=args.language, complete=providers.make_completer(prov),
        fallback=providers.make_fallback_completer(prov), groups=groups, log_dir=args.album / "logs",
    )
    data = finish(res.layout.model_dump(mode="json", exclude_none=False), ov, groups, ph, lab)
    v = store.add(data, parent=parent.version_no, user_comment=args.comment,
                  change_summary=res.layout.change_summary)
    print(f"  {res.usage.line(priced=prov.name == 'anthropic' and not prov.base_url)}")
    print(f"  wrote v{v.version_no:03d}: {res.layout.change_summary or '(no summary returned)'}")
    return report(res.layout, args, g, lab, groups)


def do_check(args) -> int:
    """Send one tiny request to each distinct role model; exit 1 if plan fails."""
    roles = providers.resolve_all(args, getattr(args, "models", None))
    print(providers.describe_roles(roles))
    seen: dict[tuple, str] = {}
    code = 0
    for name in ("plan", "revise", "annotate"):
        prov = roles[name]
        key = (prov.model, prov.base_url)
        if key in seen:
            print(f"{name}: same model as {seen[key]}")
            continue
        seen[key] = name
        if not prov.usable:
            print(f"{name}: skipped — no API key")
            if name == "plan":
                code = 1
            continue
        r = providers.preflight(prov)
        print(f"{name}: {'ok' if r.ok else 'FAILED'} ({r.seconds:.1f}s) — {r.message}")
        if name == "plan" and not r.ok:
            code = 1
    return code


def finish(data: dict, ov, groups, photos, lab=None) -> dict:
    """Everything the model does not decide: the user's own instructions, the
    measured focal points, closing up empty pages, and the location stamps.
    Re-run on every version so an override set once holds."""
    notes = OV.apply_to_layout(data, ov)
    refocused = PG.apply_focal(data, {p.id: p for p in photos})
    notes += PG.repack(data, groups, lab)
    for n in notes:
        print(f"  {n}")
    if refocused:
        print(f"  {refocused} focal point(s) taken from the photo annotations")
    return data


def report(layout: Layout, args, g, lab, groups=None) -> int:
    by_photo = {pid: grp.id for grp in (groups or []) for pid in grp.photo_ids}
    rep = check(layout, load_photos(args.photos), g, lab, by_photo)
    rep.print_report()
    templates = {}
    for sp in layout.spreads:
        templates[sp.template] = templates.get(sp.template, 0) + 1
    print("\ntemplate mix: " + ", ".join(f"{k} x{v}" for k, v in sorted(templates.items(), key=lambda kv: -kv[1])))
    used = {s.photo_id for sp in layout.spreads for s in sp.slots}
    print(f"{len(used)} photos placed")
    return 1 if rep.blocking else 0


def do_proof(args) -> int:
    lab, g, fmt, _, groups, _, ov = setup(args)
    store = Store(args.album)
    v, data = store.get(args.version)
    layout = Layout.model_validate(data)
    tmp = args.album / f"layout-v{v.version_no:03d}.json"
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    print(f"rendering proof of v{v.version_no:03d}")
    argv = ["proof", "--layout", str(tmp), "--photos", str(args.photos),
            "--lab", str(args.lab), "--out", str(args.album / "renders"),
            "--format", fmt]
    return _run_phase0(argv)


def _run_phase0(argv: list[str]) -> int:
    saved = sys.argv
    sys.argv = ["render.py"] + argv
    try:
        return R.main()
    finally:
        sys.argv = saved


def do_history(args) -> int:
    print(Store(args.album).history())
    return 0


def do_rollback(args) -> int:
    v = Store(args.album).rollback(args.to)
    print(f"wrote v{v.version_no:03d} restoring v{args.to}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, photos=True):
        p.add_argument("--album", type=Path, required=True)
        if photos:
            p.add_argument("--photos", type=Path, required=True)
            p.add_argument("--captions", type=Path)
        p.add_argument("--lab", type=Path, default=LAB)
        p.add_argument("--gap-hours", type=float, default=4.0,
                       help="a gap longer than this starts a new moment group")
        p.add_argument("--radius-m", type=float, default=400.0,
                       help="GPS distance that starts a new place group")
        p.add_argument("--no-geocode", action="store_true",
                       help="skip place-name lookup entirely")
        p.add_argument("--format", default=None)
        p.add_argument("--language", default="de")
        providers.add_arguments(p)

    p = sub.add_parser("plan"); common(p)
    p.add_argument("--title"); p.add_argument("--subtitle")
    p.add_argument("--spreads", type=int, help="target spread count")
    p.add_argument("--with-thumbnails", action="store_true",
                   help="attach raw thumbnails to the planning call instead of using "
                        "cached annotations (expensive, repaid on every retry)")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=do_plan)

    p = sub.add_parser("annotate"); common(p)
    p.add_argument("--force", action="store_true", help="re-annotate everything")
    p.add_argument("--vision-px", type=int, default=annotate.VISION_PX,
                   help=f"long edge sent to the vision model (default "
                        f"{annotate.VISION_PX}; cost scales with area, not file size)")
    p.set_defaults(fn=do_annotate)

    p = sub.add_parser("revise"); common(p)
    p.add_argument("--comment", required=True)
    p.add_argument("--from-version", type=int, default=None)
    p.set_defaults(fn=do_revise)

    p = sub.add_parser("proof"); common(p)
    p.add_argument("--version", type=int, default=None)
    p.set_defaults(fn=do_proof)

    p = sub.add_parser("check")
    p.add_argument("--album", type=Path, default=None)  # accepted, not needed
    providers.add_arguments(p)
    p.set_defaults(fn=do_check)

    p = sub.add_parser("history"); common(p, photos=False)
    p.set_defaults(fn=do_history)

    p = sub.add_parser("rollback"); common(p, photos=False)
    p.add_argument("--to", type=int, required=True)
    p.set_defaults(fn=do_rollback)

    args = ap.parse_args()
    if args.album is not None:
        args.album.mkdir(parents=True, exist_ok=True)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
