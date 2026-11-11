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
import os
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

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

import annotate
import cluster
import config as CFG
import imaging
import geocode
import keystore
import overrides as OV
import pages as PG
import photos as PH
import planner
import prompt as P
import providers
import render as R
import templates_lib
from planner import Usage
from schema import Layout
from validate import check, load_photos
from versions import Store

app = FastAPI(title="Album editor")

# Endpoints that work before any album is open: the welcome screen uses them.
NO_ALBUM_OK = {"/api/album", "/api/albums", "/api/fs", "/api/job", "/api/library"}


@app.middleware("http")
async def need_album(request, call_next):
    path = request.url.path
    if (STATE.get("album") is None and path.startswith("/api/")
            and path not in NO_ALBUM_OK):
        return JSONResponse({"detail": "no album is open — choose or create one first"},
                            status_code=409)
    return await call_next(request)
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
                   "message": keystore.redact(r.message), "seconds": round(r.seconds, 1)}
        except Exception as exc:  # preflight promises not to raise; belt and braces
            res = {"ok": False, "status": "failed", "seconds": 0.0,
                   "message": keystore.redact(f"the model check itself failed: {exc}")}
        with PREFLIGHT_LOCK:
            STATE["preflight"] = {**STATE["preflight"], n: res}
        print(f"  model check · {n}: {'ok' if res['ok'] else 'NOT OK'} — {res['message']}")
    return STATE["preflight"]


def start_preflight() -> None:
    threading.Thread(target=run_preflight, daemon=True).start()


def set_job(step: str, progress: int, error: str | None = None, done: bool = False):
    error = keystore.redact(error)
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
        rec["error"] = keystore.redact(error)[:300]
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
    data = {k: STATE.get(k) for k in ("fmt", "title", "subtitle", "language", "front_photo")}
    # Where the photographs are, so the album can be opened again by itself.
    data["photos_dir"] = str(STATE["photos"])
    if STATE.get("captions") and Path(STATE["captions"]) != Path(STATE["photos"]) / "captions.yaml":
        data["captions"] = str(STATE["captions"])
    # Options pinned by a command-line flag are not saved: the flag holds only
    # for that run, and the album keeps what was chosen in the editor.
    saved = dict(STATE.get("saved_options") or {})
    for k, v in (STATE.get("opts") or {}).items():
        if k not in STATE.get("pinned", {}):
            saved[k] = v
    data["options"] = saved
    STATE["saved_options"] = saved
    if STATE.get("model_cfg") is not None and not STATE.get("models_locked_by"):
        data["models"] = STATE["model_cfg"]
        data["preset"] = STATE.get("model_preset")
    elif "saved_models" in STATE:
        data["models"], data["preset"] = STATE["saved_models"]
    settings_path().write_text(json.dumps(data, ensure_ascii=False, indent=2))


def apply_options() -> None:
    """Make STATE follow the current options (lab, grouping, places, vision)."""
    o = STATE["opts"]
    lab_path = STATE["lab_paths"].get(o["lab"])
    if lab_path is not None and lab_path != STATE.get("lab_path"):
        lab = R.load_lab(lab_path)
        STATE["lab"], STATE["lab_path"] = lab, lab_path
        if STATE.get("fmt") not in lab["formats"]:
            STATE["fmt"] = next((k for k, v in lab["formats"].items() if v.get("default")),
                                next(iter(lab["formats"])))
        STATE["g"] = R.geometry_for(lab, STATE["fmt"])
    STATE["gap_hours"] = float(o["gap_hours"])
    STATE["radius_m"] = float(o["radius_m"])
    STATE["geocode"] = bool(o["geocode"])
    STATE["online_places"] = bool(o["geocode"] and o["online_places"])
    STATE["vision_px"] = int(o["vision_px"])


def prefs() -> dict:
    """The options the planner is told about."""
    o = STATE["opts"]
    return {k: o[k] for k in ("density", "selection", "captions")}


def target_spreads() -> int | None:
    n = int(STATE["opts"].get("length") or 0)
    if not n:
        return None
    pages = STATE["lab"]["pages"]
    n = max(int(pages["min"]), min(int(pages["max"]), n))
    return n // 2


def apply_format(fmt: str) -> None:
    if fmt not in STATE["lab"]["formats"]:
        raise HTTPException(400, f"unknown format {fmt!r}; have {list(STATE['lab']['formats'])}")
    STATE["fmt"] = fmt
    STATE["g"] = R.geometry_for(STATE["lab"], fmt)


# --- finishing a layout without the model ------------------------------


def unseen_photos(raw: dict | None, usable) -> set[str]:
    """Photographs the model never saw: added after the design was made.

    A design records the photographs it was shown (`_seen_photos`). A design
    made before that was recorded falls back to the files' age: a photograph
    whose file is newer than the last design or revision is new."""
    ids = {p.id for p in usable}
    seen = (raw or {}).get("_seen_photos")
    if seen is not None:
        return ids - set(seen)
    try:
        designs = [v for v in Store(STATE["album"]).versions()
                   if v.kind in ("design", "revise", None)]
    except Exception:
        return set()
    if not designs:
        return set()
    t = datetime.fromisoformat(designs[-1].created_at).timestamp()
    out = set()
    for p in usable:
        try:
            if p.path.stat().st_mtime > t + 1:
                out.add(p.id)
        except OSError:
            pass
    return out


