# AI Photo Album Builder — PLAN.md

Living spec. Paste back into the project instructions whenever it changes.
Last updated: Phase 0 build session.

## What this is

A self-hosted web app that turns a folder of photos plus one caption per photo
into a designed album. The AI decides sequencing, pacing, page layout and
section structure. The user reviews a low-resolution proof PDF, leaves a comment
in plain language ("page 4 is crowded", "move the church photos earlier"), and
the AI produces a revised version. When the user is happy, the app emits a
print-ready PDF for a photo lab and a shareable web album from the same source
data.

Not a drag-and-drop editor. The entire point is that the user never positions a box.

## Constraints (fixed — do not design around alternatives)

- **30–100 photos per album.** Fits in one LLM context with captions. Plan the
  whole album in a single call. Do not chunk, do not plan page-by-page.
- **Two outputs from one source:** print PDF and web album. The layout JSON is
  the single source of truth; both renderers consume it.
- **Target print lab: Saal Digital**, Professional Line, layflat binding.
  WhiteWall and Bookfactory are secondary — lab specs live in config
  (`phase0/labs/saal.yaml`), never hardcoded.
- **Colour space is sRGB, never CMYK.** Headless Chromium outputs RGB natively.
  No CMYK conversion step. Derivatives are transformed to sRGB on generation and
  carry an embedded sRGB profile.
- **Self-hosted.** Docker behind Traefik on a Proxmox homelab. No managed cloud
  beyond the LLM API.

## Stack

- Backend: FastAPI (Python 3.12), SQLAlchemy, Alembic
- DB: PostgreSQL
- Frontend: Next.js (App Router), TypeScript, Tailwind
- Renderer: Playwright + Chromium, HTML/CSS → PDF
- Queue: Redis + RQ
- Storage: filesystem volume (NAS-backed)
- LLM: Anthropic API, Claude Sonnet for planning and revision

## Architecture

```
Next.js ──HTTP──> FastAPI ──> Postgres
                    │
                    ├──> RQ worker ──> Anthropic API      (plan / revise)
                    │                └─> Playwright        (render)
                    └──> /data/albums/{id}/
                            originals/    as uploaded
                            proof/        1200px longest edge, JPEG q80
                            print/        300 DPI at slot size, JPEG q95
                            renders/      proof-v3.pdf, print-v7.pdf
```

## The central design decision

**The LLM selects from a fixed template library. It never emits CSS,
coordinates, or pixel values.**

Refined in Phase 0: templates are not HTML partials, they are **data**. Each
template is a function from page geometry to a list of slot rectangles in
millimetres (`phase0/templates_lib.py`). The renderer positions boxes from those
numbers and the validator computes effective DPI and fold safety from the same
numbers, so the two cannot drift apart. The model still only ever emits a
template name, a photo id, a normalised focal point and a caption.

| Template | Slots | Use |
|---|---|---|
| `full-bleed-single` | 1 | one photograph to the edges, facing page empty |
| `single-centered` | 1 | one photograph with white space, facing page empty |
| `duo-page` | 2 | two composed on ONE page (large above wide) |
| `trio-page` | 3 | three composed on ONE page (wide one over a pair) |
| `grid-four` | 4 | four in a square grid on ONE page |
| `two-up-equal` | 2 | one photograph on each page, same weight |
| `two-up-hero` | 2 | a bleeding photograph facing a smaller one |
| `hero-and-pair` | 3 | bleeding photograph facing two stacked |
| `hero-and-trio` | 4 | bleeding photograph facing three composed |
| `solo-and-pair` | 3 | framed single facing a stepped pair |
| `column-and-solo` | 4 | tall photograph beside two stacked, facing a single |
| `mosaic-spread` | 6 | three composed on each page — densest, use sparingly |
| `photo-and-text` | 1 | half image, half narrative block |
| `section-divider` | 0 | title page between chapters |
| `text-page` | 0 | intro or closing text |
| `panorama-spread` | 1 | WITHDRAWN — see the fold rule below |

## Page composition

The first library had no composition layer: every template placed at most one
photograph per page, so a "spread template" was only a pair of single-photo
pages and the book read as a slideshow with blank facing pages.

`phase0/compose.py` supplies the missing level. A **page composition** arranges
one or more photographs inside a single page on a shared 6x6 modular grid with
a caption band reserved at the foot. A **spread template is a pair of page
compositions**, which is what makes real pairings possible — a bleeding hero
facing a stack of three, a quiet single facing a mosaic — while keeping the
no-fold rule structural rather than remembered: a composition is built inside
one page's content area and cannot reach past it.

Slots snap to whole grid cells, so images align across different compositions
and across the book. In a composed page, slot 0 is the **feature**: the largest
rectangle and the only caption. Captioning every photograph in a composition
turns the page into a catalogue entry.

