# Notes for coding agents

Standing instructions from the repository owner. Follow them on every task.

This file is the agreement with whoever works here next, and it is kept true
by the agents themselves: **updating it is part of finishing a task**, not a
favour. The protocol is at the end, under "Keeping this file true". Read that
section before you close out a task, even a small one.

## The directions that outrank everything

These are decisions, not preferences. Do not design around alternatives; if one
of them genuinely blocks a task, say so and stop rather than quietly bending it.

- **The model never emits coordinates, CSS or pixels.** It chooses a template
  name, a photo id, a normalised focal point and a caption. Everything that
  places something on a page is computed by the system
  (`phase0/templates_lib.py`, `phase0/compose.py`).
- **The user outranks the model.** Their choices live in `overrides.yaml` and
  `settings.json` in the album directory and in `rotations.yaml` and
  `captions.yaml` beside the photos, and are re-applied deterministically to
  every layout version (`phase1/overrides.py`, `phase1/pages.py`). A redesign
  may never lose them. Their words are styled, never rewritten.
- **One model call per album.** 30–100 photos with captions fit in one context.
  No chunking, no page-by-page planning.
- **Lab numbers live in config**, `phase0/labs/*.yaml`, never hardcoded.
  `saal.yaml` still carries `VERIFY` constants: no book has been printed and
  measured yet, so do not treat those numbers as measured, and do not tune the
  template library against them.
- **sRGB only**, never CMYK. Derivatives are converted on generation and carry
  an embedded sRGB profile.
- **Local and offline by default.** Only designing needs a model; grouping,
  descriptions, sizes, rendering and export must keep working with no key and
  no network. Tests never call a real model.
- **One album, one process, one user.** Accounts, quotas and isolated renderers
  are a separate service layer, not a flag on this server. Don't half-build it.
- Not a drag-and-drop editor. The user never positions a box.

## Workflow

- **Open a pull request when a task is complete.** Commit, push the working
  branch, then open a PR against `main` without being asked. Don't wait for a
  "should I open a PR?" answer.
- Work on the branch you were given; never push to `main` directly.
- A PR that has been merged is finished. For follow-up work, start again from
  the latest `main` and open a new PR.
- One change per pull request. Run the checks below before pushing, and say in
  the PR which ones passed and which could not run (and why).
