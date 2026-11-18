# SPDX-FileCopyrightText: 2026 Alessandro Surace
# SPDX-License-Identifier: AGPL-3.0-only
"""Named landmarks near a photograph, from a list kept on this machine.

The lake, the glacier and the peak used to be asked of the public OpenStreetMap
Overpass servers, once per square kilometre. Those servers are shared and often
overloaded: every lookup could time out, and designing an album waited on them.

GeoNames publishes one file per country with the named glaciers, lakes, peaks,
passes, waterfalls, bays, beaches, valleys, rivers, castles and monuments and
where they are (CC BY 4.0, https://www.geonames.org). The file for each country
an album touches is downloaded ONCE, cut down to those kinds, and kept in the
user's config folder. Every lookup after that is local and instant, and the
only thing that ever left the machine is a country code.

    ~/.config/impaginai/gazetteer/CH.json     [[name, kind, lat, lon], ...]
"""

from __future__ import annotations

import json
import math
import threading
import time
from pathlib import Path

URL = "https://download.geonames.org/export/dump/{cc}.zip"
DOWNLOAD_TIMEOUT_S = 60.0
RETRY_AFTER_S = 15 * 60
CELL_DEG = 0.05                      # index bucket, about 5 km

# GeoNames feature code -> the kind names geocode._KINDS ranks.
KINDS = {
    "GLCR": "glacier",
    "LK": "lake", "LKS": "lake", "RSV": "lake", "LGN": "lake",
    "PK": "peak", "PKS": "peak", "MT": "peak",
    "PASS": "mountain_pass",
    "FLLS": "waterfall",
    "BAY": "bay", "COVE": "bay",
    "BCH": "beach",
    "VAL": "valley",
    "STM": "river",
    "CSTL": "castle",
    "MNMT": "monument",
}

_LOCK = threading.Lock()
_INDEX: dict[str, dict[tuple[int, int], list]] = {}     # cc -> bucket -> rows
_THREADS: dict[str, threading.Thread] = {}
_RETRY_AT: dict[str, float] = {}
_STATE: dict[str, str] = {}          # cc -> ready | downloading | unavailable
_SAID: set[str] = set()


def folder() -> Path:
    import keystore

    return keystore.config_dir() / "gazetteer"


def _bucket(lat: float, lon: float) -> tuple[int, int]:
    return (math.floor(lat / CELL_DEG), math.floor(lon / CELL_DEG))


def _install(cc: str, rows: list) -> None:
    idx: dict[tuple[int, int], list] = {}
    for row in rows:
        idx.setdefault(_bucket(row[2], row[3]), []).append(row)
    with _LOCK:
        _INDEX[cc] = idx
        _STATE[cc] = "ready"


def parse(lines) -> list:
    """Rows worth keeping from a GeoNames country file (tab-separated:
    id, name, asciiname, alternatenames, lat, lon, class, code, ...)."""
    rows = []
    for line in lines:
        c = line.rstrip("\n").split("\t")
        if len(c) < 8:
            continue
        kind = KINDS.get(c[7])
        if not kind or not c[1]:
            continue
        try:
            rows.append([c[1], kind, round(float(c[4]), 5), round(float(c[5]), 5)])
        except ValueError:
            continue
    return rows


def download(cc: str) -> list:
    """Fetch and cut down one country's file. Raises on any failure."""
    import io
    import tempfile
    import urllib.request
    import zipfile

    import os

    # ALBUM_GAZETTEER_URL points at a mirror: "https://example.org/dump/{cc}.zip"
    req = urllib.request.Request((os.environ.get("ALBUM_GAZETTEER_URL") or URL).format(cc=cc),
                                 headers={"User-Agent": "impaginai (self-hosted)"})
    with tempfile.TemporaryFile() as tmp:
        with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT_S) as r:
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                tmp.write(chunk)
        tmp.seek(0)
        with zipfile.ZipFile(tmp) as z:
            with z.open(f"{cc}.txt") as f:
                return parse(io.TextIOWrapper(f, encoding="utf-8"))


