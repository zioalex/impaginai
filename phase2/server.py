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
import copy
import io
import json
import sys
import threading
import time
import traceback
import zipfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "phase0"))
sys.path.insert(0, str(ROOT / "phase1"))

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

import annotate
import cluster
import imaging
import geocode
import overrides as OV
import pages as PG
import photos as PH
import planner
import providers
import render as R
import templates_lib
from planner import Usage
from schema import Layout
from validate import check, load_photos
from versions import Store

app = FastAPI(title="Album editor")
STATE: dict = {}
PREFLIGHT_LOCK = threading.Lock()
JOB = {"running": False, "step": "", "progress": 0, "error": None, "done": False,
       "kind": None, "cost": None, "seconds": 0.0, "result": None}
JOB_LOCK = threading.Lock()


# --- state ---------------------------------------------------------------


def reload_photos():
    a = STATE["album"]
    ph = PH.load(STATE["photos"], STATE["captions"], STATE["g"],
                 STATE["lab"]["resolution"]["warn_below_dpi"],
                 STATE["lab"]["resolution"]["block_below_dpi"])
    # Cache only: editing must never wait on the network. The named-places
    # lookup runs in prefetch_places(), at startup and before each design.
    geocode.apply(ph, a, offline_db=STATE["geocode"], online=False,
                  language=STATE["language"])
    ov = OV.Overrides.load(a)
    # Keep excluded photographs visible in the UI — the user has to be able to
    # bring one back. Exclusion is applied when building, not when listing.
    for p in ph:
        p.override = ov.get(p.id)
        # The user's description is the caption, and the planner sees it as
        # such — so it writes the others in the same voice and leaves it alone.
        if p.override.description:
            p.caption = p.override.description
    anns = annotate.Annotations.load(a / "annotations.json")
    annotate.apply(ph, anns)
    # Grouped with everything, left-out photographs included, so a photograph
    # stays in its place in the editor when it is left out and brought back.
    # (A group's left-out members are simply absent from every layout.)
    STATE["groups"] = cluster.assign(list(ph), STATE["gap_hours"], STATE["radius_m"])
    gmap = {pid: grp for grp in STATE["groups"] for pid in grp.photo_ids}
    for p in ph:
        p.group = gmap[p.id].id if p.id in gmap else None
    STATE["all"] = ph
    STATE["ov"] = ov
    return ph


def prefetch_places(photos=None) -> None:
    """Fill the named-places cache (glaciers, lakes, peaks) from OpenStreetMap.
    Bounded: cached cells are not asked again, and a failure pauses lookups."""
    if not STATE.get("online_places"):
        return
    try:
        asked = geocode.prefetch(photos if photos is not None else STATE["all"], STATE["album"],
                                 language=STATE["language"])
        if asked:
            reload_photos()
    except Exception as exc:  # never let the place lookup break the editor
        print(f"  place lookup failed: {exc}")


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


def _preflight_roles() -> list[str]:
    """The roles worth checking: plan, and revise if it is a different model or
    endpoint. Only those that could actually be called (a key, or a local model)."""
    roles = STATE["providers"]
    out = []
    for name in ("plan", "revise"):
        p = roles[name]
        if not p.usable:
            continue
        if name == "revise" and (p.model, p.base_url) == (roles["plan"].model, roles["plan"].base_url):
            continue
        out.append(name)
    return out


def run_preflight() -> dict:
    """Send one tiny request per role and record whether the model answers.
    Never raises; the result is shown, not enforced (the failure may be brief)."""
    names = _preflight_roles()
    with PREFLIGHT_LOCK:
        STATE["preflight"] = {n: {"ok": None, "status": "checking", "message": "", "seconds": 0.0}
                              for n in names}
    for n in names:
        try:
            r = providers.preflight(STATE["providers"][n])
            res = {"ok": r.ok, "status": "ok" if r.ok else "failed",
                   "message": r.message, "seconds": round(r.seconds, 1)}
        except Exception as exc:  # preflight promises not to raise; belt and braces
            res = {"ok": False, "status": "failed", "seconds": 0.0,
                   "message": f"the model check itself failed: {exc}"}
        with PREFLIGHT_LOCK:
            STATE["preflight"] = {**STATE["preflight"], n: res}
        print(f"  model check · {n}: {'ok' if res['ok'] else 'NOT OK'} — {res['message']}")
    return STATE["preflight"]