- Commit subjects read as a plain sentence about the change, the way the log
  already reads ("Two Windows bugs found by the new CI job: date labels crashed,
  and text files were not UTF-8"). No "fix stuff", no model or tool names in the
  message body beyond the attribution footer your harness gives you.
- Update the documents in the same PR as the behaviour change — see "The
  documents and what each is for".
- Never commit a photograph that is not synthetic, an album directory, or a key.
  Keys live in `~/.config/impaginai/keys.json` (`phase1/keystore.py`);
  `models.example.yaml` is the committed template, your own copy is not.

## Checks before pushing

Same as CI (`.github/workflows/ci.yml`):

```bash
pip install -r requirements.lock.txt -r requirements-dev.lock.txt
playwright install chromium
python -c "import reverse_geocoder as rg; rg.search([(47.4, 8.6)])"   # warm the geocoder
pytest --cov      # everything, each check reported on its own, with coverage
ruff check .      # mistakes the tests do not reach
```

What CI does beyond that, so a green run here is not a surprise there:

- `pytest` on Ubuntu with Python 3.11, 3.12 and 3.13, and on macOS 3.12.
- Windows runs the **engine only** (`test_loop.py`, `test_pages.py`). The two
  tests that start the editor as a separate process stop it with `SIGTERM`,
  which Windows does not have. The editor is therefore not covered on Windows:
  when you touch it, think about Windows by hand (paths, `cp1252` consoles,
  explicit UTF-8) rather than trusting the matrix.
- `pip-audit -r requirements.lock.txt --strict`, and Dependabot proposes
  upgrades weekly (`pillow` and `pypdf` grouped on their own: both parse
  untrusted binary input).
- Coverage has a floor of 80% (`pyproject.toml`); it exists to notice a module
  losing its tests, not as a target.
- `ruff` is configured for real mistakes only — undefined names, unused
  imports, always-true comparisons, mutable defaults. Style is not checked, so
  don't reformat code you are not changing. Line length 110.
- The calibration book is rendered on every run: a layout that validates can
  still come out wrong, and the Chromium scaling bug in `phase0/README.md` was
  invisible except in the PDF.
- A weekly `latest` job installs the newest versions the floors in
  `requirements.txt` allow. It does not run on pull requests, so a dependency
  release never turns your change red.

After changing either requirements file, regenerate both locks:

```bash
pip install uv
uv pip compile requirements.txt --universal --python-version 3.11 -o requirements.lock.txt
uv pip compile requirements-dev.txt --universal --python-version 3.11 \
    -c requirements.lock.txt -o requirements-dev.lock.txt
```

Each test file also runs alone, which is quicker while working on one area:

```bash
python phase1/tests/test_loop.py    # planner loop, fixtures, rendering
python phase1/tests/test_pages.py   # page repacker, front page, compositions
python phase1/tests/test_e2e.py     # CLI + editor server against a fake model
python phase1/tests/test_ui.py      # the editor in Chromium: load, describe, design, book, settings
```

- `test_e2e.py` drives the phase-2 editor over HTTP; nothing calls a real model
  (`phase1/tests/fake_model.py`).
- `test_loop.py` needs `reverse_geocoder`, which may fail to build in some
  sandboxes ("a group has no place", or a `setuptools` `install_layout` error
  against a distribution's patched `setuptools`). That is the environment, not
  the code: install into a fresh `venv` with an upgraded `pip`/`setuptools`,
  and check a failure against unmodified `main` before chasing it.
- Rendering needs Playwright's Chromium, and the two must be the same build. If
  the machine already has a browser the lock's Playwright does not match and
  `playwright install` cannot download one (a sandbox with a network policy),
  install the matching Playwright in your environment only — e.g.
  `pip install playwright==1.63.0` for a Chromium rev 1243 — and point it at
  the existing browser (`PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers`). Never
  change `requirements.lock.txt` to work around your own machine.
- `test_ui.py` loads `phase2/static/index.html` in Chromium and fails on any
  script error, failed request or duplicate id. It covers the main path only:
  **when you add a control, add a step for it.** When you add a route, add a
  case to `test_e2e.py`.

## Rules earned the hard way

Each of these comes from a real failure (the November audit,
`docs/reviews/2026-11-11-project-review.md`, and the CI jobs since). Breaking
one is a regression even when the tests stay green.

- **Write the user's work atomically.** `phase1/safeio.write_text` for
  `overrides.yaml`, `versions.json`, `settings.json`, caches — anything a crash
  mid-write would empty. Plain `Path.write_text` for those is a bug.
- **A key belongs to one host.** Never send a stored or environment key to a
  host it was not saved for: `GET /api/models/list?base_url=<attacker>` used to
  hand the key to any web page the user had open. Environment keys win over
  stored ones, and nothing that displays or logs returns a key — go through
  `keystore.status()` and `keystore.redact()`.
- **Every path that comes from the browser goes through `_browse_ok`**, the
  album path as well as the photo path. Album creation once wrote anywhere the
  user could write.
- **The editor refuses other websites.** `cross_site()` checks `Host`,
  `Origin` and `Sec-Fetch-Site` on every request; there is no login, so reaching
  the port is the permission. Keep that middleware in front of new routes, and
  do not describe `--host 0.0.0.0` as safe: it still has no authentication.
- **Uploads are streamed with a byte counter and a pixel limit.** Don't read a
  body into memory before checking its size, and don't disable Pillow's
  decompression-bomb guard.
- **Text is UTF-8, explicitly, everywhere.** A Windows default encoding broke
  reading and writing; `safeio.safe_console()` keeps a `cp1252` console from
  killing a job over a place name. Platform-specific `strftime` codes
  (`%-d`, `%e`) crash on Windows — format dates by hand.
- **An error a non-programmer sees is an instruction, not a traceback.** "The
  renderer's browser is missing or out of date, run `playwright install
  chromium`" beats a stack trace. No album open is a 409 with a sentence.
- **URL-encode file names in generated HTML.** `Sommer #3.jpg` stalled the
  renderer for 30 seconds and then failed.
- **Don't add another hardcoded model id, price or context limit.** They are
  already scattered and disagreeing; put new ones where the existing table is
  and prefer removing a duplicate to adding one.
- **Dependencies:** floors in `requirements.txt`, exact pins in the locks, caps
  on majors. Adding an unmaintained or heavy dependency needs a reason in the
  PR (`reverse-geocoder`, last released 2016 and pulling scipy, is the warning).

## Where things live

- `phase0/` print chain: renderer (`render.py`), geometry, template library
  (`templates_lib.py`), page compositions (`compose.py`), validation
  (`validate.py`), imaging and derivatives (`imaging.py`), charts, assets, and
  lab specs (`labs/saal.yaml`, which sets the 24-page minimum).
- `phase1/` planning: prompt (`prompt.py`), planner, providers, album state
  (`album.py`, `versions.py`), user overrides (`overrides.py`), the page
  repacker (`pages.py`, which runs after the model on every version: front
  page, no empty pages, place stamps, photos marked "in a composition"),
  photo handling and grouping (`photos.py`, `cluster.py`, `annotate.py`),
  places (`geocode.py`, `gazetteer.py` — a local list,
  OpenStreetMap only in the background), keys (`keystore.py`), durable writes
  (`safeio.py`), tests, and the committed synthetic `demo_photos/`.
- `phase2/` the editor: `server.py` (FastAPI) and `static/index.html` (one
  page, no build step, works offline).
- `presets/` model presets; `models.example.yaml` the template for a local
  model file.

## The documents and what each is for

- `README.md` — the shop window. Short, visual, honest about the phase table.
- `RUNNING.md` — the guide for a person on their own machine. Every command in
  it was actually run from a clean clone; keep it that way.
- `PLAN.md` — the living specification: constraints, template table, decision
  log. **Its "Stack" and "Architecture" sections are history, not the plan:**
  Postgres, Next.js and Redis/RQ were never built, and the real stack is
  FastAPI plus one static page over the filesystem. Don't resurrect them;
  correct the section when you touch that part of the file.
- `docs/plans/*.md` — agreed plans, with a status line at the top. Update the
  status as you build; the "Decided" table is the owner's answers and is not
  yours to change.
- `docs/reviews/*.md` — dated audits. Don't rewrite one; add a new dated file,
  or a status note under the finding, as the November review does.
- `CONTRIBUTING.md`, `CLA.md`, `COMMERCIAL.md`, `LICENSE` — AGPL-3.0 for
  everything except `phase0/labs/` (Apache-2.0, so labs and users can share
  specs freely). Outside contributions need the CLA sentence. Licence text
  stays in those files: source files carry no per-file headers, don't add them.

## Keeping this file true

This file only works if it matches the project, so improving it is the last
step of every task, before you open the pull request.

1. **Ask three questions.** Did I learn something the next agent needs to know?
   Did I lose time on something that could have been written down? Did anything
   already in here turn out to be wrong, stale or misleading?
2. **If any answer is yes, edit this file in the same pull request**, and say in
   the PR body what you changed here and why. A rule discovered and not written
   down is paid for again by the next agent.
3. **Write an entry as one rule plus its reason**, naming the file or the
   incident: "Write the user's work atomically — a crash mid-write emptied
   `overrides.yaml`". Point at the code; never copy code or long output in here.
4. **Prefer a test to a rule.** If CI can catch it, add the check and keep the
   note to a single line. A rule is for what no test can see — a decision, a
   platform gap, an order of work.
5. **Delete as readily as you add.** Remove what the code now enforces, what is
   no longer true, and what was only true of one task. Merge a section that
   grows past a dozen bullets. The whole file must stay readable in one sitting;
   if it does not, it stops being read, and then none of it is followed.
6. **Keep it general.** Nothing task-specific, no secrets, no absolute paths
   from a sandbox, nothing that belongs in `PLAN.md` or `docs/plans/`.
7. **Log the change below**, one dated line, so drift and rewrites are visible.

### Changes to this file

- **2026-11-18** — Rewritten against the current state: the project directions
  that outrank a task, the real CI matrix and its Windows gap, the lock and
  audit steps, the security and durability rules from the 11 November review
  with the incident behind each, an updated map of the code, what each document
  is for (including the stale stack section in `PLAN.md`), and this protocol.
  Added the two sandbox traps found while running the suite for that change: a
  clean `venv` for `reverse-geocoder`, and matching Playwright to a browser
  that is already on the machine.
