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


# The strings below are key-SHAPED and are not keys. They are also the reason
# this file is a little awkward to read: the repository's own hooks scan it.
#
#   * `# pragma: allowlist secret` is how detect-secrets is told that a line
#     was written on purpose. Every line that carries one is a test input.
#   * The private-key markers are built from two pieces because
#     `detect-private-key` is a plain regex with no pragma, and it is a hook
#     worth keeping absolute. Concatenation happens at import, so the bytes
#     the scanner sees are the real marker, and the test is no weaker for it.
#
# Nothing here is exempted from a hook; the fixtures are shaped to satisfy
# them, which is the way round that keeps the hooks honest.
RSA_MARKER = b"-----BEGIN RSA PRIVATE" + b" KEY-----"
OPENSSH_MARKER = b"-----BEGIN OPENSSH PRIVATE" + b" KEY-----"


@pytest.mark.parametrize("text, expected", [
    # Key shapes that must be caught.
    (b"sk-ant-api03-ZmFrZWJ1dGxvb2tzcmVhbA-AAbbCCddEEff", "anthropic key"),  # pragma: allowlist secret
    (b"AKIAZZ47TKQW3MNPRQXB", "aws access key"),  # pragma: allowlist secret
    (b"ghp_aB3dEfGh1JkLmN0pQrStUvWxYz0123456789", "github token"),  # pragma: allowlist secret
    (b"xoxb-1234567890-abcdefghijkl", "slack token"),  # pragma: allowlist secret
    (RSA_MARKER, "private key"),
    (OPENSSH_MARKER, "private key"),
    # The repository's own test fixtures, which say in the string that they
    # are fixtures. These must stay quiet or the scanner cries wolf forever.
    (b"sk-or-v1-0123456789abcdefSECRETtail9876", None),  # pragma: allowlist secret
    (b"AKIAIOSFODNN7EXAMPLE", None),  # pragma: allowlist secret
    # Ordinary text must not trip it.
    (b"just some prose mentioning an sk thing and a key", None),
    (b"https://openrouter.ai/api/v1", None),
])
def test_detects_key_shapes_but_not_fixtures(text: bytes, expected: str | None):
    assert _detect(text) == expected


def test_this_repository_is_clean():
    """The scanner, on this repository, end to end.

    Skipped rather than failed on a shallow clone, where most of history was
    never fetched and a pass would mean nothing."""
    import subprocess

    shallow = subprocess.run(["git", "rev-parse", "--is-shallow-repository"],
                             capture_output=True, text=True)
    if shallow.stdout.strip() == "true":
        pytest.skip("shallow clone: run this on a full clone (CI does)")
    assert scan.main() == 0, "a key-shaped string is in this repository's history"