def touched_photos(by_id: dict) -> set[str]:
    """Photographs the user has made a choice about (a size, "in a
    composition", their own words). The design may never have placed them,
    but the editor counts them as used, so the book must hold them."""
    return {pid for pid, o in STATE["ov"].by_id.items()
            if pid in by_id and not o.exclude
            and (o.size != "auto" or o.compose or o.description)}


def placed_ids(data: dict) -> list[str]:
    """Every photograph on a page of the layout, once, in book order."""
    seen: dict[str, None] = {}
    for sp in data.get("spreads") or []:
        for s in sp.get("slots") or []:
            seen.setdefault(s["photo_id"], None)
    return list(seen)


def finish_layout(raw: dict, usable) -> dict:
    """The model's layout -> the printed one: the user's settings, measured
    focal points, no empty pages, place stamps, the album's own title. Pure
    and repeatable, so it runs again whenever a setting changes."""
    data = copy.deepcopy(raw)
    data.pop("_seen_photos", None)
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
    o = STATE.get("opts") or {}
    if o.get("captions") in ("place", "none"):
        # No captions but the user's own (from the caption box or the
        # captions file): the model's are taken out here.
        mine = {p.id: p.caption for p in usable if p.caption}
        for sp in data.get("spreads", []):
            for sl in sp.get("slots") or []:
                sl["caption"] = mine.get(sl["photo_id"])
    for n in OV.apply_to_layout(data, STATE["ov"], known=[p.id for p in usable],
                                groups=STATE["groups"],
                                taken={p.id: p.taken_at.isoformat() for p in usable if p.taken_at}):
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
              alone={pid for pid, o in STATE["ov"].by_id.items()
                     if o.size in ("full-page", "spread")},
              no_bleed={p.id for p in usable if (p.can or {}).get("full-page") == "no"},
              density=o.get("density", "balanced"), use_all=o.get("selection") == "all",
              place_names=o.get("captions") != "none",
              split_short=o.get("short_book", "split") == "split",
              # Photographs added after the design: the model never had the
              # chance to leave them out, so they go in.
              include=unseen_photos(raw, usable) | touched_photos(by_id),
              features={p.id: {"scene": p.scene, "subject": p.subject, "who": p.who}
                        for p in usable})
    PG.apply_focal(data, by_id)
    # Never cut the people or the main subject: a page set to the edges whose
    # crop would cut them holds the photograph inside its margins instead, and
    # every slot carries the box the renderer must keep in view.
    fb = STATE["g"].full_bleed("left")
    keeps = PG.derive_keeps(usable)
    for n in PG.protect_subjects(data, keeps, {p.id: p.width / p.height for p in usable},
                                 fb.w / fb.h):
        print(f"  {n}")
    PG.attach_keeps(data, keeps)
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
    if STATE.get("album") is None:
        return {"open": False, **api_albums()}
    return {"open": True, "album_dir": str(STATE["album"]), "photos_dir": str(STATE["photos"]),
            **_album_info()}


def _album_info():
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
        "placed": sorted(latest_placed() or []),
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


# --- settings tab -----------------------------------------------------


def _lab_choices() -> dict[str, str]:
    out = {}
    for name, path in STATE["lab_paths"].items():
        try:
            lab = R.load_lab(path)
            pg = lab["pages"]
            out[name] = (f"{lab.get('lab', name)} {lab.get('line', '')}".strip()
                         + f" · {pg['min']}–{pg['max']} pages")
        except Exception:
            out[name] = name
    return out


@app.get("/api/config")
def api_config():
    lab = STATE["lab"]
    return {
        "options": CFG.describe(STATE["opts"], STATE["pinned"], _lab_choices()),
        "groups": CFG.GROUPS,
        "lab": {"pages": lab["pages"], "resolution": lab["resolution"],
                "binding": lab.get("binding"), "paper": (lab.get("paper") or {}).get("name")},
        "photo_count": len(STATE.get("all") or []),
        "group_count": len(STATE.get("groups") or []),
    }


@app.put("/api/config")
def api_config_put(body: dict):
    if JOB["running"]:
        raise HTTPException(409, "busy — try again when the current job finishes")
    new = dict(STATE["opts"])
    changed = []
    for k, v in body.items():
        o = CFG.BY_KEY.get(k)
        if o is None:
            raise HTTPException(400, f"unknown setting {k!r}")
        if k in STATE["pinned"]:
            raise HTTPException(409, f"{o.label} is set by {o.flag} when the editor was started")
        try:
            v = o.check(v) if k != "lab" else str(v)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        if k == "lab" and v not in STATE["lab_paths"]:
            raise HTTPException(400, f"no lab {v!r}; have {', '.join(STATE['lab_paths'])}")
        if new.get(k) != v:
            new[k] = v
            changed.append(k)
    if changed:
        STATE["opts"] = new
        apply_options()
        save_settings()
        reload_photos()
    return {"ok": True, "changed": changed, "needs": CFG.needs(changed),
            "group_count": len(STATE["groups"]), "format": STATE["fmt"]}