def load(cc: str) -> bool:
    """Bring a country already on disk into memory."""
    if cc in _INDEX:
        return True
    p = folder() / f"{cc}.json"
    try:
        rows = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    _install(cc, rows)
    return True


def _fetch(cc: str, fetch) -> None:
    try:
        rows = (fetch or download)(cc)
        import safeio

        folder().mkdir(parents=True, exist_ok=True)
        safeio.write_text(folder() / f"{cc}.json", json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        _install(cc, rows)
        print(f"  landmark names for {cc}: {len(rows)} kept on this machine")
    except Exception as exc:
        with _LOCK:
            _STATE[cc] = "unavailable"
            _RETRY_AT[cc] = time.monotonic() + RETRY_AFTER_S
        if cc not in _SAID:              # once per country, not on every reload
            _SAID.add(cc)
            print(f"  landmark names for {cc} could not be downloaded "
                  f"({str(exc)[:80]}); town names only for now")


def countries_for(points, reach_m: float = 2500.0) -> set[str]:
    """Country codes the points fall in — and the ones just across a border,
    so the glacier on the other side of the ridge is still found."""
    pts = [p for p in points if p is not None]
    if not pts:
        return set()
    try:
        import reverse_geocoder
    except ImportError:
        return set()
    # one sample per ~1 km cell is plenty, each with four neighbours
    cells = {(round(la, 2), round(lo, 2)) for la, lo in pts}
    probe = []
    for la, lo in cells:
        dlat = reach_m / 111_000.0
        dlon = dlat / max(0.2, math.cos(math.radians(la)))
        probe += [(la, lo), (la + dlat, lo), (la - dlat, lo), (la, lo + dlon), (la, lo - dlon)]
    try:
        res = reverse_geocoder.search(probe, mode=1, verbose=False)
    except Exception:
        return set()
    return {r["cc"] for r in res if r.get("cc")}


def prepare(points, fetch=None, wait_s: float | None = None) -> bool:
    """Make the landmark lists for these points' countries available: from
    disk when they are there, otherwise downloaded once. Waits at most
    `wait_s` for downloads (None: until they finish); one still running is
    left to finish in the background. True when a list became available that
    was not before."""
    before = set(_INDEX)
    waiting = []
    for cc in sorted(countries_for(points)):
        if load(cc):
            continue
        with _LOCK:
            t = _THREADS.get(cc)
            if t is None or not t.is_alive():
                if time.monotonic() < _RETRY_AT.get(cc, 0.0):
                    continue
                _STATE[cc] = "downloading"
                t = threading.Thread(target=_fetch, args=(cc, fetch), daemon=True)
                _THREADS[cc] = t
                t.start()
        waiting.append(t)
    end = None if wait_s is None else time.monotonic() + wait_s
    for t in waiting:
        t.join(None if end is None else max(0.0, end - time.monotonic()))
    return set(_INDEX) != before


def near(gps: tuple[float, float], radius_m: float = 2500.0) -> list[dict]:
    """Landmarks within reach of a point, from the lists in memory."""
    if not _INDEX:
        return []
    from geocode import _haversine_m

    lat, lon = gps
    span_lat = math.ceil(radius_m / 111_000.0 / CELL_DEG)
    span_lon = math.ceil(radius_m / (111_000.0 * max(0.2, math.cos(math.radians(lat)))) / CELL_DEG)
    b0 = _bucket(lat, lon)
    out = []
    for idx in list(_INDEX.values()):
        for i in range(b0[0] - span_lat, b0[0] + span_lat + 1):
            for j in range(b0[1] - span_lon, b0[1] + span_lon + 1):
                for name, kind, la, lo in idx.get((i, j), ()):
                    if _haversine_m(gps, (la, lo)) <= radius_m:
                        out.append({"name": name, "kind": kind, "lat": la, "lon": lo})
    return out


def status() -> dict:
    with _LOCK:
        return dict(_STATE)


def reset() -> None:
    """Forget everything in memory (tests)."""
    with _LOCK:
        _INDEX.clear()
        _THREADS.clear()
        _RETRY_AT.clear()
        _STATE.clear()
        _SAID.clear()