def start_preflight() -> None:
    threading.Thread(target=run_preflight, daemon=True).start()


def set_job(step: str, progress: int, error: str | None = None, done: bool = False):
    with JOB_LOCK:
        JOB.update(step=step, progress=progress, error=error, done=done,
                   running=not (done or error))
        if JOB.get("started"):
            JOB["seconds"] = round(time.time() - JOB["started"], 1)


def start_job(kind: str) -> None:
    with JOB_LOCK:
        JOB.update(running=True, step="Starting", progress=1, error=None, done=False,
                   kind=kind, cost=None, seconds=0.0, result=None, started=time.time())


class Session:
    """What one design session costs: every model call it makes, live.

    The completers are wrapped rather than the results summed afterwards, so
    the cost shown while the album is being designed is the cost so far, and
    retries and the fallback model are counted because they were paid for."""

    def __init__(self):
        self.usage = Usage()
        self.models: set[str] = set()

    def wrap(self, complete, prov):
        if complete is None:
            return None

        def tracked(system, messages, **opts):
            reply = complete(system, messages, **opts)
            u = reply.usage if hasattr(reply, "usage") else reply[1]
            self.usage.add(u)
            self.models.add(prov.model)
            with JOB_LOCK:
                JOB["cost"] = self.usage.as_dict()
            return reply
        return tracked


def log_session(kind: str, session: Session, started: float, *, ok: bool,
                comment: str | None = None, version: int | None = None,
                error: str | None = None) -> dict:
    rec = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "kind": kind, "ok": ok, "comment": comment, "version": version,
           "seconds": round(time.time() - started, 1),
           "models": sorted(session.models), **session.usage.as_dict()}
    if error:
        rec["error"] = error[:300]
    f = STATE["album"] / "costs.jsonl"
    with f.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def read_sessions() -> list[dict]:
    f = STATE["album"] / "costs.jsonl"
    if not f.exists():
        return []
    out = []
    for line in f.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


# --- settings: book size, title, language ------------------------------


def settings_path() -> Path:
    return STATE["album"] / "settings.json"


def save_settings() -> None:
    settings_path().write_text(json.dumps(
        {k: STATE.get(k) for k in ("fmt", "title", "subtitle", "language", "front_photo")},
        ensure_ascii=False, indent=2))


def apply_format(fmt: str) -> None:
    if fmt not in STATE["lab"]["formats"]:
        raise HTTPException(400, f"unknown format {fmt!r}; have {list(STATE['lab']['formats'])}")
    STATE["fmt"] = fmt
    STATE["g"] = R.geometry_for(STATE["lab"], fmt)


# --- finishing a layout without the model ------------------------------


def finish_layout(raw: dict, usable) -> dict:
    """The model's layout -> the printed one: the user's settings, measured
    focal points, no empty pages, place stamps, the album's own title. Pure
    and repeatable, so it runs again whenever a setting changes."""
    data = copy.deepcopy(raw)
    data.setdefault("meta", {})
    data["meta"]["format"] = STATE["fmt"]
    old_title = data["meta"].get("title")
    data["meta"]["title"] = STATE["title"]
    data["meta"]["subtitle"] = STATE["subtitle"]
    cov = data.get("cover") or {}
    for k in ("title", "spine_title"):
        if cov.get(k) == old_title:
            cov[k] = STATE["title"]
    for sp in data.get("spreads", []):
        t = sp.get("text") or {}
        if old_title and t.get("title") == old_title:
            t["title"] = STATE["title"]
    for n in OV.apply_to_layout(data, STATE["ov"], known=[p.id for p in usable]):
        print(f"  {n}")
    by_id = {p.id: p for p in usable}
    placed = {sl["photo_id"] for sp in data.get("spreads", []) for sl in sp.get("slots") or []}
    chrono = sorted(usable, key=lambda p: (p.taken_at is None,
                                           p.taken_at.timestamp() if p.taken_at else 0.0,
                                           p.upload_order))
    spare = [p.id for p in chrono if p.id not in placed and not p.unusable]
    front = STATE.get("front_photo")
    if front not in by_id:
        front = None
    PG.repack(data, STATE["groups"], STATE["lab"], spare=spare,
              taken={p.id: p.taken_at.isoformat() for p in usable if p.taken_at},
              front=front, strength={p.id: p.strength for p in usable},
              together=STATE["ov"].composed(),
              alone={pid for pid, o in STATE["ov"].by_id.items() if o.size == "full-page"},
              no_bleed={p.id for p in usable if (p.can or {}).get("full-page") == "no"})
    PG.apply_focal(data, by_id)
    # The cover shows the front page's photograph and the album's own title.
    opening = (data.get("spreads") or [{}])[0]
    cover = data.get("cover") or {}
    if opening.get("slots"):
        cover["front_photo_id"] = opening["slots"][0]["photo_id"]
    cover["title"] = STATE["title"]
    cover["subtitle"] = STATE["subtitle"]
    cover.setdefault("spine_title", STATE["title"])
    data["cover"] = cover
    templates_lib.attach_aspects(data.get("spreads", []),
                                 {p.id: (p.width, p.height) for p in usable})
    return data