@app.get("/api/groups/preview")
def api_groups_preview(gap_hours: float, radius_m: float):
    """How many moments the photographs fall into with these values, without
    changing anything — for the sliders."""
    return {"count": len(cluster.assign(list(STATE["all"]), gap_hours, radius_m))}


@app.post("/api/relook")
def api_relook():
    """Have the vision pass look at every photograph again on the next design."""
    if JOB["running"]:
        raise HTTPException(409, "busy — try again when the current job finishes")
    path = STATE["album"] / "annotations.json"
    anns = annotate.Annotations.load(path)
    for a in anns.by_id.values():
        a.v = 0
    anns.save(path)
    n = len([p for p in STATE["all"] if not STATE["ov"].get(p.id).exclude])
    return {"ok": True, "photos": n}


# --- albums: open, create, switch -----------------------------------------


def load_album_settings(album: Path) -> dict:
    sp = album / "settings.json"
    if sp.exists():
        try:
            return json.loads(sp.read_text())
        except ValueError:
            return {}
    return {}


def library_dir() -> Path:
    """Where new albums are made: $ALBUM_LIBRARY, else the user's choice,
    else ~/PhotoAlbums."""
    d = os.environ.get("ALBUM_LIBRARY") or user_config().get("library")
    return Path(d).expanduser() if d else Path.home() / "PhotoAlbums"


