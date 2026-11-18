# ImpaginAI

A self-hosted AI photo album maker (formerly `photo_album_maker`). Point it at a folder of photographs and one
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

**`RUNNING.md` is the guide** — install, use, print, troubleshoot. Every
command in it was run from a clean clone.

The short version:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.lock.txt
playwright install chromium

python phase2/server.py
```

Open the address it prints. A welcome screen asks for your photographs (choose
a folder, or drop them in) and a name; the **Settings** tab then offers a model
preset and a place for your API key. To try it first with the 22 synthetic
photographs (real EXIF) the repository ships:

```bash
python phase2/server.py --album ./albums/demo --photos phase1/demo_photos
```

That works before you supply any of your own. Designing an album
needs a model; everything else — grouping, descriptions, sizes, preview
rendering — runs locally without one.

Any model will do. Anthropic is the default, and `--base-url` points at
anything speaking the OpenAI `/chat/completions` contract:

```bash
--base-url openrouter --model anthropic/claude-sonnet-4.5   # OPENROUTER_API_KEY
--base-url ollama     --model qwen2.5vl                     # local, no key
--base-url openai     --model gpt-4o                        # OPENAI_API_KEY
```

For your own pictures, point `--photos` at the folder and give it a title:

```bash
python phase2/server.py --album ./albums/sardinien --photos ~/Pictures/sardinien \
       --title "Sardinien 2026" --language de
```

## Command line

```bash
pip install -r requirements.txt
playwright install chromium
```

The editor and the CLI read and write the same files, so you can mix them.

**Phase 0** — render the calibration book:

```bash
cd phase0
python tools/make_placeholder_photos.py
python render.py check --layout examples/layout_test24.json --photos ./photos
python render.py proof --layout examples/layout_test24.json --photos ./photos --allow-fold
python render.py cover --layout examples/layout_test24.json --photos ./photos
```

**Phase 1** — the core loop (run from inside `phase1/`):

```bash
cd phase1
export ANTHROPIC_API_KEY=...
python album.py annotate --album ../albums/sardinien --photos ./demo_photos
python album.py plan     --album ../albums/sardinien --photos ./demo_photos --title "Sardinien 2026"
python album.py proof    --album ../albums/sardinien --photos ./demo_photos
python album.py revise   --album ../albums/sardinien --photos ./demo_photos --comment "Seite 4 ist überladen"

python album.py plan --album ../albums/x --photos ./demo_photos --dry-run   # no key needed
python tests/test_loop.py                                                    # no key, no network
python tests/test_e2e.py                                                     # CLI + editor vs a fake model
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

**A different model per job.** Annotation needs vision and runs once per
album; planning needs to follow a long brief and runs once; revision edits an
existing layout. `models.yaml` routes each role to its own endpoint — the usual
split is annotate on a local GPU for free, plan on the strongest model
affordable, since it is a single call that decides whether the book is good.

**The model is swappable.** The planner only ever needed a function from
(system prompt, messages) to text; `phase1/providers.py` implements that over
both the Anthropic Messages API and the OpenAI `/chat/completions` contract, so
OpenRouter, OpenAI, Together, Groq, vLLM, Ollama and llama.cpp all work.
Localhost endpoints are treated as keyless.

**Images are sent small, on purpose.** Vision cost scales with image
dimensions, not file size, so JPEG quality changes the upload and not the bill.
The annotate pass gets its own 512px copies rather than the 1200px proof
derivatives — a fifth of the tokens for work that is really contact-sheet
reading. `phase1/tools/bench_vision.py` finds the smallest size that still
annotates *your* photographs correctly.

**Vision is paid for once.** An `annotate` pass looks at each photograph a single
time and caches subject, people, scene, focal point and a strength rating to
`annotations.json`. Planning and every revision afterwards are text-only, so
schema retries cost nothing extra.

**Place names come from an offline database.** GPS resolves to a settlement
without a network or an API key, lakes, glaciers and peaks come from a
per-country list downloaded once and kept on the machine, and `places.yaml` lets the user override any
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

## Which model

Four presets ship in `presets/` — pass one and you are done:

```bash
python phase2/server.py --album ./albums/x --photos ~/Pictures/x \
       --models presets/hybrid.yaml
```

| Preset | annotate | plan | revise | 100-photo album |
|---|---|---|---|---|
| `cloud-best.yaml` | Sonnet 5.5 | **Opus 5.5** | Sonnet 5.5 | ~$0.46 |
| `cloud-cheap.yaml` | Haiku 5.5 | Haiku 5.5 | Haiku 5.5 | ~$0.02 |
| `hybrid.yaml` **(24 GB GPU: start here)** | **Qwen3-VL 32B, local** | Sonnet 5.5 | Sonnet 5.5 | ~$0.29 |
| `local.yaml` | Qwen3-VL 32B | Qwen3-VL 32B | Qwen3-VL 32B | free, offline |

Those totals assume three revisions. **Revisions are the largest line in every
paid setup**, because each one is a full planning-sized call and you will make
several — in `hybrid.yaml`, $0.22 of the $0.29. Setting `revise` to Haiku 5.5
takes that album to $0.08; the presets say where and what the risk is.

**All cloud** needs no GPU and only `OPENROUTER_API_KEY`. **Hybrid** is the
pick for a 24 GB card: annotation on your own GPU, because it dominates the
token cost and is the only call that sees your photographs, with planning in
the cloud because that single call decides whether the book is good. **Local**
keeps everything in the house at the cost of a weaker planner.

### Why those, and how to check them

A whole album costs roughly **$0.46 on the most expensive setup here** and
about **$0.02 on the cheapest cloud one** — against a print that costs
CHF 40-100. Cost is not the reason to pick one; capability is, and the capability that matters is
following a 9,000-character layout brief, which no published benchmark
measures.

So measure it directly:

```bash
python phase1/tools/bench_model.py --photos phase1/demo_photos \
       --base-url openrouter --compare model-a model-b model-c
```

It runs the real planning prompt and scores the output against the real
validator — schema, fold rule, page-density budget, chronology, grouping,
template variety, resolution, cover. The failure mode it exists to catch is a
model that returns valid JSON and ignores every rule, which a price table
cannot show you. `RUNNING.md` has the cost table and recommended stacks.

## Dependencies and security

`requirements.txt` holds floors (the oldest versions supported) and
`requirements.lock.txt` the exact versions CI tested; install from the lock.
`requirements-geo.txt` is the one optional dependency — offline place names
from GPS — kept separate because its build can fail and must not be able to
abort the whole install.
Dependabot moves the lock forward, and a weekly CI job installs the newest
versions the floors allow, so a release that breaks something is noticed. `pillow` and `pypdf` both
parse untrusted binary input — photographs and PDFs — and are the two that must
not lag; the first version of this file pinned them months behind and collected
64 advisories.

```bash
pip install pip-audit && pip-audit -r requirements.txt
```

CI runs that on every push and weekly, and Dependabot opens grouped update PRs.
`AGENTS.md` says how to regenerate the lock after changing a requirement.

## Licence

GNU AGPL-3.0 — see `LICENSE` — with a commercial licence available for use
that the AGPL does not allow: see `COMMERCIAL.md`. The lab specification files
in `phase0/labs/` are Apache-2.0. To contribute, read `CONTRIBUTING.md` and
`CLA.md`.

Landmark names come from [GeoNames](https://www.geonames.org) (CC BY 4.0) and,
when its servers answer, © OpenStreetMap contributors (ODbL).