def render_proof(store: Store, v, data: dict) -> None:
    a = STATE["album"]
    tmp = a / f"layout-v{v.version_no:03d}.json"
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    run_render("proof", tmp, a / "renders")
    # Small page images for the editor's Book view, from the same HTML.
    html = a / "renders" / "inner-proof.html"
    if html.exists():
        R.render_jpegs(html.read_text(encoding="utf-8"), html, a / "renders" / "pages-thumb",
                       thumb_px=520)
        (a / "renders" / "pages-thumb" / "version.txt").write_text(str(v.version_no))


def run_render(command: str, layout_path: Path, out: Path, *extra: str) -> int:
    argv = ["render.py", command, "--layout", str(layout_path), "--photos", str(STATE["photos"]),
            "--lab", str(STATE["lab_path"]), "--out", str(out), "--format", STATE["fmt"], *extra]
    saved, sys.argv = sys.argv, argv
    try:
        code = R.main()
    finally:
        sys.argv = saved
    return code


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
        "front_photo": STATE.get("front_photo"),
        "has_annotations": bool(annotate.Annotations.load(STATE["album"] / "annotations.json").by_id),
        "has_key": STATE["has_key"],
        "model": STATE["providers"]["plan"].describe(mention_vision=False),
        "models": {r: p.describe(mention_vision=(r == "annotate"))
                   for r, p in STATE["providers"].items()},
        "preflight": STATE.get("preflight", {}),
        "sizes": list(OV.SIZES),
        "treatments": list(OV.TREATMENTS),
        "formats": {k: f"{v['page_w_mm'] / 10:g} × {v['page_h_mm'] / 10:g} cm"
                    for k, v in STATE["lab"]["formats"].items()},
        "costs": cost_summary(),
    }


def cost_summary() -> dict:
    sess = read_sessions()
    priced = [x for x in sess if x.get("usd") is not None]
    return {"sessions": len(sess),
            "total_usd": round(sum(x["usd"] for x in priced), 4) if priced else None,
            "last": sess[-1] if sess else None,
            "estimated": any(x.get("estimated") for x in sess)}


@app.get("/api/costs")
def api_costs():
    return {**cost_summary(), "sessions": list(reversed(read_sessions()))}


class SettingsIn(BaseModel):
    front_photo: str | None = None      # "" clears it
    format: str | None = None
    title: str | None = None
    subtitle: str | None = None
    language: str | None = None


@app.put("/api/settings")
def api_settings(body: SettingsIn):
    if JOB["running"]:
        raise HTTPException(409, "busy — try again when the current job finishes")
    changed = []
    if body.format and body.format != STATE["fmt"]:
        apply_format(body.format)
        changed.append("format")
    if body.title is not None and body.title.strip() and body.title.strip() != STATE["title"]:
        STATE["title"] = body.title.strip()
        changed.append("title")
    if body.subtitle is not None and (body.subtitle.strip() or None) != STATE["subtitle"]:
        STATE["subtitle"] = body.subtitle.strip() or None
        changed.append("subtitle")
    if body.front_photo is not None and (body.front_photo or None) != STATE.get("front_photo"):
        if body.front_photo and not any(p.id == body.front_photo for p in STATE["all"]):
            raise HTTPException(404, "no such photo")
        STATE["front_photo"] = body.front_photo or None
        if STATE["front_photo"]:
            # The front page photograph must be in the book.
            STATE["ov"].set(STATE["front_photo"], exclude=False)
            STATE["ov"].save()
        changed.append("front_photo")
    if body.language and body.language.strip() != STATE["language"]:
        STATE["language"] = body.language.strip()
        changed.append("language")
    if changed:
        save_settings()
        reload_photos()
    return {"ok": True, "changed": changed,
            # A new language means new captions, which only the model writes.
            "needs_design": "language" in changed}


