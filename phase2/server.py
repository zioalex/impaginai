#!/usr/bin/env python3
"""The album editor: a local web app for someone who is not a programmer.

    python phase2/server.py --album ./albums/sardinien --photos ./photos
    -> open http://localhost:8000

Design decisions that matter more than the code:

**No build step, no CDN, no npm.** The page is one HTML file with inline CSS
and vanilla JavaScript. A Next.js front end would need `npm install` and a
network at setup time, and would break the "self-hosted, works offline"
requirement for the sake of conveniences this UI does not need. Everything here
runs from the same `pip install` as the rest of the project.

**The user edits photographs, not layouts.** The whole premise of the project is
that nobody positions a box. So the UI offers exactly four things per
photograph — a description, how big it should be, an optional treatment, and
whether to use it at all — and leaves every other decision to the planner. Four
controls can be learnt in one sitting; a layout editor cannot.

**Changes save as you make them.** There is no Save button, because a Save
button is a thing to forget. Each change PATCHes `overrides.yaml`, which is the
same file the CLI reads, so the UI and the command line are interchangeable.

**Long work is a job with real progress**, never a spinner: planning an album
takes a minute or two and the user needs to see that something is happening.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import traceback
from dataclasses import asdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "phase0"))
sys.path.insert(0, str(ROOT / "phase1"))

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

import annotate
import cluster
import geocode
import overrides as OV
import photos as PH
import planner
import render as R
from schema import Layout
from validate import check, load_photos
from versions import Store

app = FastAPI(title="Album editor")
STATE: dict = {}
JOB = {"running": False, "step": "", "progress": 0, "error": None, "done": False}
JOB_LOCK = threading.Lock()


# --- state ---------------------------------------------------------------


def reload_photos():
    a = STATE["album"]
    ph = PH.load(STATE["photos"], STATE["captions"], STATE["g"],
                 STATE["lab"]["resolution"]["warn_below_dpi"],
                 STATE["lab"]["resolution"]["block_below_dpi"])
    geocode.apply(ph, a, offline_db=STATE["geocode"])
    ov = OV.Overrides.load(a)
    # Keep excluded photographs visible in the UI — the user has to be able to
    # bring one back. Exclusion is applied when building, not when listing.
    for p in ph:
        p.override = ov.get(p.id)
    anns = annotate.Annotations.load(a / "annotations.json")
    annotate.apply(ph, anns)
    kept = [p for p in ph if not ov.get(p.id).exclude]
    STATE["groups"] = cluster.assign(kept, STATE["gap_hours"], STATE["radius_m"])
    gmap = {pid: grp for grp in STATE["groups"] for pid in grp.photo_ids}
    for p in ph:
        p.group = gmap[p.id].id if p.id in gmap else None
    STATE["all"] = ph
    STATE["ov"] = ov
    return ph


def thumb_dir() -> Path:
    return STATE["album"] / "derivatives" / "proof"


def ensure_thumbs(ph) -> None:
    from PIL import Image

    d = thumb_dir()
    d.mkdir(parents=True, exist_ok=True)
    for p in ph:
        out = d / f"{p.id}.jpg"
        if out.exists():
            continue
        with Image.open(p.path) as im:
            im = R.to_srgb(im)
            im.thumbnail((1200, 1200), Image.LANCZOS)
            im.save(out, "JPEG", quality=80, icc_profile=R.SRGB_BYTES, optimize=True)


def set_job(step: str, progress: int, error: str | None = None, done: bool = False):
    with JOB_LOCK:
        JOB.update(step=step, progress=progress, error=error, done=done,
                   running=not (done or error))


# --- API -----------------------------------------------------------------


@app.get("/api/album")
def api_album():
    store = Store(STATE["album"])
    versions = [asdict(v) for v in store.versions()]
    groups = [
        {"id": g.id, "label": g.label, "place": g.place,
         "dates": g.date_label, "count": len(g.photo_ids)}
        for g in STATE.get("groups", [])
    ]
    return {
        "title": STATE["title"],
        "subtitle": STATE["subtitle"],
        "format": STATE["fmt"],
        "language": STATE["language"],
        "groups": groups,
        "versions": versions,
        "has_annotations": bool(annotate.Annotations.load(STATE["album"] / "annotations.json").by_id),
        "has_key": STATE["has_key"],
        "sizes": list(OV.SIZES),
        "treatments": list(OV.TREATMENTS),
    }


@app.get("/api/photos")
def api_photos():
    out = []
    for p in STATE["all"]:
        o = STATE["ov"].get(p.id)
        out.append({
            "id": p.id,
            "group": p.group,
            "width": p.width,
            "height": p.height,
            "orientation": p.orientation,
            "taken_at": p.taken_at.isoformat(sep=" ", timespec="minutes") if p.taken_at else None,
            "place": p.place,
            "caption": p.caption,
            "description": o.description,
            "size": o.size,
            "treatment": o.treatment,
            "exclude": o.exclude,
            "strength": p.strength or None,
            "annotation": p.annotation or None,
            "too_small": p.unusable,
            "avoid": p.avoid,
            "can": p.can,
        })
    return out


class OverrideIn(BaseModel):
    description: str | None = None
    size: str | None = None
    treatment: str | None = None
    exclude: bool | None = None


@app.patch("/api/photos/{photo_id}")
def api_patch(photo_id: str, body: OverrideIn):
    if not any(p.id == photo_id for p in STATE["all"]):
        raise HTTPException(404, "no such photo")
    kw = {}
    if body.description is not None:
        kw["description"] = body.description.strip() or None
    if body.size is not None:
        if body.size not in OV.SIZES:
            raise HTTPException(400, f"size must be one of {OV.SIZES}")
        # Refuse a size the photograph does not have the pixels for, rather
        # than accepting it and failing at export with the book finished.
        photo = next(p for p in STATE["all"] if p.id == photo_id)
        if photo.can.get(body.size) == "no":
            dpi = photo.can.get(body.size + "_dpi")
            raise HTTPException(400, f"{photo_id} is {photo.width}x{photo.height} pixels — printed that "
                                     f"big it would be {dpi} DPI, too coarse to print. Choose a smaller size.")
        kw["size"] = body.size
    if body.treatment is not None:
        if body.treatment not in OV.TREATMENTS:
            raise HTTPException(400, f"treatment must be one of {OV.TREATMENTS}")
        kw["treatment"] = body.treatment
    if body.exclude is not None:
        kw["exclude"] = body.exclude
    STATE["ov"].set(photo_id, **kw)
    STATE["ov"].save()
    reload_photos()
    return {"ok": True}


@app.get("/thumb/{photo_id}.jpg")
def api_thumb(photo_id: str):
    f = thumb_dir() / f"{photo_id}.jpg"
    if not f.exists():
        raise HTTPException(404)
    return FileResponse(f, media_type="image/jpeg")


@app.get("/api/job")
def api_job():
    with JOB_LOCK:
        return dict(JOB)


def _build(do_plan: bool, comment: str | None):
    """The long job: annotate (once), plan or revise, apply overrides, render."""
    try:
        a = STATE["album"]
        ph = reload_photos()
        usable = [p for p in ph if not STATE["ov"].get(p.id).exclude]
        if not usable:
            raise RuntimeError("every photograph is excluded")

        set_job("Preparing photographs", 5)
        ensure_thumbs(usable)

        anns = annotate.Annotations.load(a / "annotations.json")
        todo = anns.missing(usable)
        if todo and STATE["has_key"]:
            set_job(f"Looking at {len(todo)} photographs", 15)
            anns, _ = annotate.run(usable, thumb_dir(), a / "annotations.json",
                                   planner.anthropic_completer(STATE["model"]))
        annotate.apply(usable, anns)

        store = Store(a)
        groups = STATE["groups"]
        if do_plan:
            set_job("Designing the album", 45)
            res = planner.plan(title=STATE["title"], subtitle=STATE["subtitle"],
                               fmt=STATE["fmt"], language=STATE["language"],
                               photos=usable, g=STATE["g"], lab=STATE["lab"],
                               complete=planner.anthropic_completer(STATE["model"]),
                               groups=groups)
            parent, comment_txt, summary = None, None, "initial plan"
        else:
            set_job("Revising the album", 45)
            pv, current = store.get()
            res = planner.revise(current=current, comment=comment or "", photos=usable,
                                 g=STATE["g"], lab=STATE["lab"], language=STATE["language"],
                                 complete=planner.anthropic_completer(STATE["model"]),
                                 groups=groups)
            parent, comment_txt, summary = pv.version_no, comment, res.layout.change_summary

        set_job("Applying your settings", 70)
        data = res.layout.model_dump(mode="json", exclude_none=False)
        OV.apply_to_layout(data, STATE["ov"])
        OV.stamp_locations(data, groups, {p.id: p for p in usable})
        v = store.add(data, parent=parent, user_comment=comment_txt, change_summary=summary)

        set_job("Printing the preview", 85)
        layout = Layout.model_validate(data)
        tmp = a / f"layout-v{v.version_no:03d}.json"
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        argv = ["render.py", "proof", "--layout", str(tmp), "--photos", str(STATE["photos"]),
                "--lab", str(STATE["lab_path"]), "--out", str(a / "renders"),
                "--format", STATE["fmt"]]
        saved, sys.argv = sys.argv, argv
        try:
            R.main()
        finally:
            sys.argv = saved
        set_job("Ready", 100, done=True)
    except Exception as exc:  # surfaced in the UI, not swallowed
        traceback.print_exc()
        set_job("Failed", 0, error=f"{type(exc).__name__}: {exc}")


class BuildIn(BaseModel):
    comment: str | None = None


@app.post("/api/plan")
def api_plan(body: BuildIn):
    if JOB["running"]:
        raise HTTPException(409, "already working")
    if not STATE["has_key"]:
        raise HTTPException(400, "No ANTHROPIC_API_KEY is set, so the album cannot be designed. "
                                 "Everything else in this editor works offline.")
    set_job("Starting", 1)
    threading.Thread(target=_build, args=(body.comment is None, body.comment), daemon=True).start()
    return {"ok": True}


@app.get("/api/report")
def api_report():
    store = Store(STATE["album"])
    try:
        v, data = store.get()
    except SystemExit:
        return {"ready": False}
    layout = Layout.model_validate(data)
    by_photo = {pid: g.id for g in STATE["groups"] for pid in g.photo_ids}
    rep = check(layout, load_photos(STATE["photos"]), STATE["g"], STATE["lab"], by_photo)
    return {
        "ready": True,
        "version": v.version_no,
        "pages": rep.page_count,
        "min_dpi": round(rep.min_dpi),
        "blocking": rep.blocking,
        "empty_pages": rep.empty_pages or [],
        "bare_pages": rep.bare_pages or [],
        "issues": [{"level": i.level, "code": i.code, "where": i.where, "message": i.message}
                   for i in rep.issues],
        "spreads": [
            {"index": sp.index, "template": sp.template, "note": sp.note,
             "photos": [s.photo_id for s in sp.slots]}
            for sp in layout.spreads
        ],
    }


@app.get("/proof.pdf")
def api_proof():
    f = STATE["album"] / "renders" / "inner-proof.pdf"
    if not f.exists():
        raise HTTPException(404, "no preview yet")
    return FileResponse(f, media_type="application/pdf", filename="preview.pdf")


@app.get("/", response_class=HTMLResponse)
def index():
    return (HERE / "static" / "index.html").read_text()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--album", type=Path, required=True)
    ap.add_argument("--photos", type=Path, required=True)
    ap.add_argument("--captions", type=Path)
    ap.add_argument("--lab", type=Path, default=ROOT / "phase0" / "labs" / "saal.yaml")
    ap.add_argument("--format", default="21x21")
    ap.add_argument("--title", default=None)
    ap.add_argument("--subtitle", default=None)
    ap.add_argument("--language", default="de")
    ap.add_argument("--model", default=planner.MODEL)
    ap.add_argument("--gap-hours", type=float, default=4.0)
    ap.add_argument("--radius-m", type=float, default=400.0)
    ap.add_argument("--no-geocode", action="store_true")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()

    import os

    args.album.mkdir(parents=True, exist_ok=True)
    lab = R.load_lab(args.lab)
    STATE.update(
        album=args.album, photos=args.photos,
        captions=args.captions or (args.photos / "captions.yaml"),
        lab=lab, lab_path=args.lab, fmt=args.format,
        g=R.geometry_for(lab, args.format),
        title=args.title or args.album.name, subtitle=args.subtitle,
        language=args.language, model=args.model,
        gap_hours=args.gap_hours, radius_m=args.radius_m,
        geocode=not args.no_geocode,
        has_key=bool(os.environ.get("ANTHROPIC_API_KEY")),
    )
    ph = reload_photos()
    ensure_thumbs(ph)
    print(f"\n  {len(ph)} photographs · {len(STATE['groups'])} groups · {args.format}")
    if not STATE["has_key"]:
        print("  ANTHROPIC_API_KEY is not set — editing works, designing the album does not")
    print(f"\n  Open  http://{args.host}:{args.port}\n")

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
