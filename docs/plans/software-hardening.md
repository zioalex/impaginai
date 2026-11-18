# Hardening plan — 18 November 2026

How to get ImpaginAI from "works on the author's machine" to "a stranger can
trust it, install it, break it and have the pipeline tell them so". Security,
tests, auditing, badges and automation, in the order that buys the most safety
per hour.

This is the software-engineering companion to
[`docs/reviews/2026-11-11-project-review.md`](../reviews/2026-11-11-project-review.md).
That review asked whether the project can become a product; this plan asks
whether the code can be trusted. It deliberately does not re-litigate the
product decisions (print the calibration book, run ten real albums, licence,
name) — those still come first in the overall order of work.

Patterns marked **[VQ]** are lifted from the sibling project
[`getinspiredbythebible`](https://github.com/zioalex/getinspiredbythebible)
(Vox Quieta), which already runs 37 workflows, pre-commit, commitlint,
release-please, recurring audit agents and metrics dashboards. The last
section says which of those to leave behind and why.

---

## 1. Where the project stands, measured today

Everything in this table was run in a clean container against
`main` (`7106f41`), not recalled from the review.

| Check | Command | Result |
|---|---|---|
| Linter, as configured | `ruff check .` | **clean** |
| Test suite | `pytest --cov` | **13 passed, 5 failed** — every failure environmental (no matching Chromium, no `reverse_geocoder`) |
| Coverage | same run | **70.4%**, below the project's own `fail_under = 80` — because the browser tests could not run. `pyproject.toml` records 84% when they do |
| Documented install | `pip install -r requirements.lock.txt` | **fails** — `reverse-geocoder` cannot build a wheel; because pip resolves the set atomically, *nothing* installs |
| Browser the renderer needs | `playwright install chromium` | wants **chromium v1254**; `requirements.txt` says "verified 1.63.0"; the lock pins **1.65.1** |
| Linter, broader rulesets | `ruff check --select ALL` | 44 blind `except`, 21 functions over complexity 12, 195 bare `print`, 252 unannotated arguments |
| Secrets in history | — | **never scanned** |
| Static analysis | — | **none** beyond ruff's default rules |
| Type checking | — | **none** |
| Supply-chain attestation | — | `pip-audit` only; no hashes, no SBOM, no Scorecard |

### What that adds up to

**Good, and load-bearing — do not break these while refactoring.**
The security findings S1–S4 from the review are genuinely fixed, and fixed
well: `phase1/keystore.py:76-82` binds a key to a host name rather than to
substring text in a URL, with the attack it prevents written in the comment;
`phase2/server.py:86-116` checks `Sec-Fetch-Site`, `Origin` and `Host`;
`phase2/server.py:1020-1027` counts upload bytes as they arrive;
`phase1/safeio.py` writes through a temporary file and `os.replace`, and the
three files that hold the user's work use it. `phase0/validate.py` makes print
rules executable instead of hoping the model obeys the prompt. Captions are
escaped on the way into HTML (`phase0/render.py:364`). This is better than most
projects of this age.

**The three structural weaknesses.**

1. **The pipeline reports; it does not gate.** `.github/workflows/ci.yml` has
   no `permissions:`, no `concurrency:` and no `timeout-minutes:` on any job,
   and every action floats on a major tag (`actions/checkout@v4`). Nothing
   enforces that a red run blocks a merge. A workflow that cannot block is a
   newsletter.
2. **The tests are coarse.** 5,941 statements are covered by **18 test
   functions**, and two of those are monoliths: `test_e2e.py::test_end_to_end`
   is one `pytest` function over 695 lines, `test_ui.py::test_editor_in_a_browser`
   one over 231. When the e2e test failed in the baseline run, the whole report
   was `AssertionError: {'running'...`. A suite that cannot say *which* promise
   broke makes every regression a debugging session.
3. **There is no artefact.** No `[project]` table in `pyproject.toml`, no
   version string anywhere in the tree, 23 `sys.path.insert` calls, no
   Dockerfile, no release, no tag. Nothing can be installed, pinned, rolled
   back or reported against. "Which version are you on?" has no answer.

### Still-open defects found while measuring

| # | Severity | Finding | Where |
|---|---|---|---|
| H1 | High | **A photograph's file name is written into the render's HTML unescaped and unencoded.** `src="{src.as_posix()}"` — a name containing `"` injects attributes into the page Chromium then renders; `#`, `%` or `?` silently truncates the path and the photo renders blank. The upload route sanitises names (`phase2/server.py:1010`), but a folder the user points at is read as-is, so this is reachable with any downloaded photo set. | `phase0/render.py:349`, `:480` |
| H2 | Medium | **The editor sends no security headers.** No `Content-Security-Policy`, `X-Content-Type-Options`, `X-Frame-Options` or `Referrer-Policy` on any of the 43 routes. The same origin serves the UI, the API and the user's photographs. | `phase2/server.py` |
| H3 | Medium | **Still no authentication.** `--host 0.0.0.0` exposes 43 routes, the folder browser and uploads to the whole network; `--allow-remote-settings` adds the key store. The same-site checks stop a browser on another page, not a peer on the LAN. Carried over from the review as open. | `phase2/server.py:79-116`, `:2043` |
| H4 | Medium | **44 blind `except` clauses swallow the cause**, 14 of them in `phase2/server.py`. Nine are `except: pass`. The review's "wide-gamut photos shift colour silently" is one of these (`phase0/render.py:81`). With no `logging` anywhere in the tree — 195 `print` calls and not one logger — a swallowed error leaves no trace at all. | `phase2/server.py` (14), `phase1/geocode.py` (8), `phase1/providers.py` (5) |
| H5 | Low | **One test asserts two unrelated things under one name.** `test_missing_browser_is_a_sentence` checks the error-message shaping (a unit property, passes) and then that a real Chromium exists on the machine (`assert R.browser_ready() is True`, an environment fact). It failed in the baseline for the second reason while the behaviour it is named for was fine. | `phase1/tests/test_pages.py:140-168` |
| H6 | Low | **The install documented in `RUNNING.md` does not complete** on a machine where `reverse-geocoder` has no wheel — and takes the other 14 dependencies down with it, because pip resolves atomically. An optional dependency is currently a required one. | `requirements.txt:43`, `requirements.lock.txt` |

---

## 2. What "rock solid" will mean

The target, stated as things that must be true — so the plan has an end.

- [ ] A stranger installs it in one documented command, on Linux, macOS and
      Windows, and the command either works or says exactly what is missing.
- [ ] `make check-all` runs everything CI runs, locally, in one command.
- [ ] Every pull request is gated: lint, types, tests on three Pythons, E2E,
      coverage floor, secret scan, dependency review, SAST. Red cannot merge.
- [ ] Any single test failure names the promise that broke, in its own test ID.
- [ ] The print geometry, the page invariants and the folder jail are proved by
      properties over generated input, not by a handful of examples.
- [ ] No secret has ever been committed, and a scan proves it each run.
- [ ] Every dependency is hash-pinned; an SBOM ships with every release.
- [ ] A version number exists, a changelog is generated, releases are tagged
      and carry the proof PDF.
- [ ] A container image runs the whole thing, Chromium included, with no
      Playwright download at all.
- [ ] The README's first screen shows what the thing makes, under a row of
      badges that are all green.
- [ ] A recurring audit runs without anyone remembering to run it, and its
      findings land in `docs/audits/` with a trend.

---

## 3. The workstreams

Nine, each with why, what, where the evidence is, and when it is done.
Effort is in focused half-days.

### W1 — Make the pipeline a gate (2 half-days)

The cheapest safety in the plan. `ci.yml` is already thoughtful about *what*
it runs; it is unhardened about *how*.

**Build**

- `permissions: contents: read` at workflow level, raised per job only where
  needed. Today every job gets the repository's default token scope.
- `concurrency: group: ${{ github.workflow }}-${{ github.ref }}` with
  `cancel-in-progress: true` on pull requests — superseded pushes stop
  burning minutes.
- `timeout-minutes` on every job (10 for lint/audit, 30 for test, 20 for
  Windows). A hung Chromium currently runs to the six-hour ceiling.
- Pin every action to a full commit SHA with the version in a trailing
  comment. `.github/dependabot.yml` already watches the `github-actions`
  ecosystem monthly, so the pins stay fresh without hand work. **[VQ]**
- A `ci-ok` aggregation job (`needs: [audit, lint, test, test-windows]`) that
  fails if any dependency did not succeed, and make *that* the single required
  status check. Then adding a matrix entry never silently un-gates the branch.
- Branch protection on `main`: require `ci-ok`, require a linear history,
  require conversation resolution, no force-push. `AGENTS.md` already forbids
  pushing to `main`; make the server enforce it.
- `.github/workflows/fresh-clone.yml`, weekly: follow `RUNNING.md` verbatim
  from an empty directory on all three platforms. This is the job that would
  have caught the Playwright 1.63 → 1.65.1 drift, H6, and every future
  "works from my venv" bug. Treat the README as a test fixture.

**Done when** a pull request with a deliberately broken test cannot be merged,
and a 60-second-stale run is cancelled automatically.

### W2 — One installable package, one command (3 half-days)

The 23 `sys.path.insert` calls, the three top-level modules called `config`,
`render` and `photos`, and the absence of a version are all the same missing
thing.

**Build**

- A `[project]` table in `pyproject.toml`: name `impaginai`, dynamic version,
  `requires-python = ">=3.11"`, dependencies read from the existing floors,
  and extras — `[geo]` for `reverse-geocoder` (this alone fixes H6),
  `[dev]`, `[all]`.
- One package, `src/impaginai/`, with `phase0`→`render`, `phase1`→`plan`,
  `phase2`→`editor` as sub-packages, and real intra-package imports. Do this
  as one mechanical commit with no behaviour change, and prove it with the
  existing suite before and after. The 23 `sys.path.insert` calls and the 34
  `module-import-not-at-top-of-file` warnings go away together.
- Console scripts: `impaginai` (the CLI) and `impaginai-editor`.
- `impaginai.__version__`, surfaced in `GET /api/album` and in the PDF's
  metadata, so a bug report can name a build.
- A `Makefile` with the self-documenting `help` target **[VQ]** —
  `make setup-dev lint type-check security test test-e2e check-all`. One
  command per thing CI does, same command locally and in the workflow, so the
  two cannot drift.
- `.python-version` (3.12) and `python-version-file:` in the workflows **[VQ]**,
  so the version lives in one place.

**Done when** `pip install -e '.[dev,geo]' && make check-all` is the whole
contributor setup, and `git grep sys.path.insert` returns only test helpers.

### W3 — Tests that name their own failure (4 half-days)

The suite's content is good; its shape is wrong. 18 functions, two of them
monoliths, no `conftest.py`, no fixtures, no parametrisation. The
`_run("name")` indirection in `test_pages.py:170-178` is a hand-rolled
parametrize that hides its own assertions.

**Build**

- `phase1/tests/conftest.py` with the fixtures the monoliths currently build
  by hand: `lab` (the Saal spec, session-scoped), `album` (a tmp album),
  `demo_photos`, `fake_model` (already exists as a module — make it a fixture),
  `editor` (the server as a subprocess, with teardown that works on Windows).
- Split `test_end_to_end` into one test per promise it already checks —
  roughly 20 of them: the CLI plans, the CLI revises, a version is written,
  rollback restores, overrides survive a replan, settings round-trip, the
  cost report adds up, a bad model response is repaired. Each gets a name a
  stranger can read in a failure report.
- Split `test_editor_in_a_browser` the same way: load, describe, design, book,
  settings, each its own test, sharing one browser fixture.
- Fix H5: separate `test_missing_browser_message` (a unit test) from a
  session-scoped `require_browser` fixture that skips with a sentence when
  Chromium is absent, so an environment gap reads as a skip and never as a
  failure. Add the inverse check as its own named test: **the installed
  browser revision matches the pinned Playwright** — the agreement nothing
  currently asserts.
- Markers in `pyproject.toml`: `unit`, `integration`, `e2e`, `browser`,
  `slow`. `make test` runs the fast ones; CI runs all.
- `pytest-xdist` for the unit layer, `pytest-timeout` globally so a hung
  render fails in 120 s with a traceback instead of hanging the job.
- Raise coverage where it is thinnest rather than globally: `phase0/charts.py`
  is at **9%** (91 statements, essentially untested) and `phase2/server.py` at
  45% in the baseline run. Per-module floors in `[tool.coverage.report]`, not
  one global number, so a well-covered module cannot subsidise a bare one.
- `diff-cover` on pull requests: new lines must be covered, which raises the
  floor by ratchet instead of by decree.

**Done when** `pytest --collect-only` lists 80+ named tests, and the e2e
failure from the baseline run would have printed one promise, not a dict.

### W4 — Invariants, not examples (3 half-days)

`phase1/pages.py:485` — `repack` — has a cyclomatic complexity of **74**. It
runs after the model on every single version and is the function that
guarantees the book is printable. `phase1/providers.py:797` `_openai` is 42,
`:815` `complete` 38, `phase1/planner.py:270` `_run` 35,
`phase0/validate.py:114` `check` 37. Example-based tests cannot cover a
74-branch function; properties can.

**Build**

- `hypothesis` over generated photo sets (1–400 photos, missing EXIF, absent
  GPS, identical timestamps, 1:20 aspect ratios, zero-byte files) asserting
  the repacker's invariants, which the code already intends and nothing
  currently proves:
  - no photograph is lost and none is duplicated;
  - no page is empty;
  - the page count is even and at least 24 (`phase0/labs/saal.yaml`);
  - every user override survives (`overrides.yaml` beats the model — the
    project's central promise);
  - no slot crosses the fold beyond the lab's tolerance;
  - effective DPI never drops below `block_below_dpi`.
- Properties for `phase0/geometry.py` too: `fit_rect` never returns a rect
  outside its frame, aspect ratio is preserved within a pixel, spine width is
  monotonic in page count.
- **Golden-file tests for the output**, because the review records that the
  Chromium scaling bug "was invisible except in the rendered output": commit a
  small set of rendered HTML and the PDF's page boxes (via `pypdf`), and diff
  the geometry — millimetres, bleed, trim, spine — not the pixels. Pixel-diff
  one spread with a tolerance as a separate, non-blocking job.
- A hostile-input contract test at the model boundary: `fake_model.py` already
  exists, so feed the planner truncated JSON, a template name that does not
  exist, a photo id that does not exist, 10,000 pages, negative coordinates,
  NaN. The planner must repair or refuse, never crash and never write a
  corrupt version.
- Adversarial tests for the folder jail (`_browse_ok`, `_album_ok`): `..`
  traversal, symlinks pointing out, a symlinked parent, `//host/share`,
  `C:\`, NUL bytes, Unicode normalisation collisions, a path that is a file.
  The review reproduced S3 here once; a named test matrix is how it stays
  fixed.
- Add H1's cases as a test: a photo called `Sommer #3.jpg`, `100%.jpg`,
  `a"b.jpg` must render correctly — fix with `urllib.parse.quote` on the path
  and attribute escaping.
- **Mutation testing as an audit, not a gate**: `mutmut` quarterly over
  `phase0/geometry.py`, `phase0/validate.py`, `phase1/pages.py`,
  `phase1/overrides.py`. A surviving mutant in the print rules is a missing
  test by definition. Report it; do not block merges on it.

**Done when** the invariants are executable, and a deliberate off-by-one in
`repack`'s page-count arithmetic is caught by a property rather than by a
reviewer's eye.

### W5 — The editor's HTTP surface (3 half-days)

43 routes, no auth, no headers, 45% covered. This is the part that a user puts
on a network.

**Build**

- **Fix H1 first.** `quote()` the path and escape the attribute in
  `phase0/render.py:349` and `:480`. It is both a correctness bug and an
  injection into a page Chromium executes.
- A security-headers middleware (H2): a strict `Content-Security-Policy` for
  the editor (`default-src 'self'`; the 837-line inline script needs a nonce
  or extraction — W6 extracts it anyway), `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`,
  `Permissions-Policy` with everything off. One middleware, one test asserting
  the headers on a representative route of each kind.
- **Authentication for non-local binds (H3).** A per-process token, printed in
  the start-up URL as a fragment the editor stores, required on every mutating
  route when the bind address is not loopback. Loopback keeps working with no
  token so the single-user case stays frictionless. One route table test:
  every `@app.{post,put,patch,delete}` is either in an explicit public
  allow-list or requires the token — a test that reads the route table, so a
  route added next year is covered the day it is written.
- Replace the 44 blind excepts (H4) in three passes: `logging` with a
  `--log-level` flag and `keystore.redact` on every record; then narrow each
  `except Exception` to the exceptions actually expected; then `log.exception`
  on the rest so a swallowed error leaves a trace. Start with
  `phase0/render.py:81` (the silent colour shift) and the nine `except: pass`.
  Lock it in with `BLE001`, `S110` and `S112` in the ruff selection and a
  per-file ignore list that only shrinks.
- Request-size and rate limits: a body cap on every JSON route (uploads
  already counted), and a simple per-process limiter on the expensive ones
  (`/api/relook`, `/api/plan`, `/api/export`) so a stuck tab cannot spend the
  user's API budget.
- A `GET /healthz` that reports version, browser presence and album state —
  what every monitor and container orchestrator asks for first.

**Done when** `--host 0.0.0.0` without a token refuses every mutating route,
the headers are asserted, and no `except` in `phase2/` is bare.

### W6 — Supply chain and secrets (2 half-days)

An AGPL project that handles API keys and parses untrusted binaries needs to
prove its inputs.

**Build**

- `pre-commit` **[VQ]**, trimmed to this repo: `trailing-whitespace`,
  `end-of-file-fixer`, `check-yaml`, `check-json`, `check-added-large-files`
  (the demo photos are committed on purpose — exempt them explicitly, as Vox
  Quieta exempts its committed Bibles), `check-merge-conflict`,
  `detect-private-key`, `mixed-line-ending`, plus `ruff` and `ruff-format`
  on commit and the slower hooks (`mypy`, `detect-secrets`, `yamllint`,
  `markdownlint`, `shellcheck`) on push. A `pre-commit.yml` workflow runs
  `pre-commit run --all-files` so an un-installed hook never means an
  un-checked file. **[VQ]**
- `detect-secrets` with a `.secrets.baseline` **[VQ]**, plus `gitleaks` over
  the **full history** once. The repo has handled live API keys since day one;
  nothing has ever looked.
- Turn on GitHub secret scanning and push protection (free on public repos),
  and private vulnerability reporting.
- `--generate-hashes` on both lock files and `--require-hashes` on install.
  `AGENTS.md` already documents the `uv pip compile` regeneration; this is one
  flag on each of the two commands and it closes dependency substitution.
- `dependency-review-action` on pull requests: a new dependency with a known
  advisory, or an incompatible licence, fails the PR before merge — which
  matters for an AGPL project that must not absorb something it cannot ship.
- `pip-licenses` in CI with an allow-list, and a `NOTICE` file. The review
  flags three real exposures: `reverse-geocoder` is LGPL, HEIC decoding
  touches HEVC patents, and the lab specs are deliberately Apache-2.0 inside
  an AGPL tree. Make that machine-checked instead of remembered.
- CycloneDX SBOM generated and attached to every release.
- OpenSSF Scorecard workflow, weekly, publishing to the badge. It is the
  cheapest external audit available and it scores exactly the things in W1.
- `CODEOWNERS`, `SECURITY.md` (supported versions, how to report, response
  window), `CODE_OF_CONDUCT.md`, `.github/pull_request_template.md` **[VQ]**
  with a test-plan checklist, and issue templates for bug / lab spec /
  template contribution. The CLA step from `CONTRIBUTING.md` belongs in the PR
  template as a checkbox.

**Done when** a commit containing a key is rejected locally and in CI, the
history has been scanned, and an install can be verified by hash.

### W7 — Static analysis and types (3 half-days)

`ruff check .` passes because the selection is narrow by design
(`pyproject.toml:16-18` — "Real mistakes only"). That was a sound choice for a
young codebase. The project is now 14,500 lines and the selection should grow.

**Build**

- Widen ruff in deliberate steps, each its own PR, each leaving the tree
  clean: `I` (import order), `UP` (pyupgrade), `RUF`, `SIM`, `C4`, then
  `BLE`/`S110`/`S112` with W5, then `S` minus `S101` for the bandit-equivalent
  checks, then `C901` with `max-complexity = 15` and a per-file ignore list
  for the five functions above it that only ever shrinks. Skip `ANN`, `D`,
  `EM`, `TRY` and `COM` — 252 + 149 + 48 + 81 + 32 findings of pure ceremony
  on a codebase whose comments are already better than most.
- `ruff format` once, in one commit, so diffs stop carrying whitespace.
- `mypy`, but **not** repo-wide: `strict = true` for
  `phase0/geometry.py`, `phase0/schema.py`, `phase0/templates_lib.py`,
  `phase1/overrides.py`, `phase1/safeio.py`, `phase1/versions.py` — the pure
  modules where types pay — and `ignore_errors` elsewhere, shrinking the
  exemption list over time. Full strictness on 14,500 lines with 252
  unannotated arguments would be a month of work for a fraction of the value.
- `CodeQL` for Python **and** JavaScript. The JavaScript matters: there are
  **837 lines of script in one inline block** in `phase2/static/index.html`
  with 247 functions and no analysis of any kind.
- JavaScript hygiene, which is currently absent entirely: extract the inline
  block to `phase2/static/app.js` (needed for the CSP in W5 regardless), add
  `eslint` **[VQ]** and `prettier`, and `vitest` for the pure helpers. Keep
  Playwright for the flows. The existing UI test's rule — fail on any console
  error, failed request or duplicate id — is excellent; keep it and extend it
  with `axe-core` so the editor is checked for accessibility too.

**Done when** CodeQL is green on both languages, the six typed modules are
strict, and a widened ruff still passes clean.

### W8 — Audits that happen without being remembered (2 half-days)

The 11 November review exists because someone ran two agents by hand. That is
the pattern to automate — Vox Quieta already did.

**Build**

- Copy `.claude/agents/risk-auditor.md` and `failure-forecaster.md` **[VQ]**
  into this repo and rewrite their scope maps for `phase0/` (geometry, print
  rules), `phase1/` (planning, providers, overrides) and `phase2/` (the HTTP
  surface and the editor). Keep their non-negotiables: every finding cites
  `file:line`, severity by blast radius × likelihood, read-only.
- A `docs/AUDIT_PLAYBOOK.md` with the per-area checklists and the severity
  rubric, so successive audits are comparable rather than re-invented.
- A `/risk-audit` command **[VQ]** that fans the agents out, diffs against the
  previous report (NEW / STILL OPEN / RESOLVED) and writes to
  `docs/audits/`. The 11 November review becomes the first baseline.
- `audit-metrics.yml` **[VQ]**: regenerate the trend when a report lands,
  monthly otherwise. A finding count that goes down is the only honest
  evidence that hardening is working.
- **A model-existence probe, weekly** **[VQ]** — this repo needs it more than
  Vox Quieta does. The review counted model IDs and prices hardcoded in about
  fifteen disagreeing places (`phase1/providers.py:49`, `phase1/planner.py:35`,
  `presets/*.yaml`, `models.example.yaml`, `README.md`). Consolidate to one
  data file, then probe each ID against the provider's `/models` endpoint
  weekly, with the exit codes separated the way Vox Quieta does it — 1 for a
  retired ID, 2 for a network error — so an outage is never read as a
  retirement. A retired model ID currently breaks a preset silently.
- A staleness check for the nine `VERIFY` constants in `phase0/labs/saal.yaml`
  and for the cost tables: fail the weekly run if a constant is still
  unverified after the calibration book has been marked as printed.
- `repo-metrics.yml` **[VQ]** monthly. Low value at bus-factor one; include it
  because it is nearly free once `audit-metrics` exists, and because the
  hotspot map (`phase1/pages.py`, `phase1/providers.py`, `phase2/server.py`)
  is the refactoring queue.

**Done when** nobody has to remember to audit, and `docs/audits/` has a trend
with the first report as its baseline.

### W9 — Releases, badges, and the shop window (3 half-days)

**Build**

- **Conventional commits + `release-please`** **[VQ]**: `commitlint` on PR
  titles and commits, `release-please` on `main` producing `CHANGELOG.md`, a
  tag and a GitHub Release. Vox Quieta's hard-won lesson is worth copying
  verbatim — lint the **PR title** as well as the commits, because a
  squash-merge makes the title the commit that release-please reads, and an
  unconventional title silently drops the change from the changelog.
- Attach to each release: the inner proof PDF and cover PDF for the demo
  album, and the SBOM. A release that carries its own evidence is the strongest
  signal a print project can send.
- **A container image** published to `ghcr.io` on every release, with Chromium
  baked in at the revision the pinned Playwright expects. This is the single
  highest-leverage item in the whole plan: it removes the 150 MB browser
  download, the version drift behind H6, and the "install is for developers
  only" product risk, all at once. `docker run -p 8000:8000 -v ./photos:/photos
  ghcr.io/zioalex/impaginai` should design an album. Add `hadolint` **[VQ]**
  and a Trivy image scan to the release workflow.
- **Badges**, as a row under the title, in this order — each one a claim that a
  workflow actually checks:

  | Badge | Source | Claim |
  |---|---|---|
  | CI | `ci.yml` | tests pass on 3 Pythons × 3 OSes |
  | Coverage | Codecov, or shields + a gist if you would rather add no third party | the suite means something |
  | CodeQL | `codeql.yml` | no known injection class |
  | OpenSSF Scorecard | `scorecard.yml` | the supply chain is hardened |
  | pre-commit | `pre-commit.yml` | the hooks are enforced, not suggested |
  | Release | `release-please.yml` | a current, tagged version exists |
  | Python 3.11–3.13 | static | the support matrix |
  | Licence AGPL-3.0 | static | the terms, visible before cloning |
  | Docker image | ghcr | one command to run |

  Ten badges on a project with no printed book would be a lie. Add each badge
  in the PR that makes its claim true, never before.
- Then the README's actual problem, which badges do not solve and the review
  names: the first screen is a phase table. It needs one sentence, a picture of
  a spread, a sample PDF and a link to the demo — above the badges, above
  everything. A visitor decides in ten seconds and a badge row is not what
  decides them.

**Done when** `v0.1.0` exists, carries a PDF and an SBOM, the image runs, and
every badge is green because its workflow passes.

---

## 4. Sequence

Four blocks. Each ends with the tree green and merged, so the project is never
mid-refactor for long. Roughly 25 half-days of focused work; a block is a
week at a relaxed pace.

| Block | Contents | Why here |
|---|---|---|
| **1. Floor** | W1 (gate) · W6 secrets+pre-commit · H1 fix · H6 extras split · fresh-clone job | Cheapest, highest-value, no refactoring. After this a red run blocks a merge, a leaked key is impossible, and the documented install works. H1 is a live injection bug. |
| **2. Shape** | W2 (package, Makefile, version) · W3 (split the monoliths, conftest, markers, per-module floors) | Both are mechanical and both unblock everything after. Do the package move while the suite is the only thing depending on the layout. |
| **3. Proof** | W4 (properties, golden geometry, hostile input, jail matrix) · W5 (headers, token, logging, blind excepts) · W7 (ruff widening, targeted mypy, CodeQL, JS extraction + eslint) | The real hardening. Needs W3's fixtures to be worth writing. |
| **4. Outward** | W8 (audit agents, metrics, model probe) · W9 (release-please, image, badges, README first screen) | Only honest once blocks 1–3 are true. |

**Interleaving with the product work.** The 11 November review's order stands:
print the calibration book, run ten real albums on a real model, settle the
name and domain. Block 1 is two days and should happen now regardless — H1 is
a live bug and an unenforced pipeline is a daily risk. Blocks 2–3 are the right
work to do *while waiting for the printed book to arrive*, since neither needs
a lab or an API key. Block 4 should wait for the book, because a release and a
badge row that advertise an unprinted book are a promise the project cannot
keep yet.

---

## 5. What not to copy from getinspiredbythebible

Vox Quieta earns its 37 workflows: it has a deployed backend, a Next.js
frontend, an Android app on a release track, Azure infrastructure, a database
with migrations and real users. ImpaginAI is 14,500 lines of Python that runs
on one machine. Copying the whole apparatus would cost weeks and then charge
rent in maintenance.

**Leave behind:**

- **Deployment, infrastructure and monitoring workflows** — `azure-deploy`,
  `prod-monitor`, `prod-deploy-drift`, `prod-browser-smoke`,
  `db-firewall-cleanup`, `secrets-expiry-check`. There is no production. These
  become relevant only if the hosted version in the review ever gets built.
- **`black` alongside `ruff`.** Vox Quieta runs both for historical reasons.
  `ruff format` alone, here.
- **Story-ID enforcement** (`story-id-check.yml`, the `BITB-xxx` convention).
  Valuable with a backlog and multiple contributors; pure friction at bus
  factor one. Adopt it the day a second person commits.
- **The backlog and weekly-report automation.** Same reason.
- **`hadolint`, `yamllint --strict`, `markdownlint` as blocking gates.** Take
  `hadolint` with the Dockerfile in W9; run the other two in pre-commit only.
  A failing markdown lint should never be able to block a security fix.
- **The 37-workflow habit itself.** Prefer one `ci.yml` with well-named jobs
  and a `ci-ok` aggregator over many workflows, until a job genuinely needs a
  different trigger, permission set or cadence. Fewer files, one required
  check, no drift between near-identical setups.
- **`claude.yml`** — the `@claude` responder. Not wrong, just not hardening;
  add it when you want PR review help, not as part of this plan.

**Take without modification:** the pre-commit layout and its
`stages: [pre-commit]` / `[pre-push]` split; `.secrets.baseline`;
`.github/ACTIONS_SECURITY.md`; the self-documenting Makefile `help` target;
the commitlint-plus-PR-title pair and the reason for it; the model-existence
probe's separated exit codes; the audit agents and their "every finding cites
`file:line`" rule; the habit — visible in every one of their workflow headers
— of writing *why the job exists* at the top of the file. That last one this
repo already does better than most; keep doing it.

---

## 6. Decisions I need from you

1. **Coverage badge**: Codecov (one more third party in the pipeline, with a
   token) or shields.io reading a gist the CI writes (no third party, slightly
   more plumbing)? I lean gist, for a project whose selling point is privacy.
2. **Conventional commits**: adopting `release-please` means every commit
   message and PR title gets linted. It is the only way to get a changelog for
   free, but it changes how you commit. Yes, or stay with hand-written release
   notes?
3. **The package move in W2** renames `phase0/`, `phase1/`, `phase2/` to
   `render/`, `plan/`, `editor/` inside one package. It is the right structure
   and it invalidates every path in `PLAN.md`, `README.md`, `RUNNING.md` and
   `AGENTS.md`. Do it now, or keep the phase names as package names
   (`impaginai.phase0`) to avoid a documentation rewrite?
4. **`reverse-geocoder`**: W2 makes it an optional extra, which fixes the
   broken install. The review separately recommends replacing it outright (last
   released 2016, LGPL, drags in scipy, data ten years old) with a GeoNames
   extract. Optional-extra now and replace later, or replace in block 2?
5. **mypy depth**: six strict modules as proposed, or wider?

---

## 7. Where this plan's own claims came from

Everything in §1 was produced in a clean container on 18 November 2026 against
`main` at `7106f41`, with `pip install -r requirements.lock.txt` (minus
`reverse-geocoder`, `numpy` and `scipy`, which cannot build here — the failure
documented in `AGENTS.md`), `pytest --cov`, `ruff check` at several selections,
and reading the cited lines. The five test failures in the baseline are
environmental, not regressions: four need a Chromium matching Playwright
1.65.1, which this container cannot download, and one needs the geocoder.
Nothing in §1 or in the H1–H6 table is carried over from the 11 November review
without being re-checked against the current code; where that review's findings
are now fixed, this plan says so.
