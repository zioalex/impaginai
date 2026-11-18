# Notes for coding agents

Standing instructions from the repository owner. Follow them on every task.

## Workflow

- **Open a pull request when a task is complete.** Commit, push the working
  branch, then open a PR against `main` without being asked. Don't wait for a
  "should I open a PR?" answer.
- Work on the branch you were given; never push to `main` directly.
- A PR that has been merged is finished. For follow-up work, start again from
  the latest `main` and open a new PR.
- Run the tests below before pushing, and say in the PR which ones passed and
  which could not run (and why).

## Tests

Same as CI (`.github/workflows/ci.yml`):

```bash
pip install -r requirements.lock.txt -r requirements-dev.lock.txt
pip install -r requirements-geo.lock.txt   # optional, and installed on its own
pytest --cov      # everything, each check reported on its own, with coverage
ruff check .      # mistakes the tests do not reach
```

CI installs from all three locks. `requirements-geo.lock.txt` is a separate
line on purpose: `reverse-geocoder` builds from source where it has no wheel,
and a failed build inside the main resolution left pip installing nothing at
all. After changing any requirements file, regenerate all three:

```bash
pip install uv
uv pip compile requirements.txt --universal --python-version 3.11 -o requirements.lock.txt
uv pip compile requirements-dev.txt --universal --python-version 3.11 \
    -c requirements.lock.txt -o requirements-dev.lock.txt
uv pip compile requirements-geo.txt --universal --python-version 3.11 \
    -c requirements.lock.txt -o requirements-geo.lock.txt
```

Each file also runs alone, which is quicker while working on one area:

```bash
python phase1/tests/test_loop.py    # planner loop, fixtures, rendering
python phase1/tests/test_pages.py   # page repacker, front page, compositions
python phase1/tests/test_e2e.py     # CLI + editor server against a fake model
python phase1/tests/test_ui.py      # the editor in Chromium: load, describe, design, book, settings
```

- `test_e2e.py` drives the phase-2 editor over HTTP; nothing calls a real model.
- `test_loop.py` needs `reverse_geocoder` from `requirements-geo.lock.txt`,
  which may fail to build in some sandboxes ("a group has no place"). That is
  the environment, not the code: check it against unmodified `main` before
  chasing it. Everything else in the suite runs without it.
- Rendering needs Playwright's Chromium. If `pip install -r requirements.txt`
  pulls a Playwright whose browser is not installed, pin the version that
  matches the installed browser instead of downloading another one.
- For editor (`phase2/static/index.html`) changes, `test_ui.py` loads the page
  in Chromium and fails on any script error, failed request or duplicate id.
  It covers the main path only; when you add a control, add a step for it.

## How the album is put together (where things live)

- `phase0/` renderer, geometry, template library (`templates_lib.py`), page
  compositions (`compose.py`), lab specs (`labs/saal.yaml`, which sets the
  24-page minimum).
- `phase1/` planning: prompt, planner, user overrides (`overrides.py`), and the
  page repacker (`pages.py`), which runs after the model on every version
  (front page, no empty pages, place stamps, photos the user marked
  "in a composition").
- `phase2/` the editor: `server.py` (FastAPI) and `static/index.html`.
- The user's choices always beat the model's: they live in
  `overrides.yaml` and `settings.json` in the album directory, and
  `rotations.yaml` and `captions.yaml` beside the photos. They are re-applied
  deterministically to every layout version.
