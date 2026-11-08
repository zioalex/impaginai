# photo_album_maker

A self-hosted AI photo album maker. Point it at a folder of photographs and one
caption each; it decides the sequencing, pacing, sectioning and page layout, and
emits a print-ready PDF for a photo lab plus a shareable web album from the same
source data.

It is not a drag-and-drop editor. The user never positions a box.

`PLAN.md` is the living specification — architecture, constraints, decisions log.
Read it before changing anything.

## State of the build

| Phase | What it is | State |
|---|---|---|
| **0** | Validate the print chain: hand-written layout JSON → Saal-Digital-spec PDFs | code complete, **awaiting the physical test print** |
| **1** | Core loop, CLI only: annotate → plan → proof → revise, with version history | built, covered by an offline test, not yet run against the real model |
| **2** | Web editor: photo grid, descriptions, per-photo size and treatment | **built** — `phase2/`, no build step, works offline |
| 3 | 300 DPI export path and web album | cover generator and print path already done in Phase 0 |
| 4 | Multi-language albums, map page, suggested captions | not started |

Phase 0 is deliberately a blocker. The test book has to be ordered, printed and
measured with a ruler before the template library is worth tuning — a layout
that scores well in JSON can still look flat on paper.

## Quick start

The editor is the easiest way in:

```bash
pip install -r requirements.txt && playwright install chromium
python phase1/tools/make_demo_photos.py        # or use your own
python phase2/server.py --album ./albums/demo --photos phase1/demo_photos
```

See `OFFLINE.md` for installing and running with no internet connection, and
for what the one online dependency actually is.

## Command line

```bash
pip install -r requirements.txt
playwright install chromium
```

**Phase 0** — render the calibration book:

```bash
cd phase0
python tools/make_placeholder_photos.py
python render.py check --layout examples/layout_test24.json --photos ./photos
python render.py proof --layout examples/layout_test24.json --photos ./photos --allow-fold
python render.py cover --layout examples/layout_test24.json --photos ./photos
```

**Phase 1** — the core loop:

```bash
cd phase1
export ANTHROPIC_API_KEY=...
python album.py annotate --album ./albums/sardinien --photos ./photos
python album.py plan     --album ./albums/sardinien --photos ./photos --title "Sardinien 2026"
python album.py proof    --album ./albums/sardinien --photos ./photos
python album.py revise   --album ./albums/sardinien --photos ./photos --comment "Seite 4 ist überladen"
python tests/test_loop.py   # offline, no API key, no network
```

## The three ideas worth knowing

**The model never emits CSS, coordinates or pixel values.** It picks a template
by name and a normalised focal point. Templates are *data* — functions from page
geometry to slot rectangles in millimetres — so the renderer and the validator
read the same numbers and cannot drift apart.

**Pages are composed, not just filled.** A page composition arranges several
photographs inside one page on a shared 6x6 modular grid, and a spread template
is a pair of those compositions. Without that layer every template places at
most one photograph per page, and the book reads as a slideshow with blank
facing pages. In a composed page the first slot is the feature: largest
rectangle, only caption.

**Vision is paid for once.** An `annotate` pass looks at each photograph a single
time and caches subject, people, scene, focal point and a strength rating to
`annotations.json`. Planning and every revision afterwards are text-only, so
schema retries cost nothing extra.

**Place names come from an offline database.** GPS resolves to a settlement
without a network or an API key, and `places.yaml` lets the user override any
of them — the beach below Baunei is "Cala Goloritzé" to anyone who went there.
The place and date are stamped on each chapter opening by the system, once per
group, rather than left to the model.

**The user outranks the planner.** `overrides.yaml` holds per-photo
instructions — description, how big, edge treatment, leave out — and they are
re-applied to every layout version, so a choice made once survives every later
revision without being re-argued with the model.

**Empty pages are counted, not noticed too late.** Chapter titles face the
chapter's opening photograph rather than spending a whole spread on three
words, and the validator reports how many pages carry no photograph — and how
many carry nothing at all — on every run.

**Every print constraint is executable.** Bleed, page-count increments, effective
DPI per slot, spine width, fold crossing — all enforced in `phase0/validate.py`,
not merely requested in a prompt. Lab specifications live in `phase0/labs/*.yaml`.

## Findings so far

- **Chromium shrink-to-fits the entire document** if any page's content extends
  past the page box — which the spread canvas does by design. It scales every
  page uniformly and silently while still reporting the correct PDF page size.
  Fix: `contain: layout paint size` on `.sheet`. `verify_pdf()` reads the drawn
  width of full-bleed images back out of the PDF content stream as a regression
  guard. Do not remove either.
- **No photograph crosses the fold.** Layflat binding removes the loss of image,
  but the crease still catches light and bends paper. Fold-spanning templates are
  withheld from the planner entirely; the validator rejects them. Governed by
  `allow_fold_crossing` in the lab config, so the calibration book can still
  print a panorama deliberately and let paper settle the argument.
- **Every constraint the validator can express must also appear in the prompt**,
  or every album pays for it with a retry.
- **Grouping has to be computed, not inferred.** Timestamps and GPS give the
  moment/place skeleton for free; the annotate pass supplies who is in the frame.

## Before ordering the test book

Everything marked `VERIFY` in `phase0/labs/saal.yaml` is a considered guess —
especially the cover constants, since the spine is computed from them. Download
Saal's template for your format and confirm them first. `phase0/README.md` lists
exactly what to measure when the book arrives.

## Licence

Apache 2.0 — see `LICENSE`.