@app.get("/api/preflight")
def api_preflight(run: int = 0):
    if run and STATE.get("preflight_enabled", True):
        names = _preflight_roles()
        with PREFLIGHT_LOCK:
            STATE["preflight"] = {n: {"ok": None, "status": "checking", "message": "",
                                      "seconds": 0.0} for n in names}
        start_preflight()
    return STATE.get("preflight", {})


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
            "compose": o.compose,
            "rotate": imaging.rotation_of(p.path),
            "strength": p.strength or None,
            "annotation": p.annotation or None,
            "landmark": p.landmark or None,
            "detail": p.detail or None,
            "features": p.features or [],
            "too_small": p.unusable,
            # Not enough pixels to fill a page sharply: printed smaller than
            # the layout wanted, or soft. `sharp_cm` is the largest it prints
            # at the lab's warning DPI.
            "low_res": (not p.unusable) and (p.can or {}).get("large", "ok") != "ok",
            "sharp_cm": _sharp_cm(p),
            "avoid": p.avoid,
            "can": p.can,
        })
    return out


class OverrideIn(BaseModel):
    description: str | None = None
    size: str | None = None
    treatment: str | None = None
    exclude: bool | None = None
    compose: bool | None = None      # always on a page with other photographs
    rotate: int | None = None        # degrees clockwise, for a wrong EXIF orientation


@app.patch("/api/photos/{photo_id}")
def api_patch(photo_id: str, body: OverrideIn):
    _patch_one(photo_id, body)
    STATE["ov"].save()
    reload_photos()
    return {"ok": True}


class BulkIn(OverrideIn):
    ids: list[str]


@app.patch("/api/photos")
def api_patch_many(body: BulkIn):
    """The same change to several photographs at once (leave out a whole
    group, make three small). One save, one reload."""
    errors = []
    for pid in body.ids:
        try:
            _patch_one(pid, body)
        except HTTPException as exc:
            errors.append(f"{pid}: {exc.detail}")
    STATE["ov"].save()
    reload_photos()
    return {"ok": not errors, "errors": errors}


def _patch_one(photo_id: str, body: OverrideIn) -> None:
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
        if (body.size == "full-page" and STATE["ov"].get(photo_id).compose
                and body.compose is not False):
            raise HTTPException(400, f"{photo_id} is set to always share its page with other "
                                     f"photographs — turn that off before making it full page.")
        kw["size"] = body.size
    if body.treatment is not None:
        if body.treatment not in OV.TREATMENTS:
            raise HTTPException(400, f"treatment must be one of {OV.TREATMENTS}")
        kw["treatment"] = body.treatment
    if body.exclude is not None:
        kw["exclude"] = body.exclude
    if body.compose is not None:
        kw["compose"] = body.compose
    if body.rotate is not None:
        _rotate(photo_id, body.rotate)
    STATE["ov"].set(photo_id, **kw)


def _rotate(photo_id: str, degrees: int) -> None:
    """Turn one photograph by hand (its EXIF orientation is wrong or missing).

    Every cached copy of it was made the wrong way round, so they go; the
    focal point the vision pass measured is turned with it rather than
    measured again."""
    if degrees % 90:
        raise HTTPException(400, "rotation must be 0, 90, 180 or 270 degrees")
    old = imaging.rotation_of(next(p.path for p in STATE["all"] if p.id == photo_id))
    new = imaging.set_rotation(STATE["photos"], photo_id, degrees)
    delta = (new - old) % 360
    if not delta:
        return
    for f in (STATE["album"] / "derivatives").rglob(f"{photo_id}.jpg"):
        f.unlink(missing_ok=True)
    for f in (STATE["album"] / "renders" / "derivatives").rglob(f"{photo_id}.jpg"):
        f.unlink(missing_ok=True)
    apath = STATE["album"] / "annotations.json"
    anns = annotate.Annotations.load(apath)
    a = anns.by_id.get(photo_id)
    if a is not None and a.focal:
        a.focal = imaging.turn_point(a.focal, delta)
        anns.save(apath)
    ensure_thumbs([p for p in STATE["all"] if p.id == photo_id])