Plus two calibration templates (`test-chart`, `gutter-target`) that exist for
Phase 0 only and are hidden from the planner via `planner_visible=False`.
`templates_lib.catalogue_for_prompt()` returns exactly what the model may see.

## The fold rule

**No photograph is printed across the fold.** Layflat binding removes the loss of
image, but the crease still catches light and bends the paper, and anything
printed there loses visibility. This overrides the original brief's treatment of
panoramas as a feature.

Consequences, all implemented:

- `panorama-spread` is withheld from the planner catalogue entirely — offering a
  template and then rejecting its use only buys a retry.
- `three-up-strip` was replaced outright by the page-composition layer: three
  photographs now compose within a single page (`trio-page`, or the right-hand
  page of `hero-and-trio`), so the question of what sits in the fold does not
  arise.
- The validator distinguishes *bleeding past the fold* from *spanning it*. A
  single-page full-bleed image runs one bleed past the crease so a mis-trim
  cannot open a white gap; that margin is trimmed into the binding and none of
  the subject is lost. Spanning means real content lands on both sides, and that
  is an error.
- Governed by `safety.allow_fold_crossing` in the lab config, so this is a
  project decision rather than a hardcoded belief. The Phase 0 calibration book
  renders with `--allow-fold` on purpose: it carries a panorama and a gutter
  target so the crease can be *measured* rather than assumed. If the printed
  result is better than expected, the switch is where to change our mind.

## What the AI actually decides

Three calls, in this order:

1. **Annotate** (vision, once per album, cached to `annotations.json`). Each
   photograph gets: subject in a few words, how many people are visible, a
   stable label for who they are, scene type, a suggested focal point, and a
   strength rating out of 5. This is the only call that looks at pixels.
2. **Plan** (text). Sequencing, grouping into sections, pacing, template choice
   per spread, which photographs to leave out, focal points, caption placement,
   and section titles and connective text in the album's language.
3. **Revise** (text). Complete revised layout from a plain-language comment.

Because annotation is cached, planning and every revision are text-only. Retries
cost nothing extra, and the expensive pass is never repeated.

**Grouping** is prepared without the model: `cluster.py` turns timestamps and GPS
into moment/place groups (a gap over 4 hours, or 400m of movement, starts a new
one). The planner is given the group ids and told that a spread draws from one
group, that groups stay in one run, and that section boundaries fall between
groups rather than through them. The validator warns when a spread mixes groups.

## Page geometry — the spread canvas

Templates are authored on one **spread canvas**, 2 pages plus bleed wide. At
export the canvas is sliced into two single media pages by shifting it
`-page_w` for the right-hand page.

```
canvas_w = 2 * page_w + 2 * bleed          media_w = page_w + 2 * bleed
left page  = canvas x [0, page_w + 2*bleed]        offset 0
right page = canvas x [page_w, canvas_w]           offset -page_w
```

- A panorama is not a special case; it is a slot covering the whole canvas.
- Each page's inner bleed duplicates `bleed` mm of the facing page's trim. The
  two exported pages therefore overlap by `2 * bleed` at the seam. Correct and
  necessary: the trim consumes it, and it prevents a white sliver at the fold.
- The same canvas drives the web album in Phase 3 — different stylesheet, no
  slicing.

## Layout JSON schema

Pydantic (`phase0/schema.py`). Reject and retry on schema failure — never
hand-repair model output; a repaired layout hides a broken prompt.

Shape as in the original brief, plus three additions made in Phase 0:
`spread.text` (eyebrow/title/subtitle/body for divider and text pages),
`spread.options` (e.g. `{"side": "left"}` for one-page templates), and a
top-level `cover` object.

**Cropping:** normalised focal point only, mapped to `object-fit: cover` +
`object-position`. Never let the model emit pixel crop rectangles.

## Print constraints (encoded as validation, `phase0/validate.py`)

- **Bleed** per page from the lab config for the chosen format.
  **No crop or register marks in the export** — Saal rejects these.
- **Page count** must match the lab's allowed increments; checked before export.
- **Cover is a separate PDF.** Spine is computed from page count, leaf thickness
  and board thickness — never guessed.
- **Effective resolution:** DPI computed per slot at final print size, accounting
  for cover-crop. Warn below 200, block below 150.
- **Gutter safety:** focal points within `fold_band_mm` of the fold on a
  fold-crossing slot raise a warning.
- **Partial bleed:** any slot edge landing inside the bleed but not on the media
  edge is an error — it trims to a white sliver.

## LLM calls (Phase 1)

**Plan.** System prompt: template catalogue with slot counts and aspect
preferences, print constraints, output schema. User content: title, format, then
one line per photo — `id, width, height, orientation, taken_at, place, caption`.
Thumbnails as image blocks behind a config flag: on for the initial plan, off
for revisions.

