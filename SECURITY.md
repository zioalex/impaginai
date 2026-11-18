# Security

## Reporting a vulnerability

**Please do not open a public issue for a security problem.**

Use GitHub's private vulnerability reporting: the **Security** tab of this
repository → **Report a vulnerability**. It is private to the maintainer and
needs no email address from either of us. If that is unavailable to you,
message the owner through [github.com/zioalex](https://github.com/zioalex) and
ask for a private channel before sending details.

This is a one-maintainer project, so an honest expectation rather than a
promise: an acknowledgement within a week, and a fix or a decision before any
public description of the problem. If you report something real you will be
credited in the release notes unless you would rather not be.

## Supported versions

There is no tagged release yet, so the answer is `main` and nothing else. Once
releases exist this section will name the versions that still receive fixes.

| Version | Supported |
|---|---|
| `main` | yes |
| anything else | no — there is nothing else yet |

## What this program is, as far as security goes

Knowing the intended shape makes it clear which findings are bugs.

- **It runs on your machine, for you.** The editor binds `127.0.0.1` by
  default and has no accounts, no sessions and no multi-user model. Anyone who
  can reach the port can do anything the editor can do.
- **It holds API keys.** Stored keys live in `~/.config/impaginai/keys.json`,
  readable only by you; a key from the environment always wins over a stored
  one. A key is bound to the host it was saved for and is never sent anywhere
  else — see `phase1/keystore.py`, which exists because the opposite bug was
  found and fixed.
- **It parses files it did not make.** Photographs (including HEIC) and PDFs
  are untrusted binary input. `pillow` and `pypdf` are the two dependencies
  that must never lag, which is why `.github/dependabot.yml` groups them on
  their own and CI runs `pip-audit` on every push.
- **It sends photographs to a model provider** when you ask it to design an
  album, and to nowhere else. There is no telemetry, no analytics and no
  phone-home.
- **It writes inside the album folder and the photo folder**, and refuses
  paths outside them.

## In scope

- Anything that reads or exfiltrates an API key.
- Anything that escapes the album or photograph folder jail.
- Anything that runs code or injects markup through a file name, a caption, a
  photograph's contents, or a model's reply.
- Reaching a route from another origin, or from the network when the editor is
  bound to loopback.
- A dependency advisory that affects this project in practice.

## Known and documented, not a vulnerability report

These are design limits, written down rather than hidden. Reports that restate
them are welcome as discussion, not as findings:

- **`--host 0.0.0.0` has no authentication.** Binding to a non-loopback
  address exposes every route to whoever can reach the port. Settings and
  uploads additionally need `--allow-remote-settings`, which exposes the key
  store too. Do not do this on a network you do not control. Adding a token
  for non-local binds is planned — see
  `docs/plans/software-hardening.md` (W5).
- **No sandbox between you and your own album.** The editor's folder browser
  is meant to let you choose any folder you can read.
- A model provider sees the photographs and captions you send it. Which
  provider is your choice, including a local one.

## If you are writing a workflow for this repository

Logs of GitHub Actions runs in a public repository are public and cannot be
made private. Keep every credential in GitHub Secrets, where it is masked;
never `echo` a secret, never put one in a URL without `::add-mask::`, and
never give an unpinned third-party action access to one. Actions in
`.github/workflows/` are pinned to a commit SHA for that reason.