@app.get("/thumb/{photo_id}.jpg")
def api_thumb(photo_id: str):
    f = thumb_dir() / f"{photo_id}.jpg"
    if not f.exists():
        raise HTTPException(404)
    return FileResponse(f, media_type="image/jpeg")


@app.get("/photo/{photo_id}.jpg")
def api_photo_large(photo_id: str):
    """A large copy for looking at one photograph closely (2400 px), made on
    first request and kept. The original may be HEIC or 50 MB; this never is."""
    from PIL import Image

    p = next((x for x in STATE["all"] if x.id == photo_id), None)
    if p is None:
        raise HTTPException(404)
    d = STATE["album"] / "derivatives" / "view2400"
    out = d / f"{photo_id}.jpg"
    if not out.exists():
        d.mkdir(parents=True, exist_ok=True)
        with Image.open(p.path) as im:
            im = R.to_srgb(im)
            im.thumbnail((2400, 2400), Image.LANCZOS)
            tmp = out.with_suffix(".tmp")
            im.save(tmp, "JPEG", quality=86, icc_profile=R.SRGB_BYTES, optimize=True)
            tmp.replace(out)
    return FileResponse(out, media_type="image/jpeg")


@app.get("/api/job")
def api_job():
    with JOB_LOCK:
        return dict(JOB)


def _build(do_plan: bool, comment: str | None):
    """The long job: annotate (once), plan or revise, apply overrides, render.

    One run is one design session, and what it costs is shown live and logged."""
    session = Session()
    started = time.time()
    kind = "design" if do_plan else "revise"
    try:
        a = STATE["album"]
        ph = reload_photos()
        usable = [p for p in ph if not STATE["ov"].get(p.id).exclude]
        if not usable:
            raise RuntimeError("every photograph is excluded")

        set_job("Preparing photographs", 5)
        ensure_thumbs(usable)
        prefetch_places(ph)
        ph = STATE["all"]
        usable = [p for p in ph if not STATE["ov"].get(p.id).exclude]

        roles = STATE["providers"]
        anns = annotate.Annotations.load(a / "annotations.json")
        todo = anns.missing(usable)
        if todo and roles["annotate"].vision and roles["annotate"].usable:
            set_job(f"Looking at {len(todo)} photographs", 15)
            # Small copies for the model; the 1200px proof derivatives stay as
            # they are, because the proof PDF needs them.
            vdir = annotate.build_vision_copies(
                todo, a / "derivatives" / f"vision{STATE['vision_px']}",
                STATE["vision_px"])
            anns, _ = annotate.run(usable, vdir, a / "annotations.json",
                                   session.wrap(providers.make_completer(roles["annotate"]),
                                                roles["annotate"]))
        turned = annotate.apply_turns(usable, anns, a / "annotations.json", STATE["photos"], a)
        if turned:
            # Their shape changed: measure again, and remake the thumbnails.
            print(f"  turned upright: {', '.join(turned)}")
            ph = reload_photos()
            usable = [p for p in ph if not STATE["ov"].get(p.id).exclude]
            ensure_thumbs(usable)
        annotate.apply(usable, anns)

        store = Store(a)
        groups = STATE["groups"]
        role = roles["plan" if do_plan else "revise"]
        complete = session.wrap(providers.make_completer(role), role)
        fb_prov = providers.fallback_provider(role)
        fallback = session.wrap(providers.make_fallback_completer(role), fb_prov) if fb_prov else None
        if do_plan:
            set_job("Designing the album", 45)
            res = planner.plan(title=STATE["title"], subtitle=STATE["subtitle"],
                               fmt=STATE["fmt"], language=STATE["language"],
                               photos=usable, g=STATE["g"], lab=STATE["lab"],
                               complete=complete, fallback=fallback,
                               on_event=lambda m: set_job(m, 45),
                               groups=groups, log_dir=a / "logs")
            parent, comment_txt, summary = None, None, "initial plan"
        else:
            set_job("Revising the album", 45)
            pv, current = store.get()
            res = planner.revise(current=current, comment=comment or "", photos=usable,
                                 g=STATE["g"], lab=STATE["lab"], language=STATE["language"],
                                 complete=complete, fallback=fallback,
                                 on_event=lambda m: set_job(m, 45),
                                 groups=groups, log_dir=a / "logs")
            parent, comment_txt, summary = pv.version_no, comment, res.layout.change_summary

        set_job("Applying your settings", 70)
        raw = res.layout.model_dump(mode="json", exclude_none=False)
        data = finish_layout(raw, usable)
        Layout.model_validate(data)
        cost = session.usage.cost_usd
        v = store.add(data, parent=parent, user_comment=comment_txt, change_summary=summary,
                      raw=raw, cost_usd=cost, kind=kind)

        set_job("Printing the preview", 85)
        render_proof(store, v, data)
        rec = log_session(kind, session, started, ok=True, comment=comment_txt,
                          version=v.version_no)
        with JOB_LOCK:
            JOB["result"] = rec
        set_job("Ready", 100, done=True)
    except Exception as exc:  # surfaced in the UI, not swallowed
        traceback.print_exc()
        msg = str(exc)
        if session.usage.attempts:
            # Paid for even though it failed — the user should see that too.
            log_session(kind, session, started, ok=False, comment=comment, error=msg)
        if isinstance(exc, planner.PlanningError):
            set_job("Failed", 0, error=msg)  # the message already says what happened
        else:
            set_job("Failed", 0, error=f"{type(exc).__name__}: {msg}")