def remember_recent() -> None:
    uc = user_config()
    rec = [r for r in uc.get("recent", []) if r.get("album") != str(STATE["album"])]
    rec.insert(0, {"album": str(STATE["album"]), "photos": str(STATE["photos"]),
                   "title": STATE["title"], "photo_count": len(STATE.get("all") or []),
                   "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    uc["recent"] = rec[:12]
    save_user_config(uc)


def open_album(album: Path, photos: Path | None = None, captions: Path | None = None,
               *, first: bool = False, title: str | None = None) -> None:
    """Make `album` the one the editor works on: its settings, options and
    models, its photographs, groups and thumbnails. Raises ValueError with a
    sentence for the user when it cannot.

    `first` is the album named on the command line: only it takes --title,
    --format, --language and --captions; albums opened later use what was
    chosen in the editor."""
    args = STATE["args"]
    album = Path(album).expanduser().resolve()
    saved = load_album_settings(album)
    if photos is None and saved.get("photos_dir"):
        photos = Path(saved["photos_dir"])
    if photos is None:
        raise ValueError(f"{album.name} has no photo folder yet — choose one.")
    photos = Path(photos).expanduser().resolve()
    if not photos.is_dir():
        raise ValueError(f"The photo folder {photos} does not exist (any more).")
    heic = sorted(p for p in photos.iterdir() if p.suffix.lower() in imaging.HEIF_EXTENSIONS)
    if (problem := imaging.check(heic)):
        raise ValueError(problem)
    album.mkdir(parents=True, exist_ok=True)

    pinned = STATE["pinned"]
    opts = CFG.resolve(saved.get("options") or {}, pinned)
    if opts["lab"] not in STATE["lab_paths"]:
        opts["lab"] = "saal"
    lab_path = STATE["lab_paths"][opts["lab"]]
    lab = R.load_lab(lab_path)
    fmt = (args.format if first else None) or saved.get("fmt") or "21x21"
    if fmt not in lab["formats"]:
        fmt = next(iter(lab["formats"]))
    cap = (captions or (args.captions if first else None)
           or (Path(saved["captions"]) if saved.get("captions") else None)
           or photos / "captions.yaml")
    STATE.update(
        album=album, photos=photos, captions=cap,
        opts=opts, saved_options=dict(saved.get("options") or {}),
        lab=lab, lab_path=lab_path, fmt=fmt, g=R.geometry_for(lab, fmt),
        title=((args.title if first else None) or title or saved.get("title") or album.name),
        subtitle=(args.subtitle if first and args.subtitle is not None else saved.get("subtitle")),
        language=((args.language if first else None) or saved.get("language") or "de"),
        front_photo=saved.get("front_photo"),
    )
    apply_options()
    # The models: what this album chose in the editor, else what was chosen
    # last for another album, else the environment's defaults. Flags win.
    if saved.get("models") is not None:
        STATE["model_cfg"], STATE["model_preset"] = saved["models"], saved.get("preset")
    else:
        uc = user_config()
        STATE["model_cfg"], STATE["model_preset"] = uc.get("models"), uc.get("preset")
    STATE.pop("saved_models", None)
    if STATE["models_locked_by"]:
        STATE["saved_models"] = (saved.get("models"), saved.get("preset"))
    apply_models(check=STATE.get("preflight_enabled", True))
    ph = reload_photos()
    ensure_thumbs(ph)
    threading.Thread(target=prefetch_places, daemon=True).start()
    save_settings()
    remember_recent()


def _browse_ok(path: Path) -> Path:
    """A folder inside the browse root, resolved (symlinks cannot lead out)."""
    if not STATE.get("files_editable", True):
        raise HTTPException(403, "Folders cannot be chosen over the network: the editor was "
                                 "started on a non-local address without --allow-remote-settings.")
    root = STATE["browse_root"]
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = root / p
    p = p.resolve()
    if p != root and root not in p.parents:
        raise HTTPException(403, f"Only folders inside {root} can be chosen.")
    return p


def _count_photos(d: Path, cap: int = 5000) -> int:
    n = 0
    try:
        for f in d.iterdir():
            if f.suffix.lower() in imaging.EXTENSIONS and f.is_file():
                n += 1
                if n >= cap:
                    break
    except OSError:
        return 0
    return n


@app.get("/api/fs")
def api_fs(path: str = ""):
    """Folders for the folder browser, with how many photographs each holds.
    Directories only, never file contents, never outside the browse root."""
    p = _browse_ok(Path(path) if path else STATE["browse_root"])
    if not p.is_dir():
        raise HTTPException(404, "no such folder")
    dirs = []
    try:
        entries = sorted(p.iterdir(), key=lambda x: x.name.lower())
    except OSError as exc:
        raise HTTPException(403, f"cannot read this folder: {exc.strerror}")
    for d in entries:
        if d.name.startswith(".") or not d.is_dir():
            continue
        try:
            d.resolve().relative_to(STATE["browse_root"])
        except ValueError:
            continue                      # a link that leads out of the root
        dirs.append({"name": d.name, "path": str(d), "photos": _count_photos(d)})
    root = STATE["browse_root"]
    return {"path": str(p), "root": str(root),
            "parent": str(p.parent) if p != root else None,
            "photos": _count_photos(p), "dirs": dirs[:500]}


@app.get("/api/albums")
def api_albums():
    rec = []
    for r in user_config().get("recent", []):
        a = Path(r.get("album", ""))
        rec.append({**r, "exists": a.is_dir() and Path(r.get("photos", "")).is_dir(),
                    "current": STATE.get("album") is not None and a == STATE["album"]})
    return {"recent": rec, "library": str(library_dir()),
            "current": str(STATE["album"]) if STATE.get("album") else None,
            "files_editable": STATE.get("files_editable", True),
            "browse_root": str(STATE["browse_root"])}


class AlbumIn(BaseModel):
    album: str | None = None          # open this album folder
    name: str | None = None           # or make a new one with this name
    photos: str | None = None         # its photographs; None = upload into the album


def _slug(name: str) -> str:
    import re as _re
    s_ = _re.sub(r"[^\w\- ]+", "", name, flags=_re.UNICODE).strip().replace(" ", "-")
    return s_[:60] or "album"


@app.post("/api/albums")
def api_albums_open(body: AlbumIn):
    """Open an album, or make a new one. A new album's photographs are an
    existing folder, or a `photos` folder inside the album to drop them into."""
    if JOB["running"]:
        raise HTTPException(409, "busy — try again when the current job finishes")
    if not STATE.get("files_editable", True):
        raise HTTPException(403, "Albums cannot be opened over the network: the editor was "
                                 "started on a non-local address without --allow-remote-settings.")
    photos = _browse_ok(Path(body.photos)) if body.photos else None
    title = None
    if body.album:
        album = Path(body.album).expanduser()
        if not (album / "settings.json").exists() and photos is None:
            raise HTTPException(404, "That folder is not an album made here.")
    elif body.name and body.name.strip():
        title = body.name.strip()
        lib = library_dir()
        album = lib / _slug(title)
        n = 2
        while album.exists() and any(album.iterdir()):
            album = lib / f"{_slug(title)}-{n}"
            n += 1
        if photos is None:
            photos = album / "photos"
            photos.mkdir(parents=True, exist_ok=True)
    else:
        raise HTTPException(400, "give an album to open, or a name for a new one")
    try:
        open_album(album, photos, title=title)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "album": str(STATE["album"]), "photos": str(STATE["photos"]),
            "photo_count": len(STATE["all"])}


class PhotosDirIn(BaseModel):
    photos: str


@app.put("/api/albums/photos")
def api_album_photos(body: PhotosDirIn):
    """Point the current album at another photo folder."""
    if STATE.get("album") is None:
        raise HTTPException(409, "no album is open — choose or create one first")
    if JOB["running"]:
        raise HTTPException(409, "busy — try again when the current job finishes")
    photos = _browse_ok(Path(body.photos))
    try:
        open_album(STATE["album"], photos)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "photos": str(STATE["photos"]), "photo_count": len(STATE["all"])}


MAX_UPLOAD = 250 * 1024 * 1024


