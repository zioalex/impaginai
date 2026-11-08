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
    raw = yaml.safe_load(path.read_text()) or []
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
                self.cache = json.loads(self.cache_path.read_text())
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
            self.cache_path.write_text(json.dumps(self.cache, ensure_ascii=False, indent=1))
        except Exception:
            pass


def apply(photos, album_dir: Path, offline_db: bool = True) -> int:
    """Fill photo.place for every photo that has GPS. Returns how many were named."""
    gc = Geocoder(album_dir, offline_db)
    names = gc.resolve([p.gps for p in photos])
    n = 0
    for p, name in zip(photos, names):
        if name:
            p.place = name
            n += 1
    return n