def _rerender():
    """Re-apply the user's settings to the current design and print a new
    preview. No model call, no cost: descriptions, sizes, edges and what is
    left out show up in the book as soon as they are made."""
    try:
        a = STATE["album"]
        store = Store(a)
        pv, current = store.get()
        raw = store.raw() or current
        ph = reload_photos()
        usable = [p for p in ph if not STATE["ov"].get(p.id).exclude]
        if not usable:
            raise RuntimeError("every photograph is excluded")
        set_job("Applying your changes", 30)
        ensure_thumbs(usable)
        data = finish_layout(raw, usable)
        placed = {s["photo_id"] for sp in data["spreads"] for s in sp.get("slots") or []}
        missing = [p.id for p in usable if p.id not in placed]
        Layout.model_validate(data)
        v = store.add(data, parent=pv.version_no, change_summary="your changes applied",
                      raw=raw, kind="settings")
        set_job("Printing the preview", 60)
        render_proof(store, v, data)
        with JOB_LOCK:
            JOB["result"] = {"version": v.version_no, "not_placed": missing}
        set_job("Ready", 100, done=True)
    except Exception as exc:
        traceback.print_exc()
        set_job("Failed", 0, error=f"{type(exc).__name__}: {exc}")


EXPORTS = {
    "proof": ("Preview PDF", "inner-proof.pdf", "application/pdf"),
    "print": ("Print-ready PDF (inner pages)", "inner-print.pdf", "application/pdf"),
    "cover": ("Cover PDF", "cover.pdf", "application/pdf"),
    "jpeg": ("Page images (JPEG, zip)", "pages.zip", "application/zip"),
}


def _export(kind: str, dpi: int):
    try:
        a = STATE["album"]
        store = Store(a)
        v, data = store.get()
        out = a / "exports"
        tmp = a / f"layout-v{v.version_no:03d}.json"
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        set_job(f"Exporting: {EXPORTS[kind][0]}", 30)
        if kind == "jpeg":
            code = run_render("jpeg", tmp, out, "--dpi", str(dpi))
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
                for f in sorted((out / "pages").glob("page-*.jpg")):
                    z.write(f, f.name)
            (out / "pages.zip").write_bytes(buf.getvalue())
        else:
            code = run_render(kind, tmp, out)
        if code:
            raise RuntimeError("the export did not pass the print checks — see the "
                               "report under 'Open preview' and the server log")
        with JOB_LOCK:
            JOB["result"] = {"download": f"/export/{kind}"}
        set_job("Ready", 100, done=True)
    except Exception as exc:
        traceback.print_exc()
        set_job("Failed", 0, error=f"{type(exc).__name__}: {exc}")


class BuildIn(BaseModel):
    comment: str | None = None


