# Project review — 11 November 2026

How this project can fail, what to fix, and how to make it open and still earn
money. Based on reading the code, running the test suites, reproducing the
security findings against a local server, and two audit agents borrowed from
`getinspiredbythebible` (`risk-auditor`, `failure-forecaster`).

## Verdict

The engine is the strong part: the model picks templates by name, print rules
are executable, user choices outrank the model, vision is paid for once. That
design is better than most auto-layout tools.

The project is not yet a product. Nobody has held a printed book, the planner
has not been judged on real albums, the README shows no picture of the result,
and the server cannot safely be put on a network. Those four things, in that
order, decide whether it succeeds.

## What was checked

| Check | Result |
|---|---|
| `test_pages.py`, `test_loop.py`, `test_e2e.py` | all pass (Python 3.13, Playwright pinned to 1.63.0 to match the installed browser) |
| Fresh `pip install -r requirements.txt` | installs Playwright 1.65.0; tests then fail until the matching Chromium is downloaded |
| API key leak via `/api/models/list` | **reproduced** with a fake key |
| Album created outside the folder jail | **reproduced** |
| Cross-site `text/plain` POST | rejected by FastAPI (good) |
| Real model, real print, visual quality of a designed album | **not checked** — needs a key and a lab order |

## How it can fail

### 1. Product failures (most likely to kill it)

1. **The book has never been printed.** `phase0/labs/saal.yaml` carries nine
   `VERIFY` constants; the spine width is computed from guesses. The first
   stranger who orders a CHF 80 book with a wrong spine ends the project's
   reputation. Order the calibration book before anything else.
2. **The planner is unproven on real albums.** README says Phase 1 is "not yet
   run against the real model". The whole value is "the AI designs a book I
   would not have to fix". That needs ten real albums and honest scoring.
3. **Nobody can see what it makes.** No screenshot, no sample PDF, no demo
   site. The README opens with a phase table. A visitor decides in ten seconds.
4. **Install is for developers only.** git clone, venv, pip, a 150 MB browser
   download, an API key. The people with "album debt" (thousands of photos, no
   book) cannot do this. No Docker image, no `pip install`, no installer.
5. **One lab.** Saal only. Each added lab (CEWE, WhiteWall, Bookfactory, ifolor,
   Blurb) multiplies the audience and is a natural community contribution.
6. **Incumbents.** CEWE, Mixbook, Popsa, Journi and Google Photos all have free
   auto-layout. "AI makes a photo book" is not a differentiator. These are:
   caption-driven narrative, revision in plain language, private/local, lab-
   independent print-ready PDF, typographic quality. Say so explicitly.

### 2. Security (confirmed)

> **Status, 11 November:** S1–S4, the non-atomic writes and the job-gate race
> below were fixed the same day, with regression tests. What remains open in
> this area: no login for `--host 0.0.0.0`, and edits made while a design runs.

| # | Severity | Finding | Where |
|---|---|---|---|
| S1 | Critical | **Any web page the user visits can steal their API key.** `GET /api/models/list?base_url=<attacker>` makes the server send `Authorization: Bearer <key>` to that URL. An `<img>` tag is enough. Reproduced: a listener on another port received the `OPENAI_API_KEY` value. Environment keys go to any URL; stored keys are matched by substring (`…/api.openai.com` in a path is enough). | `phase2/server.py:1170`, `phase1/providers.py:170-180`, `:1245-1249`, `phase1/keystore.py:47-57` |
| S2 | High | No authentication, no `Host`/`Origin` check. DNS rebinding reaches every route. `--host 0.0.0.0 --allow-remote-settings` exposes keys, folder browser and uploads to the whole network. | `phase2/server.py:79-86`, `:1840` |
| S3 | High | `POST /api/albums` checks `photos` against the folder jail but not `album`; it creates directories and writes files anywhere the user can write. Reproduced. | `phase2/server.py:830-834` |
| S4 | Medium | Upload is fully read into memory before the size check; `Image.MAX_IMAGE_PIXELS = None` disables Pillow's decompression-bomb guard for the whole process. | `phase2/server.py:896-900`, `phase0/validate.py:21` |

Fixes: bind each key to the exact host it was saved for and never send an
environment key to an unknown host; a per-process token on every mutating
route plus a `Host` allow-list; run `album` through `_browse_ok`; stream
uploads with a byte counter and set a pixel limit (≈250 MP).

### 3. Reliability and data loss

- **Non-atomic writes of the user's work.** `overrides.yaml`, `versions.json`,
  `settings.json` are plain `write_text`. A crash mid-write empties the file;
  an empty `overrides.yaml` silently drops every choice the user made, a broken
  `versions.json` makes the album unopenable (`phase1/overrides.py:136`,
  `phase1/versions.py:79-97`). `keystore.py` already does it right — reuse that.
- **Edits race a running design.** The job gate is check-then-act without the
  lock, editing routes do not check for a running job, and the renderer is
  driven by swapping `sys.argv` (`phase2/server.py:211`, `:463`, `:1293-1378`).
- **File names break the render.** `<img src>` is not URL-encoded; `Sommer #3.jpg`
  or `100%.jpg` stalls 30 s then fails (`phase0/render.py:332`, `:453`).