@app.put("/api/upload")
async def api_upload(request: Request, name: str):
    """One photograph dropped onto the editor, as the raw request body. It is
    copied into the album's photo folder; an existing file is never replaced."""
    if not STATE.get("files_editable", True):
        raise HTTPException(403, "Photos cannot be uploaded over the network without "
                                 "--allow-remote-settings.")
    import re as _re

    # The file name becomes the photo's id: keep it to letters, digits, "-",
    # "_" and the extension, so it is safe in a URL and in what the model sees.
    base = Path(name).name
    stem_, suf_ = Path(base).stem, Path(base).suffix
    base = (_re.sub(r"[^\w\-]+", "_", stem_, flags=_re.UNICODE).strip("_") or "photo") + suf_.lower()
    if not stem_ or stem_.startswith(".") or Path(base).suffix.lower() not in imaging.EXTENSIONS:
        raise HTTPException(400, f"{name}: not a photograph this editor can use "
                                 f"({', '.join(sorted(imaging.EXTENSIONS))})")
    data = await request.body()
    if not data:
        raise HTTPException(400, f"{base} is empty")
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, f"{base} is larger than {MAX_UPLOAD // 2**20} MB")
    dest = STATE["photos"] / base
    stem, suf, n = dest.stem, dest.suffix, 2
    while dest.exists():
        dest = dest.with_name(f"{stem}-{n}{suf}")
        n += 1
    tmp = dest.with_name(f".{dest.name}.part")
    tmp.write_bytes(data)
    try:
        from PIL import Image

        with Image.open(tmp) as im:
            im.verify()
    except Exception:
        tmp.unlink(missing_ok=True)
        raise HTTPException(400, f"{base} could not be read as an image")
    tmp.replace(dest)
    return {"ok": True, "name": dest.name, "id": dest.stem}


@app.post("/api/photos/reload")
def api_photos_reload():
    """After uploads: read the photo folder again and make thumbnails."""
    if JOB["running"]:
        raise HTTPException(409, "busy — try again when the current job finishes")
    ph = reload_photos()
    ensure_thumbs(ph)
    remember_recent()
    return {"ok": True, "photo_count": len(ph)}


# --- AI models and keys --------------------------------------------------

PRESETS_DIR = ROOT / "presets"
MODEL_FIELDS = ("base_url", "model", "reasoning", "max_tokens", "fallback_model",
                "fallback_base_url", "json_mode", "vision")
# "High" is offered for the planner only, to start with: it is the one call
# where thinking longer pays, and the slowest and costliest to make longer.
EFFORT = {"plan": ["default", "low", "off", "high"],
          "revise": ["default", "low", "off"], "annotate": ["default", "low", "off"]}


def presets() -> list[dict]:
    out = []
    for f in sorted(PRESETS_DIR.glob("*.yaml")):
        lines = f.read_text(encoding="utf-8").splitlines()
        head = lines[0].lstrip("# ").strip() if lines else f.stem
        title, _, blurb = head.partition(" — ")
        cost = next((ln.lstrip("# ").strip() for ln in lines[1:12]
                     if "$" in ln and ("about" in ln or "~" in ln or "Cost" in ln)), "")
        out.append({"name": f.stem, "title": title.strip().capitalize(), "blurb": blurb.strip(),
                    "cost": cost, "models": providers.load_model_config(f)})
    return out


def _alias(url: str | None) -> str:
    if not url:
        return ""
    for k, v in providers.ALIASES.items():
        if v == url:
            return k
    return url


def user_config() -> dict:
    try:
        return json.loads((keystore.config_dir() / "config.json").read_text())
    except (OSError, ValueError):
        return {}


def save_user_config(data: dict) -> None:
    d = keystore.config_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text(json.dumps(data, ensure_ascii=False, indent=2))


def apply_models(check: bool = True) -> None:
    """Resolve the three roles from the flags, environment and the chosen
    models, and check them again."""
    args = STATE["args"]
    cfg = (providers.load_model_config(args.models) if getattr(args, "models", None)
           else STATE.get("model_cfg") or {})
    roles = {r: providers.resolve_role(r, args, cfg) for r in providers.ROLES}
    STATE["providers"] = roles
    STATE["has_key"] = roles["plan"].usable
    if check and STATE.get("preflight_enabled", True):
        STATE["preflight"] = {n: {"ok": None, "status": "checking", "message": "",
                                  "seconds": 0.0} for n in _preflight_roles()}
        start_preflight()


def models_locked_by(args) -> str | None:
    """Why the model choice cannot be changed in the editor, if it cannot."""
    if getattr(args, "models", None):
        return f"set by --models {args.models} when the editor was started"
    flags = [f for f in ("provider", "model", "base_url", "reasoning", "max_tokens")
             if getattr(args, f, None)]
    flags += [f"{r}_{f}" for r in providers.ROLES
              for f in ("model", "base_url", "max_tokens", "reasoning", "fallback_model")
              if getattr(args, f"{r}_{f}", None)]
    if flags:
        return "set by --" + ", --".join(f.replace("_", "-") for f in flags) + \
            " when the editor was started"
    env = [v for v in ("ALBUM_PROVIDER", "ALBUM_MODEL", "ALBUM_BASE_URL", "ALBUM_REASONING")
           + tuple(f"ALBUM_{r.upper()}_MODEL" for r in providers.ROLES) if os.environ.get(v)]
    if env:
        return "set by the environment variable " + ", ".join(env)
    return None


def _key_slots() -> list[str]:
    """The stored-key slots the current models use (hosted endpoints only)."""
    seen = []
    for p in STATE["providers"].values():
        cands = [p]
        fb = providers.fallback_provider(p) if p.fallback_model else None
        if fb:
            cands.append(fb)
        for q in cands:
            if q.is_local:
                continue
            slot = keystore.slot_for(q.name, q.base_url)
            if slot not in seen:
                seen.append(slot)
    return seen