@app.post("/api/plan")
def api_plan(body: BuildIn):
    if JOB["running"]:
        raise HTTPException(409, "already working")
    if not STATE["has_key"]:
        p = STATE["providers"]["plan"]
        raise HTTPException(400, f"No API key for {p.name}, so the album cannot be designed. "
                                 "Set ALBUM_API_KEY (or ANTHROPIC_API_KEY / OPENROUTER_API_KEY), "
                                 "or point --base-url at a local model. Everything else in this "
                                 "editor works without one.")
    start_job("design" if body.comment is None else "revise")
    threading.Thread(target=_build, args=(body.comment is None, body.comment), daemon=True).start()
    return {"ok": True}


@app.post("/api/rerender")
def api_rerender():
    if JOB["running"]:
        raise HTTPException(409, "already working")
    if Store(STATE["album"]).latest() is None:
        raise HTTPException(400, "nothing designed yet")
    start_job("rerender")
    threading.Thread(target=_rerender, daemon=True).start()
    return {"ok": True}


class ExportIn(BaseModel):
    kind: str
    dpi: int = 300


@app.post("/api/export")
def api_export(body: ExportIn):
    if body.kind not in EXPORTS:
        raise HTTPException(400, f"kind must be one of {list(EXPORTS)}")
    if JOB["running"]:
        raise HTTPException(409, "already working")
    if Store(STATE["album"]).latest() is None:
        raise HTTPException(400, "design the album first")
    start_job("export")
    threading.Thread(target=_export, args=(body.kind, max(72, min(body.dpi, 600))),
                     daemon=True).start()
    return {"ok": True}


@app.get("/export/{kind}")
def api_download(kind: str):
    if kind not in EXPORTS:
        raise HTTPException(404)
    label, name, mime = EXPORTS[kind]
    f = STATE["album"] / "exports" / name
    if not f.exists():
        raise HTTPException(404, "not exported yet")
    stem = "".join(c if c.isalnum() or c in "-_" else "-" for c in STATE["title"]).strip("-") or "album"
    return FileResponse(f, media_type=mime, filename=f"{stem}-{STATE['fmt']}-{name}")


def _sharp_cm(p) -> list[float]:
    dpi = float(STATE["lab"]["resolution"]["warn_below_dpi"])
    return [round(p.width / dpi * 2.54, 1), round(p.height / dpi * 2.54, 1)]


def low_res_pages(data: dict) -> dict[int, list[dict]]:
    """Page number -> photographs on it that resolution held back: printed
    below the lab's warning DPI ("soft"), or set smaller than the layout
    meant so that they stay sharp ("shrunk")."""
    from geometry import effective_dpi

    g = STATE["g"]
    warn = float(STATE["lab"]["resolution"]["warn_below_dpi"])
    by_id = {p.id: p for p in STATE["all"]}
    out: dict[int, list[dict]] = {}
    for n, sp in enumerate(data.get("spreads") or []):
        slots = sp.get("slots") or []
        if not slots:
            continue
        opts = dict(sp.get("options") or {})
        try:
            real = templates_lib.build(sp["template"], g, opts).slots
            ideal = templates_lib.build(sp["template"], g,
                                        {k: v for k, v in opts.items() if k != "pixels"}).slots
        except Exception:
            continue
        for slot, box, want in zip(slots, real, ideal):
            ph = by_id.get(slot["photo_id"])
            if ph is None:
                continue
            dpi = effective_dpi(ph.width, ph.height, box.rect)
            shrunk = box.rect.w * box.rect.h < 0.9 * want.rect.w * want.rect.h
            soft = dpi < warn - 1.0      # the compositor stops at the limit itself
            if not soft and not shrunk:
                continue
            page = 2 * n + (1 if box.rect.x + box.rect.w / 2 < g.fold_x else 2)
            out.setdefault(page, []).append({
                "id": ph.id, "dpi": round(dpi), "kind": "soft" if soft else "shrunk",
                "sharp_cm": _sharp_cm(ph)})
    return out


@app.get("/api/pages")
def api_pages():
    d = STATE["album"] / "renders" / "pages-thumb"
    files = sorted(d.glob("page-*.jpg")) if d.exists() else []
    ver = (d / "version.txt").read_text() if (d / "version.txt").exists() else None
    low = {}
    if ver and files:
        try:
            _, data = Store(STATE["album"]).get(int(ver))
            low = low_res_pages(data)
        except Exception:
            traceback.print_exc()
    return {"version": ver, "count": len(files), "low_res": low}


