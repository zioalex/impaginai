<!-- Keep a pull request to one change. AGENTS.md and CONTRIBUTING.md are the
     standing rules; this is the short version of them. -->

## What

<!-- What changes and why. Link the plan or review item if there is one
     (docs/plans/, docs/reviews/). -->

-

## Test plan

<!-- Exact commands you ran, and what they said. "Tests pass" is not a test
     plan; the suite's output is. -->

- [ ] `pytest --cov` — results:
- [ ] `ruff check .`
- [ ] `pre-commit run --all-files` (both stages)
- [ ] Tests added or updated for the behaviour that changed
- [ ] Anything that could **not** run here, and why:

## If this touches the editor

<!-- phase2/static/index.html or phase2/server.py. Delete if it does not. -->

- [ ] `phase1/tests/test_ui.py` covers the control I added or changed
- [ ] No new script error, failed request or duplicate id (the UI test fails on all three)
- [ ] Anything from the server written into the page goes through `esc()` or `encodeURIComponent()`

## If this touches the print chain

<!-- phase0/, or anything that changes the rendered output. Delete if not. -->

- [ ] The calibration book still renders (`python render.py proof --layout examples/layout_test24.json …`)
- [ ] Page geometry unchanged, or the change is intended and stated here
- [ ] `phase0/labs/*.yaml` constants not altered without a measurement to back it

## If this changes dependencies

- [ ] All three lock files regenerated with the commands in `AGENTS.md`
- [ ] `pip-audit` clean
- [ ] A new runtime dependency's licence is compatible with AGPL-3.0

## Licence

- [ ] I have signed the CLA — see [`CLA.md`](../CLA.md). One sentence in your
      first pull request is enough; it cannot be merged without it.
