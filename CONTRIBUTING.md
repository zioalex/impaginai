# Contributing

Contributions are welcome: bug reports, fixes, templates, translations and
lab specifications.

1. **Sign the CLA once.** Read [`CLA.md`](CLA.md) and add the sentence from its
   "How to sign" section to your first pull request. It lets the project be
   offered under the AGPL-3.0-only and under commercial licences; you keep your
   copyright. We cannot merge code without it. If your change was written with
   an AI assistant, say so in the pull request (`CLA.md` section 4.4).
2. **Install the hooks.** They are the same checks CI runs, so a push is
   not red for something that was green locally:

   ```bash
   pip install pre-commit
   pre-commit install && pre-commit install --hook-type pre-push
   ```

3. **Set up and test the way CI does.** The commands are in
   [`AGENTS.md`](AGENTS.md) (`pytest --cov` and `ruff check .`). The one
   optional dependency, offline place names, installs on its own line
   (`requirements-geo.lock.txt`) and the rest of the suite runs without it.
4. Keep a pull request to one change, say what you ran — including anything
   that could not run, and why — and update the docs when behaviour changes.
   The pull request template asks for exactly this.
5. **Security problems do not go in a pull request or an issue.** See
   [`SECURITY.md`](SECURITY.md) for the private channel.

## Licences in this repository

| Where | Licence |
|---|---|
| Everything, unless noted | AGPL-3.0-**only**, see [`LICENSE`](LICENSE) |
| `phase0/labs/` (lab specification files) | Apache-2.0, see `phase0/labs/LICENSE` |

The lab specifications stay permissive so print labs and users can share them
freely. New source files should carry the two `SPDX-FileCopyrightText` and
`SPDX-License-Identifier` lines that the existing ones have. Commercial licensing: see [`COMMERCIAL.md`](COMMERCIAL.md).