- **Wide-gamut photos shift colour silently.** `to_srgb` swallows ICC errors;
  iPhone photos are Display P3 (`phase0/render.py:69-82`).
- **Fonts are whatever the machine has.** A different machine reflows every
  caption (`phase0/assets/style.css:14`). Vendor OFL fonts (Source Serif 4,
  Source Sans 3). Charter is not freely redistributable.

### 4. Rot within twelve months

- **Model IDs and prices** are hardcoded in about fifteen places and disagree
  with each other (`providers.py:51`, `planner.py:36`, presets, README). One
  retired ID breaks a preset; the cost tables go stale. One data file, and a
  weekly CI job that checks each ID against the provider's `/models`.
- **No lock file, no upper bounds.** A fresh install today already gets a
  Playwright the documented steps do not cover; `anthropic>=0.69` resolves to a
  different major. Ship a lock file and cap majors.
- **`reverse-geocoder`** last released 2016, LGPL, pulls scipy, data ten years
  old. Replace with a GeoNames extract (CC-BY, needs attribution) and numpy.
- **Chromium is the print engine** with an undocumented shrink-to-fit workaround.
  Pin Playwright; make the PDF-geometry check a named CI gate.
- **No packaging.** Fourteen `sys.path.insert` calls, top-level modules called
  `config`, `render`, `photos`. Needs one package, `pyproject.toml`, a console
  command, a published Docker image.
- **Docs drift.** `PLAN.md` still describes Postgres/Next.js/RQ and "Phase 0
  build session"; README, PLAN and code disagree on phase status.
- **Tests** are three scripts, not pytest; no UI test for 1,200 lines of
  untested JavaScript; no lint or type check; no coverage.
- **Bus factor one**, 56 commits in five days, no human review.

## Open and commercial

### Licence — decide this week

Apache-2.0 lets a lab or a competitor host the whole thing and owe nothing. You
are the only author and the project is five days old, so changing is free now
and expensive later.

Recommended: **AGPL-3.0 for the application, plus a contributor agreement** so
you can sell a commercial licence and run a hosted service. This is what
Immich and PhotoPrism do; the self-hosting audience accepts it. Keep the lab
specification files (`labs/*.yaml`) permissive (Apache or CC0) so labs and
users contribute them freely. Register a real name and domain —
"photo_album_maker" cannot be searched for or trademarked.

### Where the money is, in order

1. **Print orders.** The book costs CHF 40–100; the model costs cents. Connect
   a print-on-demand API (Gelato, Prodigi, Cloudprinter, Peecho, Blurb — check
   current terms) or lab affiliate programmes and take a margin per order. It
   also removes the worst step: uploading two PDFs to a lab by hand.
2. **Hosted version, paid per finished book** (≈ €9–15 for the print-ready
   export; designing and previewing free). People make one or two books a year;
   a subscription does not fit. Self-hosted stays free and complete.
3. **Professional photographers** (weddings, travel): client proofing, brand
   templates, batch albums, commercial licence, support. Fewer users, far
   higher willingness to pay.
4. Design/template packs. Sponsorship is a bonus, not a plan.

### What the hosted version needs first

The current server is one process with one global `STATE` and one user's keys.
Hosting is a new service layer, not a flag: accounts, per-user storage,
a job queue with isolated renderers, quotas, billing, deletion. Photos with
faces and GPS sent to model providers need a privacy policy, processor
agreements, EXIF stripping before upload, and a retention limit. HEIC decoding
(HEVC patents) and LGPL components need a notice file and a legal look.

## Discoverability (SEO)

The `seo-auditor` agent and `seo-audit` skill are written for voxquieta.org
(Next.js, sitemap, hreflang). There is no site here to audit, so they were not
run. What to build so they have something to check:

- **README first screen:** one sentence, one animated preview, three spread
  images, a link to a sample PDF. Move phases and findings to `docs/`.
- **GitHub:** description, topics (`photo-book`, `self-hosted`, `ai`,
  `print-on-demand`, `pdf`), social preview image, releases.
- **Landing page** with real sample albums and a "try with demo photos" button.
- **One page per lab and format** ("Saal Digital 30×30 bleed and spine"):
  people search for exactly this, and the data already lives in `labs/*.yaml`.
- **Launch:** awesome-selfhosted, r/selfhosted, Show HN, photography forums —
  only after the printed book exists, with photographs of it.
- Then copy the SEO agent and skill into this repository and point them at the
  new domain.

## Order of work

| Step | What | Why first |
|---|---|---|
| 1 | Fix S1–S4, atomic writes | Users' keys and work are at risk today |
| 2 | Order and measure the test book; ten real albums on a real model | Proves the product |
| 3 | Licence, name, domain | Free now, costly later |
| 4 | Package: `pyproject.toml`, Docker image, lock file, vendored fonts, pytest | Lets strangers install it |
| 5 | README with pictures, sample PDFs, landing page | Lets strangers want it |
| 6 | Second and third lab; print-API ordering | Audience and first revenue |
| 7 | Hosted service | Only after 1–6 show demand |
