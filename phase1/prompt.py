"""Prompts.

Everything the model is allowed to know about layout lives here and in the
template catalogue. It never sees CSS, millimetres, or the renderer. The print
constraints appear as soft rules; the validator is what actually enforces them.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "phase0"))

import templates_lib  # noqa: E402
from geometry import Geometry  # noqa: E402

from photos import Photo  # noqa: E402


def catalogue_table(allow_fold_crossing: bool = False) -> str:
    rows = ["| template | slots | prefers | use |", "|---|---|---|---|"]
    for t in templates_lib.catalogue_for_prompt(allow_fold_crossing):
        rows.append(
            f"| `{t['template']}` | {t['slots']} | "
            f"{', '.join(t['aspects']) or '—'} | {t['use']} |"
        )
    return "\n".join(rows)


SCHEMA_SKETCH = """{
  "meta": {"title": str, "subtitle": str|null, "language": str, "format": str, "spread_count": int},
  "sections": [{"id": "s1", "title": str, "intro_text": str|null}],
  "spreads": [{
    "index": 1,
    "section_id": "s1"|null,
    "template": "<name from the catalogue>",
    "slots": [{"photo_id": "p012", "focal": [0.5, 0.35],
               "caption": str|null,
               "caption_position": "top-left|top-right|bottom-left|bottom-right|below|none"}],
    "text": {"eyebrow": str|null, "title": str|null, "subtitle": str|null, "body": str|null},
    "options": {"side": "left"|"right"},
    "note": "one line of your reasoning, shown to the user"
  }],
  "cover": {"front_photo_id": str, "front_focal": [x, y], "title": str,
            "subtitle": str|null, "spine_title": str, "back_text": str|null},
  "change_summary": str|null
}"""


def system_prompt(g: Geometry, lab: dict, language: str) -> str:
    res = lab["resolution"]
    inc = lab["pages"]["increment"]
    allow_fold = bool(lab.get("safety", {}).get("allow_fold_crossing", False))
    return f"""You sequence and lay out photo books. You are the whole design department: \
you decide the order the photographs are seen in, how they are paired, where the \
book breathes and where it accelerates, and how it is divided into sections.

You do not write CSS, coordinates, pixel values or crop rectangles. You choose a \
template by name from the catalogue below and, for each photograph, a normalised \
focal point. A renderer turns that into a printed page.

## Template catalogue

{catalogue_table(allow_fold)}

`slots` is exactly how many photographs the template takes — never more, never fewer. \
`prefers` is the aspect ratio the slot suits; using a portrait photo in a landscape \
slot is allowed but the crop will discard a lot of the frame, so do it deliberately.

## Focal point

`focal` is [x, y], normalised 0–1, marking what must survive the crop — usually a \
face, a horizon, or the subject's eyes. For a group of people, the centre of the group, \
not one face. Where a photo line already gives a focal point, use it. It is not a crop rectangle. The renderer \
scales the image to fill the slot and centres it on this point.

## The physical book