def estimate() -> dict:
    """About what the next design costs, before it is started."""
    roles = STATE["providers"]
    usable = [p for p in STATE["all"] if not STATE["ov"].get(p.id).exclude]
    anns = annotate.Annotations.load(STATE["album"] / "annotations.json")
    todo = anns.missing(usable)
    px = int(STATE.get("vision_px") or annotate.VISION_PX)
    a_rate, a_known = providers.rate_for(roles["annotate"])
    img_tokens = (px * px * 3 // 4) // 750 + 120          # a 4:3 copy, plus its line
    a_usd = len(todo) * (img_tokens * a_rate[0] + 180 * a_rate[1]) / 1e6 \
        if roles["annotate"].vision else 0.0
    p_rate, p_known = providers.rate_for(roles["plan"])
    try:
        text = (P.system_prompt(STATE["g"], STATE["lab"], STATE["language"])
                + P.plan_user_content(STATE["title"], STATE["subtitle"], STATE["fmt"],
                                      STATE["language"], usable, target_spreads(),
                                      STATE["groups"], prefs()))
        tin = len(text) // 3
    except Exception:
        tin = 6000 + 120 * len(usable)
    tout = 4000 + 220 * len(usable)
    high = roles["plan"].reasoning == "high"
    think = 12000 if high else 3000
    p_usd = (tin * p_rate[0] + (tout + think) * p_rate[1]) / 1e6
    high_extra = (9000 * p_rate[1]) / 1e6
    return {"annotate_photos": len(todo), "annotate_usd": round(a_usd, 4),
            "plan_usd": round(p_usd, 4), "total_usd": round(a_usd + p_usd, 4),
            "high": high, "high_extra_usd": round(high_extra, 4),
            "guessed": not (a_known and p_known)}


@app.get("/api/models")
def api_models():
    roles = STATE["providers"]
    cfg = STATE.get("model_cfg") or {}
    out = {}
    for r, p in roles.items():
        out[r] = {"base_url": _alias(p.base_url) or ("anthropic" if p.name == "anthropic" else "openai"),
                  "model": p.model, "reasoning": p.reasoning or "default",
                  "max_tokens": p.max_tokens, "fallback_model": p.fallback_model or "",
                  "json_mode": p.json_mode, "vision": p.vision, "usable": p.usable,
                  "local": p.is_local, "describe": p.describe(mention_vision=(r == "annotate")),
                  "needs": providers.ROLE_NEEDS[r], "effort": EFFORT[r]}
    return {
        "locked_by": STATE.get("models_locked_by"),
        "preset": STATE.get("model_preset"),
        "roles": out,
        "presets": [{k: v for k, v in pr.items() if k != "models"} for pr in presets()],
        "endpoints": ["anthropic"] + sorted(providers.ALIASES),
        "keys": [keystore.status(s) for s in _key_slots()],
        "all_slots": {k: v[0] for k, v in keystore.SLOTS.items()},
        "keys_editable": STATE.get("keys_editable", True),
        "estimate": estimate(),
        "custom": bool(cfg) and not STATE.get("model_preset"),
    }


class ModelsIn(BaseModel):
    preset: str | None = None
    roles: dict[str, dict] | None = None


@app.put("/api/models")
def api_models_put(body: ModelsIn):
    if JOB["running"]:
        raise HTTPException(409, "busy — try again when the current job finishes")
    if STATE.get("models_locked_by"):
        raise HTTPException(409, f"The models are {STATE['models_locked_by']}.")
    cfg = copy.deepcopy(STATE.get("model_cfg") or {})
    preset = STATE.get("model_preset")
    if body.preset:
        hit = next((pr for pr in presets() if pr["name"] == body.preset), None)
        if hit is None:
            raise HTTPException(404, f"no preset {body.preset!r}")
        cfg, preset = copy.deepcopy(hit["models"]), body.preset
    for role, fields in (body.roles or {}).items():
        if role not in providers.ROLES:
            raise HTTPException(400, f"unknown role {role!r}")
        block = dict(cfg.get(role) or {})
        for k, v in fields.items():
            if k not in MODEL_FIELDS:
                raise HTTPException(400, f"{k!r} cannot be set here")
            if k == "reasoning" and v not in EFFORT[role]:
                raise HTTPException(400, f"effort for {role} must be one of {', '.join(EFFORT[role])}")
            if k == "api_key":
                raise HTTPException(400, "keys are set with PUT /api/keys")
            if v in ("", None):
                block.pop(k, None)
            else:
                block[k] = v
        cfg[role] = block
        preset = None
    # Validate by resolving: a typo in a level or a budget is refused here,
    # not discovered at the next design.
    try:
        for r in providers.ROLES:
            providers.resolve_role(r, STATE["args"], cfg)
    except SystemExit as exc:
        raise HTTPException(400, str(exc))
    STATE["model_cfg"], STATE["model_preset"] = cfg, preset
    save_settings()
    uc = user_config()
    uc.update(models=cfg, preset=preset)      # the default for the next new album
    save_user_config(uc)
    apply_models()
    return {"ok": True}


class KeyIn(BaseModel):
    slot: str
    key: str


def _keys_allowed() -> None:
    if not STATE.get("keys_editable", True):
        raise HTTPException(403, "API keys cannot be set over the network: the editor was "
                                 "started on a non-local address without "
                                 "--allow-remote-settings.")


@app.put("/api/keys")
def api_keys_put(body: KeyIn):
    _keys_allowed()
    try:
        keystore.set_key(body.slot, body.key)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    apply_models()
    # Never the key: where it comes from, and its masked form.
    return keystore.status(body.slot)


@app.delete("/api/keys/{slot}")
def api_keys_delete(slot: str):
    _keys_allowed()
    if slot not in keystore.SLOTS:
        raise HTTPException(404, "no such key")
    keystore.delete_key(slot)
    apply_models()
    return keystore.status(slot)


@app.get("/api/models/list")
def api_models_list(role: str = "plan", base_url: str | None = None):
    """What the role's endpoint offers, for the model field's suggestions.
    `base_url` asks another endpoint (the one just picked in the editor)."""
    if role not in providers.ROLES:
        raise HTTPException(400, f"unknown role {role!r}")
    p = STATE["providers"][role]
    if base_url and providers.ALIASES.get(base_url, base_url) != p.base_url:
        if base_url == "anthropic":
            p = providers.resolve(provider="anthropic", model=p.model)
        else:
            p = providers.resolve(provider="openai", base_url=base_url, model=p.model)
    try:
        rows = providers.list_models(p, need_vision=(role == "annotate"))
    except RuntimeError as exc:
        return {"models": [], "error": keystore.redact(str(exc))}
    return {"models": rows[:400], "error": None, "count": len(rows)}


@app.get("/api/models/export")
def api_models_export():
    """The model choice as a models.yaml for the command line (no keys)."""
    import yaml

    cfg = copy.deepcopy(STATE.get("model_cfg") or {})
    if not cfg:
        cfg = {}
        for r, p in STATE["providers"].items():
            block = {"model": p.model}
            if p.base_url:
                block["base_url"] = _alias(p.base_url)
            if p.reasoning:
                block["reasoning"] = p.reasoning
            cfg[r] = block
    for block in cfg.values():
        if isinstance(block, dict):
            block.pop("api_key", None)
    head = ("# Exported from the album editor. Use it with:\n"
            "#   python phase2/server.py --models models.yaml   (or album.py ... --models)\n"
            "# Keys are not included: set them in the environment or in the editor.\n")
    body = head + yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True)
    return Response(body, media_type="application/x-yaml",
                    headers={"Content-Disposition": 'attachment; filename="models.yaml"'})


