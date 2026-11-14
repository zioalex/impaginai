"""End to end, with a mocked key and a fake model endpoint: no network, no cost.

    python phase1/tests/test_e2e.py

test_loop.py proves the planner's logic with scripted completers. This proves
the product: the real CLI and the real editor server, started as subprocesses,
talking HTTP to tests/fake_model.py, which misbehaves the way real providers
do. It covers what a scripted completer cannot:

- the key from the environment actually reaches the endpoint
- the startup model check, ok and failing
- a plan that survives a 503, an empty reply under json_schema (the original
  bug) and a layout delivered as tool-call arguments, in one build
- a revision that survives a reasoning model spending its whole budget
- the fallback model taking over from a model id that does not exist
- a refusal and a dead model ending in a readable error, the previous
  version kept

Every request the fake receives is recorded, so the assertions check what was
sent (response_format stepping down, the raised budget), not only that it
eventually worked.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]            # phase1/
REPO = ROOT.parent
sys.path.insert(0, str(REPO / "phase0"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fake_model import FakeModel  # noqa: E402
import test_loop  # noqa: E402  (fixtures and a known-good layout for these photos)

PHOTOS = REPO / "phase0" / "photos"
CAPTIONS = ROOT / "examples" / "captions.yaml"
KEY = "sk-mock-e2e-not-a-real-key"
CONFIG_DIR = tempfile.mkdtemp(prefix="album-e2e-config-")
SECRET = "sk-or-v1-e2e-stored-key-must-never-leak-7a7b"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def write_models(path: Path, url: str, model: str = "fake/model", **plan) -> Path:
    lines = ["default:", f"  base_url: {url}", f"  model: {model}", "  json_mode: true"]
    if plan:
        lines.append("plan:")
        lines += [f"  {k}: {v}" for k, v in plan.items()]
        lines.append("revise:")
        lines += [f"  {k}: {v}" for k, v in plan.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def env() -> dict:
    e = {k: v for k, v in os.environ.items()
         if not k.startswith(("ALBUM_", "ANTHROPIC_", "OPENAI_", "OPENROUTER_"))}
    e["ALBUM_API_KEY"] = KEY
    # Keys entered in the editor go here, never into the user's real config.
    e["ALBUM_CONFIG_DIR"] = CONFIG_DIR
    e["PYTHONUNBUFFERED"] = "1"
    return e


def album_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ROOT / "album.py"), *args],
                          capture_output=True, text=True, env=env(), timeout=300)


def common(album: Path, models: Path) -> list[str]:
    return ["--album", str(album), "--photos", str(PHOTOS), "--captions", str(CAPTIONS),
            "--format", "21x21", "--no-geocode", "--models", str(models)]


class Editor:
    """The phase-2 server as a subprocess, driven over its HTTP API."""

    def __init__(self, album: Path | None, models: Path | None, log: Path,
                 env_extra: dict | None = None, photos: Path | None | str = "default"):
        if photos == "default":
            photos = PHOTOS      # read at call time: a caller may point PHOTOS elsewhere
        self.port = free_port()
        self.log = log
        self.proc = subprocess.Popen(
            [sys.executable, str(REPO / "phase2" / "server.py"),
             *(["--album", str(album)] if album else []),
             *(["--photos", str(photos), "--captions", str(CAPTIONS)] if album and photos else []),
             "--format", "21x21",
             "--no-geocode", *(["--models", str(models)] if models else []),
             "--port", str(self.port)],
            stdout=log.open("w"), stderr=subprocess.STDOUT, env={**env(), **(env_extra or {})})
        self.wait(lambda: self.get("/api/album") is not None, 90, "the editor did not start")

    def get(self, path: str):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=10) as r:
                return json.loads(r.read())
        except Exception:
            if self.proc.poll() is not None:
                raise AssertionError("the editor exited:\n" + self.log.read_text(encoding="utf-8")[-3000:])
            return None

    def post(self, path: str, body: dict):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
                                     data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())

    def get_raw(self, path: str):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=10) as r:
            return json.loads(r.read())

    def put_raw(self, path: str, data: bytes):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, method="PUT")
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())

    def wait(self, cond, seconds: float, what: str):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if cond():
                return
            time.sleep(0.3)
        raise AssertionError(f"{what}\n--- editor log ---\n{self.log.read_text(encoding='utf-8')[-3000:]}")

    def send(self, method: str, path: str, body: dict):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
                                     data=json.dumps(body).encode(), method=method,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())

    def job(self, path: str, body: dict | None = None) -> dict:
        """Start a job (rerender, export) and wait for it."""
        self.post(path, body or {})
        result = {}

        def finished():
            j = self.get("/api/job") or {}
            result.update(j)
            return j.get("done") or j.get("error")
        self.wait(finished, 180, f"{path} did not finish")
        return result

    def build(self, comment: str | None = None) -> dict:
        self.post("/api/plan", {"comment": comment})
        result = {}

        def finished():
            j = self.get("/api/job") or {}
            result.update(j)
            return j.get("done") or j.get("error")
        self.wait(finished, 180, "the build did not finish")
        return result

    def versions(self) -> int:
        return len(self.get("/api/album")["versions"])

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def T_pages(sp: dict) -> bool:
    """Whether a spread is built from page compositions (and so takes aspects)."""
    import templates_lib
    spec = templates_lib.REGISTRY.get(sp["template"])
    return bool(spec and spec.pages) and bool(sp.get("slots"))


def T_pages_of(sp: dict) -> tuple[str, str]:
    """The (left, right) page compositions of a spread, as the template reads."""
    import templates_lib
    a, b = templates_lib.REGISTRY[sp["template"]].pages
    return (b, a) if (sp.get("options") or {}).get("side") == "right" else (a, b)


def main() -> int:
    test_loop.ensure_fixtures()
    fake = FakeModel(test_loop.good_layout())
    url = fake.start()
    tmp = Path(tempfile.mkdtemp(prefix="album-e2e-"))
    try:
        good = write_models(tmp / "good.yaml", url)

        # 1. the command-line model check
        r = album_cli("check", "--models", str(good))
        assert r.returncode == 0 and "plan: ok" in r.stdout, r.stdout + r.stderr
        r = album_cli("check", "--models", str(write_models(tmp / "typo.yaml", url, "fake/missing")))
        assert r.returncode == 1 and "unknown model" in r.stdout, r.stdout + r.stderr
        assert all(q["auth"] == f"Bearer {KEY}" for q in fake.requests), "the key never arrived"
        print("model check: ok")

        # 2. the editor: startup check, then a plan through three faults at once
        ed = Editor(tmp / "album", good, tmp / "editor.log")
        try:
            ed.wait(lambda: (ed.get("/api/album") or {}).get("preflight", {})
                    .get("plan", {}).get("status") == "ok", 30, "startup check never passed")

            fake.requests.clear()
            fake.set_faults(["503", "empty_schema", "tool_calls"])
            job = ed.build()
            assert job.get("done") and not job.get("error"), job
            plans = fake.of_kind("plan")
            assert [q["fault"] for q in plans] == ["503", "empty_schema", "tool_calls"], plans
            assert plans[1]["response_format"] == "json_schema", plans
            assert plans[2]["response_format"] == "json_object", "did not step down from json_schema"
            assert all(q["turns"] == 2 for q in plans), "a retry changed the conversation"
            assert ed.versions() == 1
            assert (ed.get("/api/report") or {}).get("ready"), "no proof after the plan"
            assert (tmp / "album" / "renders" / "inner-proof.pdf").exists()
            assert list((tmp / "album" / "logs").glob("plan-*.json")), "failed attempts not logged"
            print("editor plan through 503 + empty json_schema reply + tool-call reply: ok")

            # 2b. what the design session cost, live and logged: every answered
            #     call (annotate batches and plan attempts; the 503 was not
            #     answered), at the fake's reported $0.0125 each
            assert job["kind"] == "design" and job["result"]["ok"], job
            assert job["cost"]["calls"] >= 3 and abs(job["cost"]["usd"] - 0.0125 * job["cost"]["calls"]) < 1e-6, job["cost"]
            costs = ed.get("/api/costs")
            assert len(costs["sessions"]) == 1 and costs["total_usd"] == job["result"]["usd"], costs
            assert ed.get("/api/album")["versions"][-1]["cost_usd"] == job["result"]["usd"]
            print(f"session cost {costs['total_usd']} over {job['cost']['calls']} call(s): ok")

            # 2c. leaving photographs out and writing a caption reach the book
            #     with no model call; bringing one back restores it
            album_dir = tmp / "album"
            first = json.loads(sorted((album_dir / "layouts").glob("v001.json"))[0].read_text(encoding="utf-8"))
            placed = [s["photo_id"] for sp in first["spreads"] for s in sp["slots"]]
            drop, cap = placed[1], placed[0]
            fake.requests.clear()
            ed.send("PATCH", "/api/photos", {"ids": [drop], "exclude": True})
            ed.send("PATCH", f"/api/photos/{cap}", {"description": "Unser Abend am Lej da Staz"})
            j = ed.job("/api/rerender")
            assert j.get("done") and not j.get("error"), j
            assert not fake.requests, "re-applying settings called the model"
            cur = json.loads(sorted((album_dir / "layouts").glob("v0*[0-9].json"))[-1].read_text(encoding="utf-8"))
            ids = [s["photo_id"] for sp in cur["spreads"] for s in sp["slots"]]
            assert drop not in ids, "the left-out photograph is still in the book"
            caps = [s.get("caption") for sp in cur["spreads"] for s in sp["slots"] if s["photo_id"] == cap]
            assert caps and all(c == "Unser Abend am Lej da Staz" for c in caps), caps
            assert all(sp.get("options", {}).get("aspects") or not T_pages(sp)
                       for sp in cur["spreads"]), "composed pages were not laid out for their photographs"
            assert (ed.get("/api/pages") or {}).get("count") == len(cur["spreads"]) * 2, "no page previews"
            assert cur["spreads"][0]["template"].startswith(("front:", "front-framed:")), \
                f"the book does not open on the front page: {cur['spreads'][0]['template']}"
            pg = ed.get("/api/pages")
            assert isinstance(pg.get("low_res"), dict), pg
            ph = ed.get("/api/photos")
            assert all("low_res" in x and "sharp_cm" in x and "rotate" in x for x in ph)
            # turning a photograph by hand swaps its shape everywhere
            turn = ph[2]
            ed.send("PATCH", f"/api/photos/{turn['id']}", {"rotate": 90})
            back = next(x for x in ed.get("/api/photos") if x["id"] == turn["id"])
            assert (back["width"], back["height"]) == (turn["height"], turn["width"]), back
            ed.send("PATCH", f"/api/photos/{turn['id']}", {"rotate": 0})
            # "always with other photographs" and "full page" exclude each other
            ed.send("PATCH", f"/api/photos/{turn['id']}", {"compose": True})
            try:
                ed.send("PATCH", f"/api/photos/{turn['id']}", {"size": "full-page"})
                raise AssertionError("full page accepted on a composed photograph")
            except urllib.error.HTTPError as exc:
                assert exc.code == 400
            ed.send("PATCH", f"/api/photos/{turn['id']}", {"compose": False})

            # 2d. the Settings tab: options, locks, live group preview, and a
            #     change that re-applies to the book without a model call
            cfg = ed.get("/api/config")
            keys = {o["key"]: o for o in cfg["options"]}
            assert {"lab", "length", "density", "selection", "captions", "gap_hours",
                    "radius_m", "vision_px", "auto_rotate", "geocode", "online_places"} <= set(keys)
            assert keys["geocode"]["locked"], "--no-geocode should lock place names"
            try:
                ed.send("PUT", "/api/config", {"geocode": True})
                raise AssertionError("a locked setting was changed")
            except urllib.error.HTTPError as exc:
                assert exc.code == 409
            try:
                ed.send("PUT", "/api/config", {"density": "enormous"})
                raise AssertionError("an invalid value was accepted")
            except urllib.error.HTTPError as exc:
                assert exc.code == 400
            prev = ed.get("/api/groups/preview?gap_hours=1&radius_m=100")
            assert prev["count"] >= cfg["group_count"], (prev, cfg["group_count"])
            r = ed.send("PUT", "/api/config", {"gap_hours": 8})
            assert r["changed"] == ["gap_hours"] and r["needs"] == ["rerender"], r
            assert ed.get("/api/config")["options"][[o["key"] for o in cfg["options"]].index("gap_hours")]["value"] == 8
            saved = json.loads((album_dir / "settings.json").read_text(encoding="utf-8"))
            assert saved["options"]["gap_hours"] == 8 and "geocode" not in saved["options"], saved
            fake.requests.clear()
            r = ed.send("PUT", "/api/config", {"captions": "none"})
            assert r["needs"] == ["rerender", "redesign"], r
            j = ed.job("/api/rerender")
            assert j.get("done") and not j.get("error"), j
            assert not fake.requests, "a settings change called the model"
            cur = json.loads(sorted((album_dir / "layouts").glob("v0*[0-9].json"))[-1].read_text(encoding="utf-8"))
            caps = {s.get("caption") for sp in cur["spreads"] for s in sp["slots"]} - {None}
            mine = {x["description"] or x["caption"] for x in ed.get("/api/photos")} - {None}
            assert caps and caps <= mine, caps - mine
            ed.send("PUT", "/api/config", {"captions": "personal", "gap_hours": 4})
            print("settings tab: options, locks, preview, re-applied without the model: ok")

            # 2e. models and keys: --models locks the choice; a key can be
            #     stored, the environment's still wins, and it is never shown
            m = ed.get("/api/models")
            assert m["locked_by"] and "--models" in m["locked_by"], m["locked_by"]
            assert m["roles"]["plan"]["effort"][-1] == "high" and "high" not in m["roles"]["revise"]["effort"]
            try:
                ed.send("PUT", "/api/models", {"preset": "local"})
                raise AssertionError("a locked model choice was changed")
            except urllib.error.HTTPError as exc:
                assert exc.code == 409
            st = ed.send("PUT", "/api/keys", {"slot": "openrouter", "key": SECRET})
            assert st["source"] == "env" and st["stored"], st
            assert SECRET not in json.dumps(st) and SECRET not in json.dumps(ed.get("/api/models"))
            assert m["estimate"]["total_usd"] >= 0
            print("models locked by --models; key stored, environment key wins, never shown: ok")
            ed.send("PATCH", "/api/photos", {"ids": [drop], "exclude": False})
            ed.job("/api/rerender")
            cur = json.loads(sorted((album_dir / "layouts").glob("v0*[0-9].json"))[-1].read_text(encoding="utf-8"))
            assert drop in [s["photo_id"] for sp in cur["spreads"] for s in sp["slots"]], "could not bring it back"
            assert len(ed.get("/api/costs")["sessions"]) == 1, "a settings change was counted as a session"
            print("settings re-applied without the model, photograph left out and brought back: ok")

            # 2d. book size and exports
            r = ed.send("PUT", "/api/settings", {"format": "30x30"})
            assert r["changed"] == ["format"], r
            j = ed.job("/api/export", {"kind": "jpeg", "dpi": 30})
            assert j.get("done") and not j.get("error"), j
            import zipfile
            z = zipfile.ZipFile(album_dir / "exports" / "pages.zip")
            assert len(z.namelist()) >= 24, z.namelist()
            j = ed.job("/api/export", {"kind": "print"})
            assert j.get("done") and not j.get("error"), j
            from pypdf import PdfReader
            pg = PdfReader(str(album_dir / "exports" / "inner-print.pdf")).pages[0]
            assert abs(float(pg.mediabox.width) / 72 * 25.4 - 306) < 0.5, "export ignored the book size"
            ed.send("PUT", "/api/settings", {"format": "21x21"})
            ed.job("/api/rerender")
            print("book size and exports (page images, print PDF at 30x30): ok")

            # 3. a revision whose first reply is all reasoning and no content
            fake.requests.clear()
            fake.set_faults(["length"])
            before = ed.versions()
            job = ed.build("page 4 is too crowded")
            assert job.get("done") and not job.get("error"), job
            rev = fake.of_kind("revise")
            assert len(rev) == 2 and rev[1]["max_tokens"] == 32000, rev
            assert rev[1]["reasoning"] in ("low", {"effort": "low"}), rev
            assert ed.versions() == before + 1
            print("editor revise through an exhausted reasoning budget: ok")

            # 4. a refusal: readable error, nothing lost, no pointless retries
            fake.requests.clear()
            fake.set_faults(["refusal"])
            job = ed.build("make it nicer")
            assert job.get("error") and "refused" in job["error"], job
            assert not job["error"].startswith("PlanningError"), job["error"]
            assert len(fake.of_kind("revise")) == 1, "a refusal was retried"
            assert ed.versions() == before + 1, "a failed revision changed the album"
            print("editor refusal reported, previous version kept: ok")
        finally:
            ed.stop()

        # 4a. what the editor counts as used is in the design: a photograph
        #     the model never placed, marked full page, is on a page; one
        #     marked "two pages" spans both; one too small for that is refused
        short = test_loop.good_layout()
        grid = next(sp for sp in short["spreads"] if sp["template"] == "grid-four")
        grid["template"] = "trio-page"
        grid["slots"] = [x for x in grid["slots"] if x["photo_id"] != "p012"]
        fake2 = FakeModel(short)
        url2 = fake2.start()
        ed5 = Editor(tmp / "album5", write_models(tmp / "m5.yaml", url2), tmp / "editor5.log")
        try:
            job = ed5.build()
            assert job.get("done") and not job.get("error"), job
            a5 = tmp / "album5"

            def latest():
                return json.loads(sorted((a5 / "layouts").glob("v0*[0-9].json"))[-1].read_text(encoding="utf-8"))

            ed5.send("PATCH", "/api/photos/p012", {"size": "full-page"})
            j = ed5.job("/api/rerender")
            assert j.get("done") and not j.get("error"), j
            cur = latest()
            hosts = [(sp, i) for sp in cur["spreads"] for i, s in enumerate(sp["slots"]) if s["photo_id"] == "p012"]
            assert len(hosts) == 1, [sp["template"] for sp in cur["spreads"]]
            sp12, i12 = hosts[0]
            pages12 = T_pages_of(sp12)
            assert pages12[0 if i12 < __import__("compose").COMPOSITIONS[pages12[0]].slots else 1] == "bleed", \
                f"p012 is not full page: {sp12['template']}"
            res = j["result"]
            placed_layout = sorted({s["photo_id"] for sp in cur["spreads"] for s in sp["slots"]})
            assert res["placed"] == placed_layout, (res["placed"], placed_layout)
            assert "p012" not in res["not_placed_chosen"]
            phs = ed5.get("/api/photos")
            assert {x["id"] for x in phs if x["placed"]} == set(placed_layout), "placed does not match the layout"
            assert ed5.get("/api/album")["placed"] == placed_layout
            assert "p012" in (a5 / "renders" / "inner-proof.html").read_text(encoding="utf-8"), "p012 is not in the rendered book"

            # two pages: a high-resolution photograph spans the fold
            ed5.send("PATCH", "/api/photos/p004", {"size": "spread"})
            ed5.job("/api/rerender")
            cur = latest()
            spans = [sp for sp in cur["spreads"] if sp["template"] == "user-spread"]
            assert len(spans) == 1 and spans[0]["slots"][0]["photo_id"] == "p004", spans
            import re
            html = (a5 / "renders" / "inner-proof.html").read_text(encoding="utf-8")
            imgs = re.findall(r'<img [^>]*p004[^>]*style="([^"]*)"', html)
            # (the photograph may also sit on an ordinary page elsewhere)
            imgs = [x for x in imgs if float(re.search(r"width:([\d.]+)mm", x).group(1)) > 250]
            drawn = {re.match(r"left:([\d.\-]+)mm;top:([\d.\-]+)mm;width:([\d.]+)mm", x).groups() for x in imgs}
            assert len(imgs) == 2 and len(drawn) == 1, "one picture should be drawn on both pages: " + str(imgs)
            left, _, width = (float(v) for v in drawn.pop())
            assert left < 213 < left + width, (left, width)   # across the 21x21 fold at 213 mm
            assert ed5.get("/api/report")["pages"] == len(cur["spreads"]) * 2
            # too small to span: refused with a readable reason
            try:
                ed5.send("PATCH", "/api/photos/p013", {"size": "spread"})
                raise AssertionError("a 700 px photograph was allowed to span two pages")
            except urllib.error.HTTPError as exc:
                assert exc.code == 400, exc.code
            p4 = next(x for x in ed5.get("/api/photos") if x["id"] == "p004")
            assert p4["size"] == "spread" and p4["can"]["spread"] == "ok", p4
            # "two pages" and "always with other photographs" exclude each other
            try:
                ed5.send("PATCH", "/api/photos/p004", {"compose": True})
                ed5.send("PATCH", "/api/photos/p004", {"size": "spread"})
                raise AssertionError("spread accepted on a composed photograph")
            except urllib.error.HTTPError as exc:
                assert exc.code == 400
        finally:
            ed5.stop()
            fake2.stop()
        print("photographs marked full page / two pages are in the design; placed matches the book: ok")

        # 4b. models chosen in the editor (no --models): a preset, a role's
        #     model and effort; High only for the planner; kept in settings
        ed2 = Editor(tmp / "album2", None, tmp / "editor2.log",
                     env_extra={"ALBUM_API_KEY": ""})
        try:
            m = ed2.get("/api/models")
            assert not m["locked_by"], m["locked_by"]
            ed2.send("PUT", "/api/models", {"preset": "cloud-cheap"})
            m = ed2.get("/api/models")
            assert m["preset"] == "cloud-cheap" and "openrouter" in m["roles"]["plan"]["base_url"], m
            keys = {k["slot"]: k for k in m["keys"]}
            assert keys["openrouter"]["source"] == "user", keys      # stored in 2e
            assert keys["openrouter"]["masked"].endswith(SECRET[-4:])
            ed2.send("PUT", "/api/models", {"roles": {"plan": {"base_url": url, "model": "fake/model",
                                                              "reasoning": "high"}}})
            plan = ed2.get("/api/models")["roles"]["plan"]
            assert plan["reasoning"] == "high" and plan["max_tokens"] >= 32000, plan
            try:
                ed2.send("PUT", "/api/models", {"roles": {"revise": {"reasoning": "high"}}})
                raise AssertionError("High was accepted for revise")
            except urllib.error.HTTPError as exc:
                assert exc.code == 400
            saved = json.loads((tmp / "album2" / "settings.json").read_text(encoding="utf-8"))
            assert saved["models"]["plan"]["reasoning"] == "high" and saved["preset"] is None, saved
            assert ed2.get("/api/estimate")["high"] is True
            lst = ed2.get("/api/models/list?role=plan")
            assert isinstance(lst["models"], list) and ("error" in lst), lst
            with urllib.request.urlopen(f"http://127.0.0.1:{ed2.port}/api/models/export", timeout=10) as r:
                y = r.read().decode()
                assert "attachment" in r.headers.get("Content-Disposition", "")
            assert "fake/model" in y and "reasoning: high" in y and SECRET not in y and KEY not in y, y
        finally:
            ed2.stop()
        print("models chosen in the editor: preset, model, High for the planner only: ok")

        # 4c. albums in the editor: started with no album, a folder browser that
        #     stays inside its root, a new album from a folder, uploads, a new
        #     album to drop photos into, recent albums, reopening by --album alone
        import shutil
        home = tmp / "home"
        trip = home / "Pictures" / "trip"
        trip.mkdir(parents=True)
        for f in sorted(PHOTOS.glob("*.jpg"))[:4]:
            shutil.copy(f, trip)
        lib_env = {"ALBUM_BROWSE_ROOT": str(home), "ALBUM_LIBRARY": str(home / "Albums")}
        ed3 = Editor(None, None, tmp / "editor3.log", env_extra=lib_env)
        try:
            assert ed3.get("/api/album")["open"] is False
            try:
                ed3.send("PATCH", "/api/photos/p001", {"exclude": True})
                raise AssertionError("an album endpoint answered with no album open")
            except urllib.error.HTTPError as exc:
                assert exc.code == 409
            fs = ed3.get("/api/fs?path=Pictures")
            assert [(d["name"], d["photos"]) for d in fs["dirs"]] == [("trip", 4)], fs
            for bad in ("/etc", "../..", str(tmp)):
                try:
                    ed3.get_raw(f"/api/fs?path={urllib.parse.quote(bad)}")
                    raise AssertionError(f"the folder browser left its root: {bad}")
                except urllib.error.HTTPError as exc:
                    assert exc.code == 403, (bad, exc.code)
            r = ed3.post("/api/albums", {"name": "Engadin 2026", "photos": str(trip)})
            assert r["photo_count"] == 4 and r["album"].endswith("Engadin-2026"), r
            jpg = sorted(PHOTOS.glob("*.jpg"))[5].read_bytes()
            up = ed3.put_raw("/api/upload?name=" + urllib.parse.quote("my photo (1).JPG"), jpg)
            assert up["name"] == "my_photo_1.jpg", up
            for name, body in (("notes.txt", b"hello"), ("fake.jpg", b"not an image")):
                try:
                    ed3.put_raw("/api/upload?name=" + name, body)
                    raise AssertionError(f"{name} was accepted")
                except urllib.error.HTTPError as exc:
                    assert exc.code == 400
            assert ed3.post("/api/photos/reload", {})["photo_count"] == 5
            assert (trip / "my_photo_1.jpg").exists() and not list(trip.glob(".*part"))
            r = ed3.post("/api/albums", {"name": "Dropped"})
            assert r["photo_count"] == 0 and r["photos"].endswith("Dropped/photos"), r
            rec = ed3.get("/api/albums")["recent"]
            assert [x["title"] for x in rec][:2] == ["Dropped", "Engadin 2026"], rec
            first = next(x for x in rec if x["title"] == "Engadin 2026")
            assert ed3.post("/api/albums", {"album": first["album"]})["photo_count"] == 5
            saved = json.loads((Path(first["album"]) / "settings.json").read_text(encoding="utf-8"))
            assert saved["photos_dir"] == str(trip.resolve()), saved

            # photographs added after the design are brought into the book at
            # once (no model call), and a revision is told to place them
            ed3.send("PUT", "/api/models", {"roles": {r: {"base_url": url, "model": "fake/model"}
                                                      for r in ("annotate", "plan", "revise")}})
            job = ed3.build()
            assert job.get("done") and not job.get("error"), job
            album3 = Path(first["album"])
            v1 = json.loads(sorted((album3 / "layouts").glob("v0*[0-9].json"))[-1].read_text(encoding="utf-8"))
            before = {s["photo_id"] for sp in v1["spreads"] for s in sp["slots"]}
            jpg2 = sorted(PHOTOS.glob("*.jpg"))[6].read_bytes()
            added = ed3.put_raw("/api/upload?name=added_later.jpg", jpg2)["id"]
            ed3.post("/api/photos/reload", {})
            fake.requests.clear()
            j = ed3.job("/api/rerender")
            assert j.get("done") and not j.get("error"), j
            assert not fake.requests, "bringing in an added photograph called the model"
            cur = json.loads(sorted((album3 / "layouts").glob("v0*[0-9].json"))[-1].read_text(encoding="utf-8"))
            after = {s["photo_id"] for sp in cur["spreads"] for s in sp["slots"]}
            assert added in after and added not in before, (added, after)
            fake.requests.clear()
            job = ed3.build("make it calmer")
            assert job.get("done") and not job.get("error"), job
            rv = fake.of_kind("revise")
            assert rv and rv[0]["new_photos"], "the revision was not told about the new photograph"
            # a design made before designs recorded what the model saw: a
            # photograph whose file is newer than the design counts as new
            for f in (album3 / "layouts").glob("v*.raw.json"):
                d = json.loads(f.read_text(encoding="utf-8"))
                d.pop("_seen_photos", None)
                f.write_text(json.dumps(d), encoding="utf-8")
            time.sleep(1.5)
            late = ed3.put_raw("/api/upload?name=added_much_later.jpg",
                               sorted(PHOTOS.glob("*.jpg"))[7].read_bytes())["id"]
            ed3.post("/api/photos/reload", {})
            j = ed3.job("/api/rerender")
            assert j.get("done") and not j.get("error"), j
            cur = json.loads(sorted((album3 / "layouts").glob("v0*[0-9].json"))[-1].read_text(encoding="utf-8"))
            assert late in {s["photo_id"] for sp in cur["spreads"] for s in sp["slots"]}, \
                "a photograph added after an older design was not brought in"
            # --- what another website, or a crafted request, must not get ---
            import http.server
            import threading
            seen = []

            class Sink(http.server.BaseHTTPRequestHandler):
                def do_GET(self):
                    seen.append(self.headers.get("Authorization"))
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"data": []}')

                def log_message(self, *a):
                    pass

            sink = http.server.HTTPServer(("127.0.0.1", 0), Sink)
            threading.Thread(target=sink.serve_forever, daemon=True).start()
            try:
                # the address has "api.openai.com" in its path, as an attacker's would
                bait = urllib.parse.quote(f"http://127.0.0.1:{sink.server_port}/api.openai.com", safe="")
                ed3.get_raw(f"/api/models/list?role=plan&base_url={bait}")
                assert seen == [None], f"an API key was sent to an endpoint nobody configured: {seen}"
            finally:
                sink.shutdown()

            def refused(method, path, body=None, **headers):
                req = urllib.request.Request(
                    f"http://127.0.0.1:{ed3.port}{path}", method=method,
                    data=json.dumps(body).encode() if body is not None else None,
                    headers={"Content-Type": "application/json", **headers})
                try:
                    urllib.request.urlopen(req, timeout=10).read()
                except urllib.error.HTTPError as exc:
                    return exc.code
                return None

            assert refused("GET", "/api/models/list", **{"Sec-Fetch-Site": "cross-site"}) == 403
            assert refused("GET", "/api/album", Host="evil.example") == 403, "DNS rebinding"
            assert refused("PUT", "/api/keys", {"slot": "openai", "key": "sk-attacker-000000000000"},
                           Origin="https://evil.example") == 403
            assert refused("GET", "/api/album", **{"Sec-Fetch-Site": "same-origin"}) is None
            outside = tmp / "outside-the-root" / "x"
            assert refused("POST", "/api/albums",
                           {"album": str(outside), "photos": str(home / "Pictures")}) == 403
            assert not outside.exists(), "an album was created outside the browse root"
            print("other websites refused; keys stay with their host; albums stay in their root: ok")
            lib2 = home / "Elsewhere"
            lib2.mkdir()
            try:
                ed3.send("PUT", "/api/library", {"path": str(tmp)})
                raise AssertionError("a library outside the browse root was accepted")
            except urllib.error.HTTPError as exc:
                assert exc.code == 403
            # ALBUM_LIBRARY in the environment still decides, and says so
            assert ed3.send("PUT", "/api/library", {"path": str(lib2)})["library"] == str(home / "Albums")
        finally:
            ed3.stop()
        ed4 = Editor(Path(first["album"]), None, tmp / "editor4.log", env_extra=lib_env, photos=None)
        try:
            a = ed4.get("/api/album")
            assert a["open"] and a["title"] == "Engadin 2026" and len(ed4.get("/api/photos")) == 7, a
        finally:
            ed4.stop()
        print("albums: welcome, folder browser in its root, new/open/recent, uploads, reopen: ok")

        # the keys never reach a file the editor writes, nor its output
        leaks = []
        for root in (tmp, Path(CONFIG_DIR)):
            for f in root.rglob("*"):
                if f.is_file() and f.name != "keys.json" and f.suffix not in (".jpg", ".pdf", ".png"):
                    t = f.read_text(errors="ignore")
                    if SECRET in t or KEY in t:
                        leaks.append(str(f))
        assert not leaks, f"an API key was written to: {leaks}"
        print("no API key in any file or log the editor wrote: ok")

        # 5. a model id that does not exist, rescued by the fallback model
        fake.requests.clear()
        fake.set_faults([])
        rescue = write_models(tmp / "rescue.yaml", url, "fake/missing",
                              fallback_model="fake/model")
        r = album_cli("plan", *common(tmp / "cli", rescue))
        assert r.returncode == 0, r.stdout + r.stderr
        assert "switching to the fallback model" in r.stdout, r.stdout
        assert [q["model"] for q in fake.of_kind("plan")] == ["fake/missing", "fake/model"], fake.requests
        print("fallback model takes over from an unknown model id: ok")

        # 6. no fallback and a dead endpoint: a clear failure, the album untouched
        dead = write_models(tmp / "dead.yaml", url, "fake/missing")
        r = album_cli("revise", *common(tmp / "cli", dead), "--comment", "anything")
        out = r.stdout + r.stderr
        assert r.returncode == 2 and "planning failed:" in r.stderr and "not found" in out, out
        assert "Traceback" not in out, "an expected failure printed a traceback"
        hist = album_cli("history", "--album", str(tmp / "cli"))
        assert hist.stdout.count("v00") == 1, hist.stdout
        print("unknown model without a fallback fails clearly, album untouched: ok")
    finally:
        fake.stop()

    print("end to end: ok")
    return 0


def test_end_to_end():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