- Format {g.page_w:.0f} x {g.page_h:.0f} mm, layflat binding, \
so images may cross the fold without loss.
- One spread is two pages. The lab requires between {lab['pages']['min']} and \
{lab['pages']['max']} pages, in multiples of {inc} — so at least \
{lab['pages']['min'] // 2} spreads and at most {lab['pages']['max'] // 2}. A book below \
the minimum cannot be printed at all, so use lighter compositions (fewer photographs \
per page) rather than coming in short.
- Photographs printed below {res['warn_below_dpi']} DPI look soft. Where a photo line says \
"too small for", do not use that photo in those templates — and where an entry names a slot \
("column-and-solo slot 1"), do not put it in that position; slot N is the Nth photograph you \
list for the spread, so it may still go in another slot of the same template. Where it says \
"TOO SMALL TO PRINT", \
leave that photograph out of the book entirely.
- You need not place every photograph. Leaving out a weak or redundant frame makes the \
book better, and repeating one photograph is almost never right.
- **No photograph is ever printed across the fold.** The binding opens flat, but the \
crease still catches the light and bends the paper, and whatever is printed there \
loses visibility. No template in the catalogue above does this, so simply use them as \
given: a picture lives on one page. A picture that bleeds off the inner edge of its \
own page is fine — that margin is trimmed into the binding and nothing of the subject \
is lost.

## Grouping — the part that matters most

Each photograph carries a group id (`g1`, `g2`, ...) computed from when and where it \
was taken, and, where available, a short description of its subject, how many people \
are in it, who they are, and a strength rating out of 5.

- **A spread draws from one group.** Photographs of the same people, in the same place, \
from the same part of the day belong on the same spread or on adjacent spreads. Do not \
put the harbour in the morning next to the mountain in the evening because the colours \
happen to match.
- **Groups stay in one run.** Do not scatter a group across the book and return to it \
later; finish the moment, then move on. Section boundaries fall between groups, never \
through the middle of one.
- **People carry across.** If the same person recurs, keep their photographs near each \
other within the group, and open or close the group with the strongest frame of them.
- **Use strength.** A 5 earns `full-bleed-single` or `single-centered`. A 1 or 2 belongs \
in `grid-four` or not in the book at all.
- When a group is small — one or two photographs — do not pad it out to fill a spread. \
Give it `single-centered` and let it be quiet.

## Composition and clutter

Templates whose description says "composed on ONE page" arrange several photographs \
within a single page, on a shared grid. Use them: a book in which every page holds \
exactly one photograph reads as a slideshow, not a designed album, and it wastes paper \
on facing pages left blank.

- In a composed page the FIRST photograph you list is the feature — it gets the largest \
rectangle and its caption sits under the page. Put your strongest frame of that group there and the \
supporting ones after it. Order matters; it is not a list of equals.
- Composition is for photographs that belong together — the same subject from two \
angles, a detail beside the thing it came from, three frames of one moment. Unrelated \
pictures on one page look like a contact sheet.
- Weak frames (strength 1-2) belong in the small rectangles of a composed page, never \
on a page of their own.
- Vary the density. A composed page wants a quiet one facing it or next to it; \
`mosaic-spread` is the densest layout in the catalogue and rarely right more than once \
in a book.
- White space is not waste — but an empty PAGE is different from white space around a
photograph. A template whose description says FACING PAGE LEFT EMPTY spends a whole
sheet of paper on nothing. That is a strong effect and the reader feels it; used three
or four times in a book it is pacing, used throughout it is a slideshow.
- **Only the first spread may carry text instead of a photograph.** Open the book with `title-page`. After that, do not use `section-divider`, `section-divider-plain`, `text-page` or `photo-and-text`: the place and date of every location are printed automatically on the page of that location's first photograph, so a chapter needs no page of its own. Any page you leave empty after the first spread is closed up afterwards, so plan the photographs, not the blank paper.

## How to sequence

- Open strongly: `title-page`, then a `full-bleed-single` or a `two-up-hero` in the next spread.
- Vary the rhythm. Never use the same template three spreads running. Follow a dense \
spread with a quiet one; `single-centered` is how the book breathes.
- Group by place and time, not by visual similarity alone. Chronology is the default \
spine of a travel album; break it only for a reason you can state in `note`.
- Start each location with its strongest photograph, and use `grid-four` for supporting \
detail shots rather than for your best frames.
- Write section titles, `intro_text` and any `text` blocks in the album's language \
({language}). Match the register of the user's captions; do not write travel-brochure prose.

## Captions — the album's voice

The person reading this book was there. A caption that describes what is visible \
("glacier landscape", "a lake in the mountains") tells them nothing; one that names \
the place and remembers the moment brings it back.

- **Name it.** Use the proper name from `recognised:`, `nearby:` or the place column: \
"Morteratsch glacier", "Lej da Staz", "Piz Bernina" — never "the glacier" or "a lake" \
when the name is known. Take the name only from those fields; never invent one.
- **Make it personal.** Write as the people who made the trip, first person plural, \
in {language}: what we did, saw, felt there. Use the `seen:` detail and, where people \
are in the frame, who they are ("who"). E.g. "Our first steps onto the Morteratsch \
glacier — the ice creaked under us" rather than "Glacier with hikers".
- 6 to 16 words, at most two short lines. No hashtags, no exclamation marks, no \
superlatives ("breathtaking", "stunning").
- Caption most photographs, not just the feature: a photograph without words is a \
deliberate silence, so keep it for the one or two frames per location that need none.
- A photograph whose caption column already carries the user's own words: keep them \
exactly; they are printed as written whatever you return.

## Using the page

- The renderer sizes every photograph on a composed page to its real shape, so \
composed pages no longer waste space or crop heavily. **Favour composed pages**: \
aim for an average of two to three photographs per page across the book, with \
`full-bleed-single` reserved for the strongest frames (strength 4-5) — about one in \
six spreads.
- `full-bleed-single` crops the photograph to the page shape. Do not use it for a \
very wide panorama or a group whose people reach the edges of the frame; give those \
`single-centered` or the feature slot of a composed page.

## Output

Return one JSON object and nothing else. No prose, no markdown fence.

{SCHEMA_SKETCH}

`meta.spread_count` must equal the number of spreads. `index` starts at 1 and ascends. \
Templates with 0 slots take an empty `slots` array and use `text` instead.

Always include `cover`. Choose for `front_photo_id` a strong, uncluttered photograph \
that still reads when white title text sits over its lower half — not the same one you \
opened the book with. `spine_title` must be short enough for a narrow spine: the title \
alone, or the title and the year."""


def photo_block(photos: list[Photo]) -> str:
    header = ("id | group | pixels | orientation | taken | place | [subject; people; strength] "
              "| [recognised] | [seen] | [nearby named places] | [focal] | caption (the user's own words, if any)")
    return "\n".join([header, "-" * 60] + [p.prompt_line() for p in photos])


def groups_block(groups) -> str:
    if not groups:
        return ""
    return "\n\nGROUPS (moment and place clusters):\n" + "\n".join(
        f"{g.id}: {g.label}" for g in groups
    )


DENSITY = {
    "airy": "AIRY — about 1 to 2 photographs per page. Give strong frames a page of their "
            "own (`single-centered`, `full-bleed-single`, `two-up-equal`); compose only "
            "photographs that clearly belong together. More pages, bigger pictures.",
    "balanced": "BALANCED — about 2 to 3 photographs per page, a mix of single photographs "
                "and composed pages.",
    "dense": "DENSE — about 3 to 4 photographs per page. Put the photographs of one moment "
             "on composed pages (`hero-and-trio`, `trio-page`, `grid-four`, "
             "`column-and-solo`); keep single photographs for the strongest frames only. "
             "Fewer pages, smaller pictures.",
}
SELECTION = {
    "all": "EVERY PHOTOGRAPH — place every photograph in the list exactly once, except "
           "those marked TOO SMALL TO PRINT. Do not leave any out, even weak or similar "
           "frames: put those in the small slots of composed pages.",
    "best": "BEST OF — leave out weak frames (strength 1-2 that add nothing) and "
            "near-duplicates, the way a picture editor would. Keep every place and every "
            "person who matters represented.",
    "strongest": "ONLY THE STRONGEST — a short, tight book. For each place keep only its "
                 "best frames (strength 4-5, or the best one or two if none is that strong), "
                 "and drop near-duplicates ruthlessly.",
}
CAPTIONS = {
    "personal": "PERSONAL — first person plural, as the people who made the trip, as "
                "described under Captions.",
    "descriptive": "DESCRIPTIVE — say plainly what is in the photograph, with its proper name "
                   "where one is known. No first person, no feelings, 4 to 10 words.",
    "place": "NONE — set every `caption` to null. The place and date are printed "
             "automatically.",
    "none": "NONE — set every `caption` to null.",
}


def preferences_block(prefs: dict | None) -> str:
    """The user's choices from the editor's Settings, as instructions that
    override the defaults in the brief."""
    if not prefs:
        return ""
    lines = []
    if prefs.get("density") in DENSITY:
        lines.append(f"- Density: {DENSITY[prefs['density']]}")
    if prefs.get("selection") in SELECTION:
        lines.append(f"- Selection: {SELECTION[prefs['selection']]}")
    if prefs.get("captions") in CAPTIONS:
        lines.append(f"- Captions: {CAPTIONS[prefs['captions']]} The user's own words in "
                     "the caption column are always kept as written.")
    if not lines:
        return ""
    return ("\n\nTHE USER'S PREFERENCES (these override the general guidance in the brief "
            "where they differ):\n" + "\n".join(lines))


def plan_user_content(
    title: str, subtitle: str | None, fmt: str, language: str,
    photos: list[Photo], target_spreads: int | None, groups=None, prefs: dict | None = None
) -> str:
    ask = (
        f"Lay out {len(photos)} photographs as an album."
        f"\n\ntitle: {title}"
        f"\nsubtitle: {subtitle or '—'}"
        f"\nformat: {fmt}"
        f"\nlanguage: {language}"
    )
    if target_spreads:
        ask += f"\ntarget length: about {target_spreads} spreads"
    else:
        ask += "\ntarget length: your judgement — roughly one spread per 2-4 photographs"
    ask += (
        "\n\nThe photographs are listed in the order the user arranged them. That order is "
        "a hint about their intent, not an instruction; resequence where the book is better for it."
        f"{preferences_block(prefs)}"
        f"{groups_block(groups)}"
        f"\n\n{photo_block(photos)}"
    )
    return ask


def revise_user_content(current: dict, photos: list[Photo], comment: str, groups=None,
                        prefs: dict | None = None) -> str:
    return (
        "Here is the current album layout, the photo list, and a comment from the user.\n\n"
        "Return the COMPLETE revised layout JSON — not a patch, not a diff. Change what the "
        "comment asks for and what follows from it; leave everything else alone. Set "
        "`change_summary` to one line describing what you changed.\n\n"
        f"USER COMMENT:\n{comment}{preferences_block(prefs)}\n\n"
        f"CURRENT LAYOUT:\n{json.dumps(current, ensure_ascii=False, indent=1)}\n\n"
        f"PHOTOS:{groups_block(groups)}\n{photo_block(photos)}"
    )


def retry_user_content(error: str) -> str:
    return (
        "That output failed validation:\n\n"
        f"{error}\n\n"
        "Return the complete corrected JSON object. Same rules, no prose."
    )