class LibraryIn(BaseModel):
    path: str


@app.put("/api/library")
def api_library(body: LibraryIn):
    """Where new albums are made."""
    p = _browse_ok(Path(body.path))
    if not p.is_dir():
        raise HTTPException(404, "no such folder")
    uc = user_config()
    uc["library"] = str(p)
    save_user_config(uc)
    return {"ok": True, "library": str(library_dir())}


@app.get("/api/estimate")
def api_estimate():
    return estimate()


@app.get("/api/preflight")
def api_preflight(run: int = 0):
    if run and STATE.get("preflight_enabled", True):
        names = _preflight_roles()
        with PREFLIGHT_LOCK:
            STATE["preflight"] = {n: {"ok": None, "status": "checking", "message": "",
                                      "seconds": 0.0} for n in names}
        start_preflight()
    return STATE.get("preflight", {})


def latest_placed() -> set[str] | None:
    """The photographs on the pages of the latest version, None without one."""
    try:
        store = Store(STATE["album"])
        if store.latest() is None:
            return None
        return set(placed_ids(store.get()[1]))
    except Exception:
        return None


@app.get("/api/photos")
def api_photos():
    out = []
    placed = latest_placed()
    for p in STATE["all"]:
        o = STATE["ov"].get(p.id)
        out.append({
            "id": p.id,
            # In the design as it stands (the latest version), not merely "not
            # left out". None before the album has been designed.
            "placed": (p.id in placed) if placed is not None else None,
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
        if body.size == "spread" and body.compose:
            raise HTTPException(400, f"{photo_id} cannot span two pages and share a page with others.")
        if (body.size in ("full-page", "spread") and STATE["ov"].get(photo_id).compose
                and body.compose is not False):
            raise HTTPException(400, f"{photo_id} is set to always share its page with other "
                                     f"photographs — turn that off before making it "
                                     f"{'full page' if body.size == 'full-page' else 'span two pages'}.")
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
        a.keep = imaging.turn_box(a.keep, delta)
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
        turned = (annotate.apply_turns(usable, anns, a / "annotations.json", STATE["photos"], a)
                  if STATE["opts"].get("auto_rotate", True) else [])
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
                               groups=groups, log_dir=a / "logs",
                               target_spreads=target_spreads(), prefs=prefs())
            parent, comment_txt, summary = None, None, "initial plan"
        else:
            set_job("Revising the album", 45)
            pv, current = store.get()
            new = sorted(unseen_photos(store.raw(pv.version_no) or current, usable))
            res = planner.revise(current=current, comment=comment or "", photos=usable,
                                 g=STATE["g"], lab=STATE["lab"], language=STATE["language"],
                                 complete=complete, fallback=fallback,
                                 on_event=lambda m: set_job(m, 45),
                                 groups=groups, log_dir=a / "logs", prefs=prefs(),
                                 new_photos=new)
            parent, comment_txt, summary = pv.version_no, comment, res.layout.change_summary

        set_job("Applying your settings", 70)
        raw = res.layout.model_dump(mode="json", exclude_none=False)
        # What the model was shown, so photographs added later are known to
        # be new (and brought into the book) when the design is re-applied.
        raw["_seen_photos"] = [p.id for p in usable]
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
        placed = set(placed_ids(data))
        missing = [p.id for p in usable if p.id not in placed]
        Layout.model_validate(data)
        v = store.add(data, parent=pv.version_no, change_summary="your changes applied",
                      raw=raw, kind="settings")
        set_job("Printing the preview", 60)
        render_proof(store, v, data)
        with JOB_LOCK:
            JOB["result"] = {"version": v.version_no, "not_placed": missing,
                             "placed": sorted(placed),
                             # Photographs the user chose something for that
                             # are still not on a page: should never happen.
                             "not_placed_chosen": sorted(touched_photos(
                                 {p.id: p for p in usable}) - placed)}
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
    ap.add_argument("--album", type=Path, default=None,
                    help="the album folder to open (default: choose or create one in the editor)")
    ap.add_argument("--photos", type=Path, default=None,
                    help="its photographs (default: the folder the album was made with)")
    ap.add_argument("--captions", type=Path)
    ap.add_argument("--lab", type=Path, default=None,
                    help="lab file (default: chosen in the editor, else saal)")
    ap.add_argument("--format", default=None,
                    help="book size from the lab config (default 21x21, or what was last "
                         "chosen in the editor)")
    ap.add_argument("--title", default=None)
    ap.add_argument("--subtitle", default=None)
    ap.add_argument("--language", default=None, help="album language (default de)")
    providers.add_arguments(ap)
    ap.add_argument("--gap-hours", type=float, default=None)
    ap.add_argument("--radius-m", type=float, default=None)
    ap.add_argument("--no-geocode", action="store_true")
    ap.add_argument("--offline-places", action="store_true",
                    help="do not look up glacier, lake and peak names on OpenStreetMap "
                         "(that lookup sends photo coordinates to overpass-api.de)")
    ap.add_argument("--vision-px", type=int, default=None,
                    help=f"long edge sent to the vision model (default {annotate.VISION_PX})")
    ap.add_argument("--no-preflight", action="store_true",
                    help="skip the one-request model check at startup")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--allow-remote-settings", action="store_true",
                    help="allow API keys, folders and uploads from the editor even when it "
                         "listens on a non-local address")
    ap.add_argument("--browse-root", type=Path, default=None,
                    help="the folder browser starts here and cannot leave it "
                         "(default: your home folder)")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    # Nothing printed — a log line, a traceback — may carry an API key.
    sys.stdout = keystore.RedactingStream(sys.stdout)
    sys.stderr = keystore.RedactingStream(sys.stderr)

    # Options: a flag pins one for this run (shown locked in the editor).
    lab_paths = CFG.labs(ROOT / "phase0" / "labs")
    pinned = {}
    if args.lab is not None:
        lab_paths[args.lab.stem] = args.lab
        pinned["lab"] = args.lab.stem
    if args.gap_hours is not None:
        pinned["gap_hours"] = args.gap_hours
    if args.radius_m is not None:
        pinned["radius_m"] = args.radius_m
    if args.vision_px is not None:
        pinned["vision_px"] = str(args.vision_px)
    if args.no_geocode:
        pinned["geocode"] = False
        pinned["online_places"] = False
    elif args.offline_places:
        pinned["online_places"] = False
    local = args.host in ("127.0.0.1", "localhost", "::1")
    STATE.update(args=args, pinned=pinned, lab_paths=lab_paths, album=None,
                 models_locked_by=models_locked_by(args),
                 keys_editable=local or args.allow_remote_settings,
                 files_editable=local or args.allow_remote_settings,
                 browse_root=(args.browse_root or Path(os.environ.get("ALBUM_BROWSE_ROOT")
                                                       or Path.home())).expanduser().resolve(),
                 preflight_enabled=not args.no_preflight, preflight={})

    if args.album is not None:
        try:
            open_album(args.album, args.photos, args.captions, first=True)
        except ValueError as exc:
            raise SystemExit(str(exc))
        print(f"\n  {len(STATE['all'])} photographs · {len(STATE['groups'])} groups · {STATE['fmt']}")
        print(providers.describe_roles(STATE["providers"]))
        if not STATE["has_key"]:
            print("  no API key — editing works, designing the album does not")
    else:
        print("\n  no album given — choose or create one in the editor")
    print(f"\n  Open  http://{args.host}:{args.port}\n")

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
