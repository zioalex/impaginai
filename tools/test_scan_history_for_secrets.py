#!/usr/bin/env python3
"""The history scanner's own test.

A scanner nobody has tried on a real key shape is a green tick, not a check.
This feeds it strings it must catch and strings it must not, so an edit to the
patterns or to FIXTURE_MARKERS cannot quietly turn it into a no-op.

    python -m pytest tools/test_scan_history_for_secrets.py
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "scan_history", Path(__file__).with_name("scan_history_for_secrets.py"))
scan = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scan)


def _detect(text: bytes) -> str | None:
    """What the scanner would report for one string, fixtures excluded."""
    for name, rx in scan.PATTERNS.items():
        found = rx.search(text)
        if found and not scan.is_fixture(found.group()):
            return name
    return None


# Every key shape this test must DETECT is assembled from fragments, so that
# no complete key shape exists as a literal anywhere in this file.
#
# That is not decoration. The history scanner reads every blob ever committed,
# and the moment this file was committed it became the one blob that always
# matches — CI went red on a test file for containing its own test data, which
# is how this comment came to be written. The two ways out were to exempt this
# path in the scanner, putting a hole in it exactly where the key shapes live,
# or to keep the bytes out of the source and assemble them at import. The
# second leaves the scanner with no exemptions at all, and the test is not one
# bit weaker: what reaches the pattern is the same bytes either way.
#
# It is also what `detect-private-key` needs, which is a plain regex with no
# pragma and a hook worth keeping absolute.
#
# A fragment boundary has to fall inside the part the pattern matches — after
# "sk-" but before "ant-", after "AKIA", after "ghp_" — or the literal still
# matches and nothing has been gained.
DETECTABLE = {
    "anthropic key": b"sk-" + b"ant-api03-" + b"ZmFrZWJ1dGxvb2tzcmVhbA-AAbbCCddEEff",
    "aws access key": b"AKIA" + b"ZZ47TKQW3MNPRQXB",
    "github token": b"ghp_" + b"aB3dEfGh1JkLmN0pQrStUvWxYz0123456789",  # pragma: allowlist secret
    "slack token": b"xoxb-" + b"1234567890-abcdefghijkl",  # pragma: allowlist secret
    "private key": b"-----BEGIN RSA PRIVATE" + b" KEY-----",
    "private key (openssh)": b"-----BEGIN OPENSSH PRIVATE" + b" KEY-----",
}

# These, by contrast, are meant to stay QUIET, because they say in the string
# that they are fixtures — that is the property under test, so they have to be
# written out in full, and the scanner's FIXTURE_MARKERS is what keeps them
# quiet rather than any exemption.
FIXTURES = {
    # the stored-key fixture from phase1/tests/test_pages.py
    b"sk-or-v1-0123456789abcdefSECRETtail9876",  # pragma: allowlist secret
    b"AKIAIOSFODNN7EXAMPLE",  # pragma: allowlist secret
}


@pytest.mark.parametrize("text, expected", [
    *[(shape, name.removesuffix(" (openssh)")) for name, shape in DETECTABLE.items()],
    *[(shape, None) for shape in sorted(FIXTURES)],
    # Ordinary text must not trip it.
    (b"just some prose mentioning an sk thing and a key", None),
    (b"https://openrouter.ai/api/v1", None),
])
def test_detects_key_shapes_but_not_fixtures(text: bytes, expected: str | None):
    assert _detect(text) == expected


def test_known_blobs_are_pinned_and_explained():
    """The one exemption the scanner has, kept narrow.

    KNOWN_BLOBS is content-addressed on purpose: a full git blob hash can only
    ever refer to one exact set of bytes, so an entry cannot widen into a hole
    the way a path or a glob would. These assertions are what stop that
    property from being given away by a well-meaning edit — a short hash, a
    path, or an entry with no reason to read."""
    import re as _re

    for sha, reason in scan.KNOWN_BLOBS.items():
        assert _re.fullmatch(r"[0-9a-f]{40}", sha), \
            f"{sha!r}: a blob must be named by its full 40-character hash, " \
            f"never by a path or a prefix"
        assert len(reason) > 20, f"{sha}: say why this blob is not a secret"

    # An exemption is a judgement someone has to be able to re-make, so it has
    # to stay small enough to read.
    assert len(scan.KNOWN_BLOBS) <= 5, \
        "more than a handful of exempt blobs means the patterns need fixing, " \
        "not the allowlist extending"


def test_this_repository_is_clean():
    """The scanner, on this repository, end to end.

    This reads committed history, so a file still only staged is invisible to
    it: this test can pass locally and fail in CI one commit later, which is
    exactly what happened the first time this file was committed. CI is the
    check that counts here.

    Skipped rather than failed on a shallow clone, where most of history was
    never fetched and a pass would mean nothing."""
    import subprocess

    shallow = subprocess.run(["git", "rev-parse", "--is-shallow-repository"],
                             capture_output=True, text=True)
    if shallow.stdout.strip() == "true":
        pytest.skip("shallow clone: run this on a full clone (CI does)")
    assert scan.main() == 0, "a key-shaped string is in this repository's history"