**Revise.** Input: current layout JSON, full photo list, user comment. Output:
complete revised JSON plus a one-line `change_summary`. Store as a new
`layout_version` row with `parent_version_id`.

## Data model

```
album           id, title, subtitle, language, format, status, created_at
photo           id, album_id, filename, width, height, taken_at, gps_lat, gps_lon,
                place_name, caption, upload_order
layout_version  id, album_id, version_no, parent_version_id, json, user_comment,
                change_summary, created_at
render          id, layout_version_id, kind(proof|print|web), path, status, error
```

## Frontend flow

Four screens: Upload, Captions, Proof (spread viewer + comment box + version
history), Export. Resist adding more. Every long operation is a job; poll
`/jobs/{id}` and show real progress.

## Build phases

**Phase 0 — validate the print chain. CODE COMPLETE, AWAITING PHYSICAL TEST.**
See `phase0/README.md`. Order the test book, measure it, write the numbers back
into `labs/saal.yaml`. Do not start Phase 1 until that is done.

**Phase 1 — core loop, CLI only. BUILT, UNTESTED AGAINST THE REAL MODEL.**
`phase1/album.py` — `plan`, `proof`, `revise`, `history`, `rollback`. Prompt
construction, one-call planning, schema-failure retry, append-only version store
and a cost line are all in place and covered by an offline test with a scripted
model (`phase1/tests/test_loop.py`). What remains is the part that needs real
output: iterating the template library and the prompt against actual albums, and
that should wait for the printed test book.

**Phase 2 — web frontend.** The four screens, FastAPI + Postgres + RQ behind them.

**Phase 3 — print export and web album.** 300 DPI path, resolution validation,
shareable web album route. (Cover generation moved forward into Phase 0.)

**Phase 4 — polish.** Multi-language albums (DE/IT/EN), optional map page from
GPS, vision-suggested captions the user edits.

## Phase 1 notes

- Every constraint the validator can express must also appear in the prompt.
  First run produced a valid 16-page book that no lab will print, because the
  prompt gave the page increment but not the minimum.
- Each photo line carries the templates it is too small for, computed from the
  real slot rectangles. Photos too small for everything are marked
  "TOO SMALL TO PRINT — leave it out".
- The model is told explicitly that it need not place every photograph.
- Version store mirrors the `layout_version` table exactly, so Phase 2 moves it
  to Postgres without changing anything above it. Rollback appends a new version
  rather than rewinding — history stays append-only.
- Open template-library item: `two-up-equal` slots are near-square, so 4:5
  portraits crop hard. Do not fix from screen impressions; wait for paper.
- Anti-clutter rules are in the prompt as structure, not adjectives: the feature
  slot is first and carries the only caption, composed pages are for photographs
  that belong together, weak frames (1-2/5) go in the small rectangles rather
  than on a page of their own, and `mosaic-spread` is rarely right twice.
- Adding composition shortened the demo album from 36 pages to 26 for the same
  21 photographs, by ending the one-photo-per-page default and its blank facing
  pages.

## Decisions log

- **Test print is 21×21, not 30×30.** Same chain at roughly half the cost. 30×30
  stays in the lab config as the production format.
- **Photo-print cover, so the spine calculator is built in Phase 0** rather than
  Phase 3. Front panel carries the photo, back panel and spine are solid ink —
  the hard boundary at the front hinge is itself the spine-width measurement.
- **The test book carries two calibration spreads**: a trim ruler with tone,
  colour and type targets, and a gutter target with density bands crossing the
  fold. Real photos fill the other pages.
- **Section dividers are solid ink with white type.** Doubles as a black density
  test on Fuji Crystal Archive.
- **Fonts:** Charter for display and captions, Carlito for utility text. Must be
  vendored as WOFF2 before production — a font substitution on a Docker rebuild
  silently reflows every caption.

## Known risks

- **Aesthetic quality is the real unknown.** Budget two or three rounds of
  template-library revision after seeing printed output.
- **Revision cost.** Keep revisions text-only; the model already made the visual
  decisions in the plan call.
- **Chromium print scaling — found and fixed in Phase 0.** Chromium
  shrink-to-fits the *entire document* when any page's content extends past the
  page box, which the spread canvas does by design. It scales every page
  uniformly and silently while still reporting the correct PDF page size. Fix:
  `contain: layout paint size` on `.sheet`. Guard: `verify_pdf()` reads the drawn
  width of every full-bleed image back out of the PDF content stream and fails
  the render if it is under the media box. Do not remove either.
- **Unverified lab constants.** Everything marked VERIFY in `labs/saal.yaml`,
  especially the cover geometry, is a considered guess until measured.
