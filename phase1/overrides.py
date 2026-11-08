"""Per-photo instructions from the user, and the pass that applies them.

The planner is good at sequencing and bad at knowing that this particular
photograph is the one of your daughter that has to be big. So the user gets a
direct channel that does not go through the model at all.

`overrides.yaml` in the album directory:

    p002:
      description: Sie steht lange am Rand und schaut hinunter
      size: full-page          # full-page | large | small | auto
      treatment: frame         # none | frame | rounded | vignette | soft-edge
    p016:
      exclude: true

Why these are applied AFTER planning rather than only asked for in the prompt:
a request in a prompt is a wish. The user clicking "make this full page" in the
UI expects it to be true, and to stay true through the next revision. So the
overrides are re-applied to every layout version, deterministically, and the
model is told about them as well so that it plans around them rather than
against them.

`size: full-page` is the only one that changes structure. It pulls the photo out
of its spread, downgrades that spread to a template with one fewer slot, and
inserts a full-bleed spread immediately after — so the sequence is preserved and
the page count grows by one spread.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

SIZES = ("auto", "full-page", "large", "small")
TREATMENTS = ("none", "frame", "rounded", "vignette", "soft-edge")

# Which template to fall back to when a spread loses one photograph. Chosen to
# keep the character of the spread rather than merely the slot count.
DOWNGRADE = {
    "mosaic-spread": "hero-and-trio",      # 6 -> 4, loses two; extras re-slotted
    "hero-and-trio": "hero-and-pair",      # 4 -> 3
    "column-and-solo": "solo-and-pair",    # 4 -> 3
    "grid-four": "trio-page",              # 4 -> 3
    "hero-and-pair": "two-up-hero",        # 3 -> 2
    "solo-and-pair": "two-up-equal",       # 3 -> 2
    "trio-page": "duo-page",               # 3 -> 2
    "two-up-hero": "full-bleed-single",    # 2 -> 1
    "two-up-equal": "single-centered",     # 2 -> 1
    "duo-page": "single-centered",         # 2 -> 1
    "full-bleed-single": None,             # 1 -> 0, drop the spread
    "single-centered": None,
    "trio-page ": None,
    "photo-and-text": None,
    "section-divider": "section-divider-plain",
    "title-page": "text-page",
}


@dataclass
class Override:
    description: str | None = None
    size: str = "auto"
    treatment: str = "none"
    exclude: bool = False

    def is_empty(self) -> bool:
        return (
            self.description is None
            and self.size == "auto"
            and self.treatment == "none"
            and not self.exclude
        )

    def summary(self) -> str:
        bits = []
        if self.size != "auto":
            bits.append(self.size)
        if self.treatment != "none":
            bits.append(self.treatment)
        if self.exclude:
            bits.append("excluded")
        return ", ".join(bits)


@dataclass
class Overrides:
    by_id: dict[str, Override] = field(default_factory=dict)
    path: Path | None = None

    @classmethod
    def load(cls, album_dir: Path) -> "Overrides":
        p = album_dir / "overrides.yaml"
        if not p.exists():
            return cls({}, p)
        raw = yaml.safe_load(p.read_text()) or {}
        out = {}
        for pid, item in raw.items():
            if not isinstance(item, dict):
                continue
            size = str(item.get("size", "auto"))
            treat = str(item.get("treatment", "none"))
            out[str(pid)] = Override(
                description=(str(item["description"]) if item.get("description") else None),
                size=size if size in SIZES else "auto",
                treatment=treat if treat in TREATMENTS else "none",
                exclude=bool(item.get("exclude", False)),
            )
        return cls(out, p)

    def save(self) -> None:
        if self.path is None:
            return
        data = {
            pid: {k: v for k, v in asdict(o).items()
                  if not (k == "size" and v == "auto")
                  and not (k == "treatment" and v == "none")
                  and not (k == "exclude" and v is False)
                  and not (k == "description" and v is None)}
            for pid, o in sorted(self.by_id.items())
            if not o.is_empty()
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=True))

    def get(self, pid: str) -> Override:
        return self.by_id.get(pid, Override())

    def set(self, pid: str, **kw) -> Override:
        o = self.by_id.setdefault(pid, Override())
        for k, v in kw.items():
            setattr(o, k, v)
        return o

    def excluded(self) -> set[str]:
        return {pid for pid, o in self.by_id.items() if o.exclude}


# --- applying ----------------------------------------------------------


def apply_to_photos(photos, ov: Overrides) -> list:
    """Caption from the user's description, and drop excluded photographs."""
    kept = []
    for p in photos:
        o = ov.get(p.id)
        if o.exclude:
            continue
        if o.description:
            p.caption = o.description
        p.override = o
        kept.append(p)
    return kept


def _template_slots(name: str) -> int:
    import templates_lib

    spec = templates_lib.REGISTRY.get(name)
    return spec.slot_count if spec else 0


def _downgrade(template: str, needed: int) -> str | None:
    """A template taking `needed` slots, closest in character to `template`."""
    import templates_lib

    if needed == 0:
        return DOWNGRADE.get(template) if _template_slots(DOWNGRADE.get(template) or "") == 0 else None
    cand = DOWNGRADE.get(template)
    if cand and _template_slots(cand) == needed:
        return cand
    for name, spec in templates_lib.REGISTRY.items():
        if spec.planner_visible and spec.slot_count == needed and not spec.spans_fold:
            return name
    return None


