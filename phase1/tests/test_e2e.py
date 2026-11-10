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
    path.write_text("\n".join(lines) + "\n")
    return path


def env() -> dict:
    e = {k: v for k, v in os.environ.items()
         if not k.startswith(("ALBUM_", "ANTHROPIC_", "OPENAI_", "OPENROUTER_"))}
    e["ALBUM_API_KEY"] = KEY
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

    def __init__(self, album: Path, models: Path, log: Path):
        self.port = free_port()
        self.log = log
        self.proc = subprocess.Popen(
            [sys.executable, str(REPO / "phase2" / "server.py"), "--album", str(album),
             "--photos", str(PHOTOS), "--captions", str(CAPTIONS), "--format", "21x21",
             "--no-geocode", "--models", str(models), "--port", str(self.port)],
            stdout=log.open("w"), stderr=subprocess.STDOUT, env=env())
        self.wait(lambda: self.get("/api/album") is not None, 90, "the editor did not start")

    def get(self, path: str):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=10) as r:
                return json.loads(r.read())
        except Exception:
            if self.proc.poll() is not None:
                raise AssertionError("the editor exited:\n" + self.log.read_text()[-3000:])
            return None

    def post(self, path: str, body: dict):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
                                     data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())

    def wait(self, cond, seconds: float, what: str):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if cond():
                return
            time.sleep(0.3)
        raise AssertionError(f"{what}\n--- editor log ---\n{self.log.read_text()[-3000:]}")

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
            first = json.loads(sorted((album_dir / "layouts").glob("v001.json"))[0].read_text())
            placed = [s["photo_id"] for sp in first["spreads"] for s in sp["slots"]]
            drop, cap = placed[1], placed[0]
            fake.requests.clear()
            ed.send("PATCH", "/api/photos", {"ids": [drop], "exclude": True})
            ed.send("PATCH", f"/api/photos/{cap}", {"description": "Unser Abend am Lej da Staz"})
            j = ed.job("/api/rerender")
            assert j.get("done") and not j.get("error"), j
            assert not fake.requests, "re-applying settings called the model"
            cur = json.loads(sorted((album_dir / "layouts").glob("v0*[0-9].json"))[-1].read_text())
            ids = [s["photo_id"] for sp in cur["spreads"] for s in sp["slots"]]
            assert drop not in ids, "the left-out photograph is still in the book"
            caps = [s.get("caption") for sp in cur["spreads"] for s in sp["slots"] if s["photo_id"] == cap]
            assert caps and all(c == "Unser Abend am Lej da Staz" for c in caps), caps
            assert all(sp.get("options", {}).get("aspects") or not T_pages(sp)
                       for sp in cur["spreads"]), "composed pages were not laid out for their photographs"
            assert (ed.get("/api/pages") or {}).get("count") == len(cur["spreads"]) * 2, "no page previews"
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
            ed.send("PATCH", "/api/photos", {"ids": [drop], "exclude": False})
            ed.job("/api/rerender")
            cur = json.loads(sorted((album_dir / "layouts").glob("v0*[0-9].json"))[-1].read_text())
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


if __name__ == "__main__":
    sys.exit(main())
