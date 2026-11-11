"""API keys entered in the editor: stored, looked up, masked, never shown.

A key from the environment ALWAYS wins over one stored here. Run as a service,
that means the operator's key is used when there is one, and a user's own key
only when there is not.

Stored keys live per user, outside every album folder (so an album can be
zipped and shared), in a file only that user can read:

    $ALBUM_CONFIG_DIR/keys.json         (default ~/.config/impaginai)
    {"openrouter": "sk-or-...", "anthropic": "sk-ant-..."}

Nothing in this module returns a key to a caller that displays things: the
editor gets `status()` (where a key comes from and its masked form), and every
log line and error that could carry one goes through `redact()`.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

# Slot -> (label, environment variables that win over a stored key, in order).
SLOTS: dict[str, tuple[str, tuple[str, ...]]] = {
    "anthropic": ("Anthropic", ("ALBUM_API_KEY", "ANTHROPIC_API_KEY")),
    "openrouter": ("OpenRouter", ("ALBUM_API_KEY", "OPENROUTER_API_KEY")),
    "openai": ("OpenAI", ("ALBUM_API_KEY", "OPENAI_API_KEY")),
    "together": ("Together", ("ALBUM_API_KEY", "TOGETHER_API_KEY")),
    "groq": ("Groq", ("ALBUM_API_KEY", "GROQ_API_KEY")),
    "other": ("Other endpoint", ("ALBUM_API_KEY",)),
}

_EXTRA: set[str] = set()      # keys seen at runtime (e.g. --api-key), for redact()


APP_DIR = "impaginai"
OLD_APP_DIR = "photo_album_maker"      # the project's name before the rename


def config_dir() -> Path:
    d = os.environ.get("ALBUM_CONFIG_DIR")
    if d:
        return Path(d)
    base = Path.home() / ".config"
    new, old = base / APP_DIR, base / OLD_APP_DIR
    if not new.exists() and old.is_dir():
        # Carry stored keys and recent albums over from the old name, once.
        # If the move is refused, keep using the old folder rather than
        # start empty and make the keys look lost.
        try:
            old.rename(new)
        except OSError:
            return old
    return new


def _path() -> Path:
    return config_dir() / "keys.json"


def slot_for(provider: str, base_url: str | None) -> str:
    """Which stored key a provider/endpoint uses."""
    url = (base_url or "").lower()
    host = host_of(url)
    if provider == "anthropic" and (not url or _is(host, "anthropic.com")):
        return "anthropic"
    for s, domain in KEY_HOSTS.items():
        if _is(host, domain):
            return s
    if not url:
        return "openai"
    return "other"


# Which host each named key belongs to. Matched on the URL's host name, never
# on the text of the URL: `https://evil.example/api.openai.com` contains the
# right words and must not be handed the OpenAI key.
KEY_HOSTS = {"openrouter": "openrouter.ai", "together": "together.xyz",
             "groq": "groq.com", "openai": "openai.com"}


def host_of(url: str | None) -> str:
    from urllib.parse import urlparse

    try:
        return (urlparse(url or "").hostname or "").lower()
    except ValueError:
        return ""


def origin_of(url: str | None) -> str:
    """scheme://host:port of an endpoint: what a key is trusted to, exactly.
    The port counts — another program on this machine is another endpoint."""
    from urllib.parse import urlparse

    try:
        u = urlparse(url or "")
        port = u.port or {"http": 80, "https": 443}.get(u.scheme, 0)
        return f"{u.scheme}://{(u.hostname or '').lower()}:{port}"
    except ValueError:
        return ""


def _is(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def _load() -> dict[str, str]:
    try:
        raw = json.loads(_path().read_text())
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in raw.items() if k in SLOTS and isinstance(v, str) and v}


def stored(slot: str) -> str | None:
    return _load().get(slot)


def set_key(slot: str, key: str) -> None:
    if slot not in SLOTS:
        raise ValueError(f"unknown key slot {slot!r}")
    key = (key or "").strip()
    if not key:
        raise ValueError("the key is empty")
    if any(c.isspace() for c in key) or len(key) > 512:
        raise ValueError("that does not look like an API key")
    data = _load()
    data[slot] = key
    _write(data)


def delete_key(slot: str) -> None:
    data = _load()
    if data.pop(slot, None) is not None:
        _write(data)


def _write(data: dict) -> None:
    d = config_dir()
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    p = _path()
    tmp = p.with_suffix(".tmp")
    # Created 0600 from the start: never readable by anyone else, not even
    # for the moment between writing and chmod.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh)
    os.replace(tmp, p)
    os.chmod(p, 0o600)


def env_key(slot: str) -> tuple[str | None, str | None]:
    """(key, variable name) from the environment, or (None, None)."""
    for var in SLOTS.get(slot, SLOTS["other"])[1]:
        v = os.environ.get(var)
        if v:
            return v, var
    return None, None


def lookup(provider: str, base_url: str | None) -> str | None:
    """The key for this endpoint: environment first, then the stored one."""
    slot = slot_for(provider, base_url)
    k, _ = env_key(slot)
    return k or stored(slot)


def mask(key: str | None) -> str:
    """`sk-or-v1-…a1b2`: enough to tell two keys apart, never enough to use one."""
    if not key:
        return ""
    if len(key) < 12:
        return "…" + key[-2:]
    m = re.match(r"^([a-zA-Z]{2,5}[-_](?:[a-zA-Z0-9]{1,4}[-_])?)", key)
    head = m.group(1) if m else key[:3]
    return f"{head[:8]}…{key[-4:]}"


def status(slot: str) -> dict:
    """Where this slot's key comes from — never the key itself."""
    k, var = env_key(slot)
    if k:
        return {"slot": slot, "label": SLOTS[slot][0], "source": "env", "env_var": var,
                "masked": mask(k), "stored": bool(stored(slot))}
    s = stored(slot)
    return {"slot": slot, "label": SLOTS[slot][0], "source": "user" if s else "missing",
            "env_var": SLOTS[slot][1][-1], "masked": mask(s), "stored": bool(s)}


def remember(key: str | None) -> None:
    """A key that reached the process another way (a flag, models.yaml):
    redact() hides it as well."""
    if key and len(key) >= 8:
        _EXTRA.add(key)


def _known() -> list[str]:
    keys = set(_EXTRA)
    for _, envs in SLOTS.values():
        for var in envs:
            v = os.environ.get(var)
            if v and len(v) >= 8:
                keys.add(v)
    keys.update(v for v in _load().values() if len(v) >= 8)
    return sorted(keys, key=len, reverse=True)


def redact(text):
    """Every known key in `text` replaced by its masked form."""
    if not isinstance(text, str) or not text:
        return text
    for k in _known():
        if k in text:
            text = text.replace(k, mask(k))
    return text


class RedactingStream:
    """Wraps stdout/stderr so nothing printed — a log line, a traceback —
    can carry a key to the terminal or a service's log collector."""

    def __init__(self, stream):
        self._s = stream

    def write(self, s):
        return self._s.write(redact(s))

    def __getattr__(self, name):
        return getattr(self._s, name)