@app.get("/pages/{n}.jpg")
def api_page_image(n: int):
    f = STATE["album"] / "renders" / "pages-thumb" / f"page-{n:03d}.jpg"
    if not f.exists():
        raise HTTPException(404)
    return FileResponse(f, media_type="image/jpeg", headers={"Cache-Control": "no-cache"})


class RollbackIn(BaseModel):
    to: int


@app.post("/api/rollback")
def api_rollback(body: RollbackIn):
    if JOB["running"]:
        raise HTTPException(409, "already working")
    store = Store(STATE["album"])
    try:
        v = store.rollback(body.to)
    except SystemExit as exc:
        raise HTTPException(404, str(exc))
    start_job("rerender")

    def go():
        try:
            set_job("Printing the preview", 50)
            render_proof(store, v, store.get(v.version_no)[1])
            set_job("Ready", 100, done=True)
        except Exception as exc:
            traceback.print_exc()
            set_job("Failed", 0, error=f"{type(exc).__name__}: {exc}")
    threading.Thread(target=go, daemon=True).start()
    return {"ok": True, "version": v.version_no}


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
    ap.add_argument("--format", default=None,
                    help="book size from the lab config (default 21x21, or what was last "
                         "chosen in the editor)")
    ap.add_argument("--title", default=None)
    ap.add_argument("--subtitle", default=None)
    ap.add_argument("--language", default=None, help="album language (default de)")
    providers.add_arguments(ap)
    ap.add_argument("--gap-hours", type=float, default=4.0)
    ap.add_argument("--radius-m", type=float, default=400.0)
    ap.add_argument("--no-geocode", action="store_true")
    ap.add_argument("--offline-places", action="store_true",
                    help="do not look up glacier, lake and peak names on OpenStreetMap "
                         "(that lookup sends photo coordinates to overpass-api.de)")
    ap.add_argument("--vision-px", type=int, default=annotate.VISION_PX,
                    help=f"long edge sent to the vision model (default {annotate.VISION_PX})")
    ap.add_argument("--no-preflight", action="store_true",
                    help="skip the one-request model check at startup")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()

    import os

    args.album.mkdir(parents=True, exist_ok=True)
    lab = R.load_lab(args.lab)
    # What was chosen in the editor last time, unless a flag says otherwise.
    saved = {}
    sp = args.album / "settings.json"
    if sp.exists():
        try:
            saved = json.loads(sp.read_text())
        except ValueError:
            saved = {}
    fmt = args.format or saved.get("fmt") or "21x21"
    STATE.update(
        album=args.album, photos=args.photos,
        captions=args.captions or (args.photos / "captions.yaml"),
        lab=lab, lab_path=args.lab, fmt=fmt,
        g=R.geometry_for(lab, fmt),
        title=args.title or saved.get("title") or args.album.name,
        subtitle=args.subtitle if args.subtitle is not None else saved.get("subtitle"),
        language=args.language or saved.get("language") or "de",
        gap_hours=args.gap_hours, radius_m=args.radius_m,
        geocode=not args.no_geocode,
        online_places=not (args.no_geocode or args.offline_places),
        vision_px=args.vision_px,
        front_photo=saved.get("front_photo"),
    )
    roles = providers.resolve_all(args, args.models)
    STATE["providers"] = roles
    # Planning is the call that cannot be skipped, so its key decides whether
    # the album can be designed at all; annotate degrades gracefully.
    STATE["has_key"] = roles["plan"].usable
    heic = sorted(p for p in args.photos.iterdir()
                  if p.suffix.lower() in imaging.HEIF_EXTENSIONS) if args.photos.is_dir() else []
    if (problem := imaging.check(heic)):
        raise SystemExit(problem)
    ph = reload_photos()
    ensure_thumbs(ph)
    threading.Thread(target=prefetch_places, daemon=True).start()
    print(f"\n  {len(ph)} photographs · {len(STATE['groups'])} groups · {fmt}")
    print(providers.describe_roles(roles))
    if not STATE["has_key"]:
        print("  no API key — editing works, designing the album does not")
    STATE["preflight_enabled"] = not args.no_preflight
    STATE["preflight"] = {}
    if STATE["preflight_enabled"]:
        STATE["preflight"] = {n: {"ok": None, "status": "checking", "message": "", "seconds": 0.0}
                              for n in _preflight_roles()}
        start_preflight()
    print(f"\n  Open  http://{args.host}:{args.port}\n")

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
