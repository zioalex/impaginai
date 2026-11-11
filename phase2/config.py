"""Every editor setting that is not a photograph, in one place.

Each option knows its default, what values it accepts, which group of the
Settings tab it belongs to, which command-line flag can pin it, and what a
change costs (its *effect*):

    instant    re-applied to the current design, no model call, free
    regroup    photographs regrouped, then re-applied, free
    redesign   takes effect on the next "Design again…" (a model call)
    relook     the vision pass looks at photographs again (costs money)

Precedence, highest first: a command-line flag (the option is then shown
locked in the editor) -> the album's settings.json -> the default here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

EFFECTS = ("instant", "regroup", "redesign", "relook")


@dataclass
class Option:
    key: str
    group: str
    label: str
    default: Any
    effect: str
    kind: str                       # choice | number | bool
    choices: dict[str, str] = field(default_factory=dict)   # value -> label
    min: float | None = None
    max: float | None = None
    step: float | None = None
    unit: str = ""
    help: str = ""                  # one line under the control
    info: str = ""                  # the ⓘ pop-up: the difference between choices
    flag: str | None = None         # the command-line flag that pins it

    def check(self, v):
        """The value, normalised; ValueError when it is not allowed."""
        if self.kind == "bool":
            if isinstance(v, bool):
                return v
            if str(v).lower() in ("true", "1", "yes", "on"):
                return True
            if str(v).lower() in ("false", "0", "no", "off"):
                return False
            raise ValueError(f"{self.key} must be true or false")
        if self.kind == "choice":
            if str(v) not in self.choices:
                raise ValueError(f"{self.key} must be one of {', '.join(self.choices)}")
            return str(v)
        try:
            n = float(v)
        except (TypeError, ValueError):
            raise ValueError(f"{self.key} must be a number") from None
        if self.min is not None and n < self.min or self.max is not None and n > self.max:
            raise ValueError(f"{self.key} must be between {self.min:g} and {self.max:g}")
        return int(n) if float(n).is_integer() and (self.step or 1) >= 1 else n


OPTIONS: list[Option] = [
    # --- Book -----------------------------------------------------------
    Option("lab", "book", "Print lab", "saal", "instant", "choice",
           help="Sets the page limits, the paper sizes and how sharp a photograph must be.",
           flag="--lab"),
    Option("length", "book", "Length", 0, "redesign", "number", min=0, max=200, step=2,
           unit="pages",
           help="0 lets the album decide. Otherwise the design aims for this many pages, "
                "within the lab's limits."),
    Option("density", "book", "Photos per page", "balanced", "redesign", "choice",
           choices={"airy": "Airy", "balanced": "Balanced", "dense": "Dense"},
           help="How many photographs share a page.",
           info="Airy — about 1–2 photographs per page. Single photographs get room "
                "of their own; more pages, bigger pictures.\n"
                "Balanced — about 2–3 per page. A mix of single photographs and "
                "composed pages; the default.\n"
                "Dense — about 3–4 per page. Photographs of one moment are grouped "
                "on composed pages; fewer pages, smaller pictures.\n"
                "With Balanced and Dense, three or four photographs of the same scene "
                "and moment are composed together on one page.\n"
                "Photographs you mark “Always with other photos” or “Full page” "
                "keep that, whatever is chosen here."),
    Option("selection", "book", "Which photos", "best", "redesign", "choice",
           choices={"all": "Every photo", "best": "Best of", "strongest": "Only the strongest"},
           help="How many of your photographs go into the book.",
           info="Every photo — nothing is left out (except photographs too small to "
                "print). The book is as long as it needs to be.\n"
                "Best of — the album leaves out weak frames and near-duplicates, the "
                "way a picture editor would; the default.\n"
                "Only the strongest — a short, tight book of the best frames of each "
                "place. Much is left out.\n"
                "A photograph you left out stays out, and one you mark “Always with "
                "other photos” is always in."),
    Option("captions", "book", "Captions", "personal", "redesign", "choice",
           choices={"personal": "Personal", "descriptive": "Descriptive",
                    "place": "Place and date only", "none": "None"},
           help="Your own captions are always printed as you wrote them.",
           info="Personal — written as “we”: what we did and saw there.\n"
                "Descriptive — what is in the photograph, plainly, with its name.\n"
                "Place and date only — no captions; each place is named once, on the "
                "first page of its photographs.\n"
                "None — no captions and no place names."),
    Option("short_book", "book", "When there are too few photos", "split", "instant", "choice",
           choices={"split": "Spread photos out first", "pad": "Add blank pages"},
           help="The lab prints at least its minimum number of pages.",
           info="Spread photos out first — composed pages are split into lighter "
                "ones until the book is long enough; blank pages only if that is "
                "not enough.\n"
                "Add blank pages — compositions stay as designed and the book is "
                "padded with blank pages at the end."),

    # --- Photos and grouping ---------------------------------------------
    Option("gap_hours", "grouping", "New moment after", 4.0, "regroup", "number",
           min=1, max=24, step=0.5, unit="hours",
           help="A longer pause between two photographs starts a new moment.",
           flag="--gap-hours"),
    Option("radius_m", "grouping", "New place after", 400, "regroup", "number",
           min=100, max=5000, step=50, unit="metres",
           help="Moving further than this starts a new place.",
           flag="--radius-m"),
    Option("vision_px", "grouping", "Vision detail", 512, "relook", "choice",
           choices={"512": "Standard", "768": "Detailed"},
           help="How large a copy the vision model is shown. Detailed costs about "
                "twice as much and applies to photographs looked at from now on.",
           flag="--vision-px"),
    Option("auto_rotate", "grouping", "Turn sideways photos upright", True, "relook", "bool",
           help="Photographs the vision pass sees lying on their side are turned. "
                "Your own turns are never changed."),

    # --- Places and privacy ------------------------------------------------
    Option("geocode", "places", "Place names", True, "regroup", "bool",
           help="Names towns from the photographs' GPS, offline.",
           flag="--no-geocode"),
    Option("online_places", "places", "Named landmarks", True, "regroup", "bool",
           help="Names lakes, glaciers and peaks. This sends photo coordinates to "
                "overpass-api.de (OpenStreetMap).",
           flag="--offline-places"),
]

BY_KEY = {o.key: o for o in OPTIONS}
GROUPS = {"book": "Book", "grouping": "Photos and grouping", "places": "Places and privacy"}


def labs(lab_dir: Path) -> dict[str, Path]:
    return {p.stem: p for p in sorted(lab_dir.glob("*.yaml"))}


def resolve(saved: dict, pinned: dict) -> dict:
    """Current values: pinned flags, then saved settings, then defaults."""
    out = {}
    for o in OPTIONS:
        if o.key in pinned:
            out[o.key] = pinned[o.key]
            continue
        v = saved.get(o.key, o.default)
        try:
            out[o.key] = o.check(v if o.kind != "choice" else str(v))
        except ValueError:
            out[o.key] = o.default
    return out


def describe(values: dict, pinned: dict, lab_choices: dict[str, str]) -> list[dict]:
    """The options as the editor shows them."""
    rows = []
    for o in OPTIONS:
        choices = lab_choices if o.key == "lab" else o.choices
        rows.append({
            "key": o.key, "group": o.group, "label": o.label, "value": values[o.key],
            "default": o.default, "effect": o.effect, "kind": o.kind,
            "also_now": o.key in ALSO_NOW,
            "choices": choices, "min": o.min, "max": o.max, "step": o.step, "unit": o.unit,
            "help": o.help, "info": o.info,
            "locked": o.key in pinned,
            "locked_by": (f"set by {o.flag} when the editor was started"
                          if o.key in pinned and o.flag else None),
        })
    return rows


# Redesign options that also change the current book at once, through the
# page repacker: every photo brought in, captions taken out, pages folded
# together or not.
ALSO_NOW = {"density", "selection", "captions"}


def needs(changed: list[str]) -> list[str]:
    """What the editor must do after these options changed."""
    eff = {BY_KEY[k].effect for k in changed}
    out = []
    if eff & {"instant", "regroup"} or ALSO_NOW & set(changed):
        out.append("rerender")
    if "redesign" in eff:
        out.append("redesign")
    if "relook" in eff:
        out.append("relook")
    return out
