"""Grouping photographs into moments before the planner sees them.

"Keep the same people and the same place together" is only enforceable if the
planner knows which photographs belong together. Captions alone are a weak
signal — people write "Salz auf der Haut", not "beach, day 3, the kids".

Two signals need no model at all and are almost free:

- **Time.** A gap longer than `gap_hours` between consecutive exposures is a new
  moment. This is how photographers actually shoot: bursts around an event,
  then nothing for hours.
- **Place.** GPS beyond `radius_m` from the running centre of the current
  cluster is a new place, even if the time is continuous.

What this cannot see is *who is in the frame*. That is what annotate.py is for.
The two combine: clusters give the skeleton, subject tags give the flesh.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from photos import Photo


@dataclass
class Group:
    id: str
    label: str
    photo_ids: list[str]
    place: str | None = None
    date_label: str | None = None


def haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    r = 6371000.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp = p2 - p1
    dl = math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def assign(photos: list[Photo], gap_hours: float = 4.0, radius_m: float = 400.0) -> list[Group]:
    """Assign every photo a group id, in chronological order where dates exist.

    Photos with no timestamp fall back to upload order, which is the user's own
    arrangement and usually already grouped by moment.
    """
    ordered = sorted(
        photos,
        key=lambda p: (p.taken_at is None, p.taken_at or __import__("datetime").datetime.min, p.upload_order),
    )
    groups: list[Group] = []
    current: list[Photo] = []
    anchor_time = None
    anchor_gps = None

    def flush():
        if not current:
            return
        gid = f"g{len(groups) + 1}"
        groups.append(
            Group(gid, describe(current), [p.id for p in current],
                  place=dominant_place(current), date_label=date_range(current))
        )
        for p in current:
            p.group = gid

    for p in ordered:
        new = False
        if current:
            if p.taken_at and anchor_time:
                if (p.taken_at - anchor_time).total_seconds() > gap_hours * 3600:
                    new = True
            if p.gps and anchor_gps and haversine_m(p.gps, anchor_gps) > radius_m:
                new = True
        if new:
            flush()
            current = []
        current.append(p)
        anchor_time = p.taken_at or anchor_time
        anchor_gps = p.gps or anchor_gps
    flush()
    return groups


def dominant_place(members: list[Photo]) -> str | None:
    """The place most of the group was shot in — the one the album will print."""
    places = [p.place for p in members if p.place]
    return max(set(places), key=places.count) if places else None


def date_range(members: list[Photo]) -> str | None:
    times = [p.taken_at for p in members if p.taken_at]
    if not times:
        return None
    lo, hi = min(times), max(times)
    if lo.date() == hi.date():
        return lo.strftime("%-d %B %Y")
    if (lo.year, lo.month) == (hi.year, hi.month):
        return f"{lo.strftime('%-d')}–{hi.strftime('%-d %B %Y')}"
    return f"{lo.strftime('%-d %b')} – {hi.strftime('%-d %b %Y')}"


def describe(members: list[Photo]) -> str:
    """A short human label so the group means something in the prompt."""
    bits = []
    if (pl := dominant_place(members)):
        bits.append(pl)
    if (dr := date_range(members)):
        bits.append(dr)
    bits.append(f"{len(members)} photo{'s' if len(members) > 1 else ''}")
    return ", ".join(bits)


def summary(groups: list[Group]) -> str:
    return "\n".join(f"{g.id}: {g.label}" for g in groups)