def apply_to_layout(layout: dict, ov: Overrides) -> list[str]:
    """Re-apply the user's instructions to a planned layout. Returns a log.

    Runs on every version, so an override set once survives every later
    revision without the user having to repeat themselves to the model.
    """
    notes: list[str] = []
    spreads = layout.get("spreads", [])

    # 1. drop excluded photographs wherever the planner put them
    excluded = ov.excluded()
    if excluded:
        for sp in spreads:
            before = len(sp.get("slots", []))
            sp["slots"] = [s for s in sp["slots"] if s["photo_id"] not in excluded]
            if len(sp["slots"]) != before:
                want = len(sp["slots"])
                newt = _downgrade(sp["template"], want)
                if newt:
                    notes.append(f"spread {sp['index']}: {sp['template']} -> {newt} (excluded photo removed)")
                    sp["template"] = newt
                else:
                    notes.append(f"spread {sp['index']}: cannot hold {want} photos after exclusion — marked for removal")
                    sp["_drop"] = True
        spreads = [sp for sp in spreads if not sp.pop("_drop", False)]

    # 2. descriptions become captions
    for sp in spreads:
        for slot in sp.get("slots", []):
            o = ov.get(slot["photo_id"])
            if o.description:
                slot["caption"] = o.description

    # 3. treatments ride along on the slot for the renderer
    for sp in spreads:
        for slot in sp.get("slots", []):
            o = ov.get(slot["photo_id"])
            slot["treatment"] = o.treatment

    # 4. size: small — never the feature of a composed page
    for sp in spreads:
        slots = sp.get("slots", [])
        if len(slots) > 1 and ov.get(slots[0]["photo_id"]).size == "small":
            for i in range(1, len(slots)):
                if ov.get(slots[i]["photo_id"]).size != "small":
                    slots[0], slots[i] = slots[i], slots[0]
                    notes.append(f"spread {sp['index']}: {slots[i]['photo_id']} moved out of the feature slot (marked small)")
                    break

    # 5. size: large — make it the feature of its composed page
    for sp in spreads:
        slots = sp.get("slots", [])
        for i in range(1, len(slots)):
            if ov.get(slots[i]["photo_id"]).size == "large" and ov.get(slots[0]["photo_id"]).size != "large":
                slots[0], slots[i] = slots[i], slots[0]
                notes.append(f"spread {sp['index']}: {slots[0]['photo_id']} promoted to the feature slot (marked large)")
                break

    # 6. size: full-page — structural. Pull it out and give it its own spread.
    wants_full = [pid for pid, o in ov.by_id.items() if o.size == "full-page" and not o.exclude]
    for pid in wants_full:
        host = next((sp for sp in spreads if any(s["photo_id"] == pid for s in sp.get("slots", []))), None)
        if host is None:
            continue
        if host["template"] == "full-bleed-single":
            continue  # already there
        slot = next(s for s in host["slots"] if s["photo_id"] == pid)
        rest = [s for s in host["slots"] if s["photo_id"] != pid]
        newt = _downgrade(host["template"], len(rest))
        pos = spreads.index(host)
        if newt is None and rest:
            notes.append(f"{pid}: cannot be made full-page without breaking spread {host['index']}")
            continue
        if rest:
            host["slots"] = rest
            host["template"] = newt
        else:
            spreads.pop(pos)
            pos -= 1
        # A treated photograph cannot bleed: a frame or a soft edge that runs
        # off the paper is trimmed away, so "full page" for those means the
        # full page inside its margins, not to the edges.
        treated = ov.get(pid).treatment != "none"
        spreads.insert(pos + 1, {
            "index": 0,
            "section_id": host.get("section_id"),
            "template": "single-centered" if treated else "full-bleed-single",
            "slots": [{**slot, "caption_position": "below" if treated else "bottom-left"}],
            "text": None,
            "options": {"side": "right"},
            "note": (f"{pid}: ganzseitig mit Rahmen auf Wunsch" if treated
                     else f"{pid}: randabfallend ganzseitig auf Wunsch"),
        })
        notes.append(f"{pid}: given its own full-page spread")

    for i, sp in enumerate(spreads, 1):
        sp["index"] = i
    layout["spreads"] = spreads
    layout.setdefault("meta", {})["spread_count"] = len(spreads)
    return notes


def stamp_locations(layout: dict, groups, photos_by_id: dict) -> int:
    """Put the place and date on each chapter opening — once per group.

    Guaranteed by the system rather than left to the model, so the album always
    says where it was, in the same form, in the same position.
    """
    by_photo = {pid: g for g in groups for pid in g.photo_ids}
    stamped = 0
    seen_groups = set()
    for sp in layout.get("spreads", []):
        if sp["template"] not in ("section-divider", "section-divider-plain", "title-page"):
            continue
        grp = None
        if sp.get("slots"):
            grp = by_photo.get(sp["slots"][0]["photo_id"])
        if grp is None:
            # a plain divider: use the group of the next photograph in the book
            idx = layout["spreads"].index(sp)
            for nxt in layout["spreads"][idx + 1:]:
                if nxt.get("slots"):
                    grp = by_photo.get(nxt["slots"][0]["photo_id"])
                    break
        if grp is None or grp.id in seen_groups:
            continue
        seen_groups.add(grp.id)
        line = " · ".join(x for x in (grp.place, grp.date_label) if x)
        if line:
            sp.setdefault("text", None)
            sp["text"] = dict(sp.get("text") or {})
            sp["text"]["eyebrow"] = line
            stamped += 1
    return stamped
