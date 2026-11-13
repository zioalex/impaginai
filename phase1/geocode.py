"""GPS to place names, offline.

Three sources, in order of authority:

1. **The user's own names** (`places.yaml` in the album directory). A nearest
   point wins within its radius. This exists because a reverse geocoder returns
   the nearest administrative settlement, and that is frequently not what a
   person calls the place: the beach below Baunei is "Cala Goloritze" to anyone
   who went there. The album should use the user's words.
2. **`reverse_geocoder`**, an offline package with a bundled settlement
   database. No network, no API key, no rate limit. Optional: if it is not
   installed the pipeline carries on without place names.
3. **Nothing.** A missing place is not an error; the album simply does not
   mention where it was.

Results are cached to `places_cache.json` so a second run is instant and, more
importantly, deterministic — the same album always prints the same place names.
"""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class NamedPlace:
    name: str
    lat: float
    lon: float
    radius_m: float = 1500.0


def _haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    r = 6371000.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def load_user_places(path: Path) -> list[NamedPlace]:
    """places.yaml:  - name: Cala Goloritze
                       lat: 40.1150
                       lon: 9.6900
                       radius_m: 2000
    """
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    out = []
    for item in raw:
        try:
            out.append(
                NamedPlace(
                    str(item["name"]),
                    float(item["lat"]),
                    float(item["lon"]),
                    float(item.get("radius_m", 1500.0)),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _key(gps: tuple[float, float]) -> str:
    # ~100m grid. Finer than this just inflates the cache with duplicates.
    return f"{gps[0]:.3f},{gps[1]:.3f}"


class Geocoder:
    def __init__(self, album_dir: Path, offline_db: bool = True):
        self.user_places = load_user_places(album_dir / "places.yaml")
        self.cache_path = album_dir / "places_cache.json"
        self.cache: dict[str, str | None] = {}
        if self.cache_path.exists():
            try:
                self.cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
            except Exception:
                self.cache = {}
        self.offline_db = offline_db
        self._rg = None
        self._rg_tried = False

    def _lookup_db(self, todo: list[tuple[float, float]]) -> dict[str, str]:
        if not self.offline_db or not todo:
            return {}
        if not self._rg_tried:
            self._rg_tried = True
            try:
                import reverse_geocoder

                self._rg = reverse_geocoder
            except ImportError:
                self._rg = None
        if self._rg is None:
            return {}
        try:
            # mode=1 is single-threaded: slower to start, but it does not fork,
            # which matters inside a worker process.
            results = self._rg.search(todo, mode=1, verbose=False)
        except Exception:
            return {}
        out = {}
        for gps, res in zip(todo, results):
            name = (res.get("name") or "").strip()
            admin = (res.get("admin1") or "").strip()
            label = name or admin
            if label and admin and admin != name:
                label = f"{name}, {admin}"
            if label:
                out[_key(gps)] = label
        return out

    def resolve(self, coords: list[tuple[float, float] | None]) -> list[str | None]:
        """Resolve many coordinates at once. One database load, not N."""
        # 1. the user's own names
        resolved: dict[str, str | None] = {}
        for gps in coords:
            if gps is None:
                continue
            k = _key(gps)
            if k in resolved:
                continue
            best, best_d = None, float("inf")
            for place in self.user_places:
                d = _haversine_m(gps, (place.lat, place.lon))
                if d <= place.radius_m and d < best_d:
                    best, best_d = place.name, d
            if best:
                resolved[k] = best

        # 2. the offline database, for whatever is left and not already cached
        todo = []
        for gps in coords:
            if gps is None:
                continue
            k = _key(gps)
            if k in resolved:
                continue
            if k in self.cache:
                resolved[k] = self.cache[k]
            elif gps not in todo:
                todo.append(gps)

        if todo:
            found = self._lookup_db(todo)
            for gps in todo:
                k = _key(gps)
                resolved[k] = found.get(k)
                self.cache[k] = found.get(k)
            self._save()

        return [None if g is None else resolved.get(_key(g)) for g in coords]

    def _save(self) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            import safeio

            safeio.write_text(self.cache_path, json.dumps(self.cache, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:
            pass


# --- named features nearby: the lake, the glacier, the peak ---------------
#
# A settlement database says "Pontresina". Nobody who stood on the ice calls
# it that: it was the Morteratsch glacier. OpenStreetMap knows the names of
# glaciers, lakes, peaks, passes, rivers and viewpoints.
#
# The first source is `gazetteer.py`: a per-country landmark list downloaded
# once and kept on this machine. It always answers. The public Overpass API,
# below, is the second: asked once per place (rounded to about a kilometre),
# cached in `features_cache.json`, and only ever from the background, because
# its servers are shared and time out often. It adds viewpoints and names in
# the album's language when it answers and costs nothing when it does not.

# The public instances are busy; one answers when the other is overloaded.
OVERPASS_URLS = ["https://overpass-api.de/api/interpreter",
                 "https://overpass.private.coffee/api/interpreter",
                 "https://overpass.kumi.systems/api/interpreter"]
# ALBUM_OVERPASS_URL replaces them, comma separated: your own Overpass server,
# which answers when the public ones do not.
if os.environ.get("ALBUM_OVERPASS_URL"):
    OVERPASS_URLS = [u.strip() for u in os.environ["ALBUM_OVERPASS_URL"].split(",") if u.strip()]
FEATURE_RADIUS_M = 2500
# Longer than the 15 s the query allows the server: giving up first (it was
# 12 s) threw away answers that were on their way.
TIMEOUT_S = 20.0
# After a failure, stop asking for this long. Every reload of the editor used
# to try again (and wait out the timeout again), printing the same line each time.
COOLDOWN_S = 15 * 60
_DOWN_UNTIL = 0.0
# A place that failed waits longer than the general cooldown, so the next
# round starts with the others instead of the same slow one.
CELL_COOLDOWN_S = 2 * 60 * 60
_CELL_RETRY: dict[str, float] = {}
_OSM_STATE = "idle"                  # idle | ok | unavailable, for the editor
_OSM_SAID = False

# kind of feature -> how strongly it names a place (lower wins at equal distance)
_KINDS = {
    "glacier": 0, "water": 0, "lake": 0, "peak": 1, "waterfall": 1,
    "mountain_pass": 1, "bay": 1, "beach": 1, "valley": 2, "river": 2,
    "viewpoint": 3, "attraction": 3, "castle": 3, "monument": 4,
}

_QUERY = """[out:json][timeout:15];
(
  nwr(around:{r},{lat},{lon})["natural"~"^(glacier|water|peak|valley|bay|beach)$"]["name"];
  nw(around:{r},{lat},{lon})["waterway"~"^(river|waterfall)$"]["name"];
  nwr(around:{r},{lat},{lon})["mountain_pass"="yes"]["name"];
  nwr(around:{r},{lat},{lon})["tourism"~"^(viewpoint|attraction)$"]["name"];
  nwr(around:{r},{lat},{lon})["historic"~"^(castle|monument)$"]["name"];
);
out center tags 60;"""


def _feature_key(gps: tuple[float, float]) -> str:
    return f"{gps[0]:.2f},{gps[1]:.2f}"


def _kind(tags: dict) -> str:
    for k in ("natural", "waterway", "tourism", "historic"):
        if tags.get(k):
            v = tags[k]
            if v == "water" and tags.get("water") in ("lake", "reservoir", "pond"):
                return "lake"
            return v
    if tags.get("mountain_pass") == "yes":
        return "mountain_pass"
    return "place"


def _overpass(lat: float, lon: float, language: str | None, timeout: float = TIMEOUT_S) -> list[dict]:
    import urllib.parse
    import urllib.request

    q = _QUERY.format(r=FEATURE_RADIUS_M, lat=f"{lat:.4f}", lon=f"{lon:.4f}")
    data, last = None, None
    for url in list(OVERPASS_URLS):
        req = urllib.request.Request(
            url, data=urllib.parse.urlencode({"data": q}).encode(),
            headers={"User-Agent": "impaginai (self-hosted)"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read().decode("utf-8"))
            if url != OVERPASS_URLS[0]:      # ask the one that answered first next time
                OVERPASS_URLS.remove(url)
                OVERPASS_URLS.insert(0, url)
            break
        except Exception as exc:
            last = exc
    if data is None:
        raise last or RuntimeError("no Overpass instance answered")
    out = []
    for el in data.get("elements", []):
        tags = el.get("tags") or {}
        name = (tags.get(f"name:{language}") if language else None) or tags.get("name")
        c = el.get("center") or {"lat": el.get("lat"), "lon": el.get("lon")}
        if not name or c.get("lat") is None:
            continue
        out.append({"name": name, "kind": _kind(tags), "lat": c["lat"], "lon": c["lon"]})
    return out


class Features:
    """Named geographic features near each photograph.

    Two sources, merged. The landmark list kept on this machine
    (`gazetteer.py`) answers at once and always. OpenStreetMap's answers, where
    its public servers gave one, add viewpoints and names in the album's
    language; they are cached per album in `features_cache.json`.

    `near()` never touches the network. `fetch_missing()` is the only thing
    here that does, and nothing waits on it."""

    def __init__(self, album_dir: Path, online: bool = True, language: str | None = None,
                 fetch=None, landmarks: bool = True):
        self.path = album_dir / "features_cache.json"
        self.online = online
        self.language = language
        self.landmarks = landmarks
        self.fetch = fetch or _overpass
        self.cache: dict[str, list[dict]] = {}
        if self.path.exists():
            try:
                self.cache = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                self.cache = {}
        self.failed = False

    def fetch_missing(self, points) -> int:
        """Ask OpenStreetMap about every cell not answered yet. Returns how
        many answered. Quiet: one line per session when it is unavailable."""
        global _DOWN_UNTIL, _OSM_STATE, _OSM_SAID
        answered = 0
        for gps in points:
            if gps is None:
                continue
            k = _feature_key(gps)
            now = time.monotonic()
            if (k in self.cache or not self.online or self.failed or now < _DOWN_UNTIL
                    or now < _CELL_RETRY.get(k, 0.0)):
                continue
            try:
                # An empty answer is cached too: "nothing named here" is an
                # answer, and asking again would only cost another wait.
                self.cache[k] = self.fetch(round(gps[0], 2), round(gps[1], 2), self.language)
            except Exception as exc:  # no network, overloaded, rate limited
                if not _OSM_SAID:
                    _OSM_SAID = True
                    print(f"  extra names from OpenStreetMap unavailable ({str(exc)[:60]}); "
                          f"using the landmark list on this machine")
                self.failed = True
                _OSM_STATE = "unavailable"
                _DOWN_UNTIL = time.monotonic() + COOLDOWN_S
                _CELL_RETRY[k] = time.monotonic() + CELL_COOLDOWN_S
                break
            answered += 1
            _OSM_STATE = "ok"
            self._save()
        return answered

    def near(self, gps: tuple[float, float]) -> list[dict]:
        if not self.landmarks:
            return []
        osm = self.cache.get(_feature_key(gps)) or []
        rows = list(osm)
        import gazetteer

        for f in gazetteer.near(gps, FEATURE_RADIUS_M):
            # The same lake from both sources: keep OpenStreetMap's, which
            # carries the name in the album's language.
            if any(o["kind"] == f["kind"] and (o["name"] == f["name"] or
                   _haversine_m((o["lat"], o["lon"]), (f["lat"], f["lon"])) < 400) for o in osm):
                continue
            rows.append(f)
        found = []
        for f in rows:
            d = _haversine_m(gps, (f["lat"], f["lon"]))
            found.append({**f, "distance_m": round(d)})
        found.sort(key=lambda f: (f["distance_m"] + 600 * _KINDS.get(f["kind"], 5)))
        seen, uniq = set(), []
        for f in found:
            if f["name"] not in seen:
                seen.add(f["name"])
                uniq.append(f)
        return uniq

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            import safeio

            safeio.write_text(self.path, json.dumps(self.cache, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:
            pass


def prefetch_gazetteer(photos, wait_s: float | None = None, fetch=None) -> bool:
    """Have the landmark lists for these photographs' countries on this
    machine. True when one became available that was not before."""
    import gazetteer

    return gazetteer.prepare([p.gps for p in photos], fetch=fetch, wait_s=wait_s)


def prefetch_overpass(photos, album_dir: Path, language: str | None = None, fetch=None) -> int:
    """Ask OpenStreetMap about every photograph's cell not answered yet.
    Returns how many cells answered. Meant for a background thread: it can
    take a minute and nothing should wait for it."""
    feats = Features(album_dir, online=True, language=language, fetch=fetch)
    return feats.fetch_missing([p.gps for p in photos])


def places_status() -> dict:
    """For the editor: where landmark names are coming from right now."""
    import gazetteer

    return {"countries": gazetteer.status(), "osm": _OSM_STATE}


def _short_town(label: str) -> str:
    return label.split(",")[0].strip()


def apply(photos, album_dir: Path, offline_db: bool = True, online: bool = False,
          language: str | None = None, fetch=None, landmarks: bool = True) -> int:
    """Fill photo.place for every photo that has GPS. Returns how many were named.

    Also fills photo.features (named glaciers, lakes, peaks... in reach,
    nearest first) and leads the place with the nearest strong one:
    "Morteratschgletscher · Pontresina" rather than "Pontresina, Grisons". A
    name the user gave in places.yaml is never overridden.

    Without `online` nothing is fetched: only what is already on this machine
    is used, so the editor can call this on every reload. With it (the command
    line, which has no background to work in) the landmark lists are downloaded
    if missing and OpenStreetMap is asked, here and now. `landmarks=False`
    leaves landmark names out altogether.
    """
    gc = Geocoder(album_dir, offline_db)
    names = gc.resolve([p.gps for p in photos])
    feats = Features(album_dir, online=online, language=language, fetch=fetch,
                     landmarks=landmarks)
    if online and landmarks:
        if offline_db:
            prefetch_gazetteer(photos)
        feats.fetch_missing([p.gps for p in photos])
    user_named = {pl.name for pl in gc.user_places}
    n = 0
    for p, name in zip(photos, names):
        if p.gps is not None:
            near = feats.near(p.gps)
            p.features = [f"{f['name']} ({f['kind'].replace('_', ' ')}, "
                          f"{f['distance_m'] / 1000:.1f} km)" for f in near[:5]]
            spot = next((f for f in near if _KINDS.get(f["kind"], 5) <= 1
                         and f["distance_m"] <= 2000), None)
            if spot and name not in user_named:
                name = f"{spot['name']} · {_short_town(name)}" if name else spot["name"]
        if name:
            p.place = name
            n += 1
    return n


def _check(lat: float, lon: float) -> None:
    """`python phase1/geocode.py check <lat> <lon>`: which source answers, how
    fast, and with what. For finding out why a place has no landmark name."""
    import gazetteer

    gps = (lat, lon)
    t = time.monotonic()
    ccs = gazetteer.countries_for([gps])
    print(f"countries in reach: {', '.join(sorted(ccs)) or 'none (is reverse_geocoder installed?)'}")
    gazetteer.prepare([gps])
    print(f"landmark list ({time.monotonic() - t:.1f}s, kept in {gazetteer.folder()}): "
          f"{gazetteer.status() or 'nothing loaded'}")
    t = time.monotonic()
    rows = gazetteer.near(gps, FEATURE_RADIUS_M)
    print(f"  {len(rows)} landmarks within {FEATURE_RADIUS_M} m, "
          f"found in {(time.monotonic() - t) * 1000:.0f} ms")
    for url in list(OVERPASS_URLS):
        t = time.monotonic()
        saved = list(OVERPASS_URLS)
        OVERPASS_URLS[:] = [url]
        try:
            n = len(_overpass(round(lat, 2), round(lon, 2), None))
            print(f"OpenStreetMap {url.split('/')[2]}: {n} names in {time.monotonic() - t:.1f}s")
        except Exception as exc:
            print(f"OpenStreetMap {url.split('/')[2]}: failed after "
                  f"{time.monotonic() - t:.1f}s ({str(exc)[:70]})")
        OVERPASS_URLS[:] = saved
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        feats = Features(Path(td), online=False)
        for f in feats.near(gps)[:8]:
            print(f"  {f['name']} ({f['kind']}, {f['distance_m'] / 1000:.1f} km)")


if __name__ == "__main__":
    import sys

    if len(sys.argv) == 4 and sys.argv[1] == "check":
        _check(float(sys.argv[2]), float(sys.argv[3]))
    else:
        raise SystemExit("usage: python phase1/geocode.py check <lat> <lon>")
