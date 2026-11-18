#!/usr/bin/env python3
"""Every blob that has ever been in this repository, checked for key shapes.

`detect-secrets` and the pre-push hook guard what is about to be committed.
They say nothing about what was committed before they existed — and this
project handled live API keys from its first week. Rewriting history to remove
a key is expensive and only possible while few people have cloned, so the
question "has one ever been in here" is worth a cheap, honest answer that can
be re-run.

    python tools/scan_history_for_secrets.py

Exit 0 means no key-shaped string outside the known test fixtures. Exit 1 names
every hit with the commit-reachable blob it is in, so it can be looked at.

This reads `git rev-list --all --objects`, so it covers every branch and tag
the clone has, including commits no branch points at any more. It does not see
objects a shallow clone never fetched: run it on a full clone (CI does).
"""

from __future__ import annotations

import collections
import re
import subprocess
import sys

# Shapes, not entropy. A false positive costs a glance; a missed key costs a
# key. `detect-secrets` does the entropy half on the working tree.
PATTERNS = {
    "anthropic key": re.compile(rb"sk-ant-[A-Za-z0-9_\-]{20,}"),
    "openrouter key": re.compile(rb"sk-or-v?\d?-[A-Za-z0-9]{20,}"),
    "openai key": re.compile(rb"sk-[A-Za-z0-9]{32,}"),
    "aws access key": re.compile(rb"(?<![A-Z0-9])AKIA[0-9A-Z]{16}(?![A-Z0-9])"),
    "github token": re.compile(rb"gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{20,}"),
    "slack token": re.compile(rb"xox[baprs]-[A-Za-z0-9-]{10,}"),
    "google api key": re.compile(rb"AIza[0-9A-Za-z_\-]{35}"),
    "private key": re.compile(rb"-----BEGIN (RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"),
}

# Strings the tests use on purpose. Each is a sentence about why it is not a
# secret, not a hash to be taken on trust: the fixtures spell out that they are
# fixtures, which is what makes them safe to list here. A real key would not
# contain any of these.
FIXTURE_MARKERS = (
    b"0123456789abcdef",      # phase1/tests/test_pages.py, the stored-key fixture
    b"fromtheenvironment",    # phase1/tests/test_pages.py, the environment-key fixture
    b"SECRETtail",            # the tail the redaction test looks for
    b"EXAMPLE",
    b"xxxxxxxx",
)

# Blobs that have been read and are known not to hold a key, named by content
# rather than by path.
#
# A path exemption would be a standing hole in the scanner, and it would sit
# exactly where the key shapes are. A blob hash cannot grow one: git names a
# blob by its contents, so this entry can only ever refer to these exact
# bytes. Change one character of that file and the hash changes and the new
# version is scanned like anything else.
#
# The entry below exists because this scanner's own test must feed it real key
# shapes, and the first commit of that test wrote them as literals. The test
# now assembles them from fragments at import, so no later commit of it will
# need an entry here — but that first blob is in history for good, and
# rewriting a pushed branch to remove a string that was never a secret is a
# worse trade than naming it.
KNOWN_BLOBS = {
    # tools/test_scan_history_for_secrets.py as first committed, in 25219ef.
    # Its key shapes are the test's own inputs: a made-up Anthropic, AWS,
    # GitHub and Slack shape, each written to match a pattern and nothing
    # else. Read the blob if you want to check: `git cat-file -p <hash>`.
    "6d4f35de7ebfad8e4a3e5b09d81aa5f9d170ae34":  # pragma: allowlist secret
        "this scanner's own test, first commit: its key shapes are test inputs",
}

MAX_BLOB = 4_000_000          # a photograph is not a key


def is_fixture(hit: bytes) -> bool:
    return any(m.lower() in hit.lower() for m in FIXTURE_MARKERS)


def main() -> int:
    listing = subprocess.run(["git", "rev-list", "--all", "--objects"],
                             capture_output=True, check=True).stdout
    paths: dict[str, str] = {}
    for line in listing.decode(errors="replace").splitlines():
        sha, _, path = line.partition(" ")
        if len(sha) == 40:
            paths.setdefault(sha, path or "(no path: a tree or a commit)")
    if not paths:
        print("no objects: is this a git repository?", file=sys.stderr)
        return 2

    batch = subprocess.run(["git", "cat-file", "--batch"],
                           input=("\n".join(paths) + "\n").encode(),
                           capture_output=True, check=True).stdout

    real: list[tuple[str, str, str, str]] = []
    fixtures: collections.Counter = collections.Counter()
    known: list[str] = []
    scanned = at = 0
    while at < len(batch):
        end = batch.index(b"\n", at)
        header = batch[at:end].decode(errors="replace").split()
        at = end + 1
        if len(header) < 3:
            continue
        sha, kind, size = header[0], header[1], int(header[2])
        body, at = batch[at:at + size], at + size + 1
        if kind != "blob" or size > MAX_BLOB:
            continue
        if sha in KNOWN_BLOBS:
            known.append(sha)
            continue
        scanned += 1
        for name, rx in PATTERNS.items():
            for found in rx.finditer(body):
                hit = found.group()
                if is_fixture(hit):
                    fixtures[(name, paths.get(sha, "?"))] += 1
                else:
                    real.append((name, paths.get(sha, "?"), sha[:10],
                                 hit[:12].decode(errors="replace") + "…"))

    print(f"objects in history: {len(paths)}, blobs scanned: {scanned}")
    for (name, path), n in sorted(fixtures.items()):
        print(f"  allowed: {n:3} {name} in {path} (a test fixture)")
    # Never silent about what it did not look at.
    for sha in known:
        print(f"  skipped: blob {sha[:10]} — {KNOWN_BLOBS[sha]}")
    for sha in KNOWN_BLOBS:
        if sha not in known:
            print(f"  note: KNOWN_BLOBS lists {sha[:10]}, which is no longer in "
                  f"history — the entry can go")

    if real:
        print(f"\n{len(real)} string(s) shaped like a real key:")
        for name, path, sha, sample in real:
            print(f"  {name:16} {path:50} blob {sha} {sample}")
        print("\nIf one of these is a key, rotate it first and rewrite history second.")
        print("If it is a fixture, make the fixture say so and add its marker to")
        print("FIXTURE_MARKERS in this file.")
        return 1

    print("\nno key-shaped string in this repository's history, outside the fixtures")
    return 0


if __name__ == "__main__":
    sys.exit(main())
