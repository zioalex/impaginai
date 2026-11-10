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
python phase1/tests/test_loop.py    # planner loop, fixtures, rendering
python phase1/tests/test_pages.py   # page repacker, front page, compositions
python phase1/tests/test_e2e.py     # CLI + editor server against a fake model
```

- `test_e2e.py` drives the phase-2 editor over HTTP; nothing calls a real model.
- `test_loop.py` needs `reverse_geocoder`, which may fail to build in some
  sandboxes ("a group has no place"). That is the environment, not the code:
  check it against unmodified `main` before chasing it.
- Rendering needs Playwright's Chromium. If `pip install -r requirements.txt`
  pulls a Playwright whose browser is not installed, pin the version that
  matches the installed browser instead of downloading another one.
- For editor (`phase2/static/index.html`) changes, also check the page in a
  real browser: the JS has no build step and no unit tests, so a syntax check
  alone misses things like two elements sharing an id.

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
