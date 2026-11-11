"""Which model answers, and over which wire protocol.

The planner never knew what it was talking to: its one requirement is a
`Completer`, a function taking (system prompt, messages, **opts) and returning
a `planner.Reply` (text, usage, and why it stopped); a bare (text, usage) tuple
is still accepted by the planner. What was missing was a second implementation. There is nothing
Anthropic-specific about sequencing a photo album, and binding a self-hosted
project to one vendor's endpoint is a poor default — especially when the
machine running it may already have a model on it.

Two protocols cover essentially everything:

- **anthropic** — the Messages API.
- **openai** — the `/chat/completions` contract, which is what OpenRouter,
  OpenAI, Together, Groq, vLLM, Ollama, LM Studio and llama.cpp's server all
  speak. Point `--base-url` at any of them.

Messages are held internally in the Anthropic block shape and translated on the
way out, because that shape distinguishes text from images explicitly and
survives round-tripping; flattening to a string would lose the image blocks the
annotate pass depends on.

Choosing a provider, in order of precedence: command-line flags, then
environment, then a sensible default.

    ALBUM_PROVIDER   anthropic | openai
    ALBUM_MODEL      model identifier as that provider names it
    ALBUM_BASE_URL   endpoint root, e.g. https://openrouter.ai/api/v1
    ALBUM_API_KEY    key; falls back to ANTHROPIC_API_KEY / OPENAI_API_KEY /
                     OPENROUTER_API_KEY depending on the provider, and then to
                     a key entered in the editor (keystore.py) — an environment
                     key always wins
    ALBUM_VISION     "0" to declare the model cannot see images
    ALBUM_MAX_TOKENS     output budget per call (default 16000)
    ALBUM_REASONING      default | low | off | high — how much a reasoning model may think
    ALBUM_FALLBACK_MODEL second model for the same role, tried only after the
                         first has clearly failed (ALBUM_FALLBACK_BASE_URL if it
                         lives on another endpoint)
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
from dataclasses import dataclass, field

DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-5",
    "openai": "gpt-4o-mini",
}

# Endpoints people actually point this at, so --base-url can be a short name.
ALIASES = {
    "openrouter": "https://openrouter.ai/api/v1",
    "openai": "https://api.openai.com/v1",
    "together": "https://api.together.xyz/v1",
    "groq": "https://api.groq.com/openai/v1",
    "ollama": "http://localhost:11434/v1",
    "lmstudio": "http://localhost:1234/v1",
    "vllm": "http://localhost:8000/v1",
    "llamacpp": "http://localhost:8080/v1",
}

# Endpoints that are local and therefore need no key at all.
LOCAL_HINTS = ("localhost", "127.0.0.1", "0.0.0.0", "::1")


@dataclass
class Provider:
    name: str = "anthropic"
    model: str = DEFAULT_MODELS["anthropic"]
    base_url: str | None = None
    api_key: str | None = None
    vision: bool = True
    json_mode: bool = False          # ask the endpoint to guarantee JSON
    max_tokens: int = 16000
    extra_headers: dict = field(default_factory=dict)
    # None sends nothing and lets the model decide; "low" / "off" turn thinking
    # down. Reasoning models can spend the whole output budget thinking and
    # return an empty reply, so this is a setting rather than a hard-coded retry.
    reasoning: str | None = None
    # A second model for the same role. The planner switches to it only after
    # the primary has clearly failed, never to save money or to second-guess an
    # answer. Another vendor is the point: an outage rarely hits two at once.
    fallback_model: str | None = None
    fallback_base_url: str | None = None   # default: the primary's endpoint
    # USD per million tokens, when the list price table below does not know
    # the model (models.yaml: price_in / price_out, or ALBUM_PRICE_IN/OUT).
    price_in: float | None = None
    price_out: float | None = None

    @property
    def is_local(self) -> bool:
        return bool(self.base_url) and any(h in self.base_url for h in LOCAL_HINTS)

    @property
    def usable(self) -> bool:
        """A local endpoint needs no key; a hosted one does."""
        return bool(self.api_key) or self.is_local

    def describe(self, mention_vision: bool = True) -> str:
        where = self.base_url or ("api.anthropic.com" if self.name == "anthropic"
                                  else ALIASES["openai"])
        bits = [f"{self.model} via {where}"]
        if mention_vision and not self.vision:
            bits.append("no vision")
        if not self.usable:
            bits.append("NO API KEY")
        if self.reasoning:
            bits.append(f"reasoning {self.reasoning}")
        if self.fallback_model:
            bits.append(f"fallback {self.fallback_model}")
        return " · ".join(bits)


def _reasoning(v) -> str | None:
    """Normalise a reasoning level. "default" and empty both mean "send nothing"."""
    if v is None or v is True:
        return None
    if v is False:
        # YAML 1.1 reads an unquoted `off` as the boolean False.
        return "off"
    level = str(v).strip().lower()
    if level in ("", "default"):
        return None
    if level not in ("low", "off", "high"):
        raise SystemExit(f"unknown reasoning level {v!r}; use 'default', 'low', 'off' or 'high'")
    return level


# Thinking hard is billed as output, so "high" needs room to think and still
# write the whole layout.
HIGH_MIN_TOKENS = 32000


def _max_tokens(v) -> int | None:
    if v is None or v == "":
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise SystemExit(f"max_tokens must be a whole number, got {v!r}")
    if n <= 0:
        raise SystemExit(f"max_tokens must be positive, got {n}")
    return n


def resolve(provider=None, model=None, base_url=None, api_key=None,
            vision=None, json_mode=None, max_tokens=None, reasoning=None,
            fallback_model=None, fallback_base_url=None) -> Provider:
    name = (provider or os.environ.get("ALBUM_PROVIDER") or "anthropic").lower()
    if name not in ("anthropic", "openai"):
        raise SystemExit(f"unknown provider {name!r}; use 'anthropic' or 'openai'")

    url = base_url or os.environ.get("ALBUM_BASE_URL")
    if url:
        url = ALIASES.get(url.lower(), url).rstrip("/")
        # Pointing at an OpenAI-compatible endpoint implies the openai protocol,
        # which is almost certainly what was meant.
        if provider is None and not os.environ.get("ALBUM_PROVIDER"):
            name = "openai"

    mdl = model or os.environ.get("ALBUM_MODEL") or DEFAULT_MODELS[name]

    import keystore

    key = api_key or os.environ.get("ALBUM_API_KEY")
    if not key:
        for env in (("ANTHROPIC_API_KEY",) if name == "anthropic"
                    else ("OPENROUTER_API_KEY", "OPENAI_API_KEY")):
            if os.environ.get(env):
                key = os.environ[env]
                break
    if not key:
        # A key the user entered in the editor: only when the environment has
        # none, so an operator's key always wins.
        key = keystore.lookup(name, url)
    keystore.remember(key)

    see = vision
    if see is None:
        see = os.environ.get("ALBUM_VISION", "1") not in ("0", "false", "no")

    jm = json_mode
    if jm is None:
        jm = os.environ.get("ALBUM_JSON_MODE", "0") in ("1", "true", "yes")

    headers = {}
    if url and "openrouter" in url:
        # OpenRouter attributes traffic by these; harmless elsewhere.
        headers = {"HTTP-Referer": "https://github.com/zioalex/impaginai",
                   "X-Title": "ImpaginAI"}
    mt = _max_tokens(max_tokens if max_tokens is not None
                     else os.environ.get("ALBUM_MAX_TOKENS"))
    rs = _reasoning(reasoning if reasoning is not None
                    else os.environ.get("ALBUM_REASONING"))
    fb = fallback_model or os.environ.get("ALBUM_FALLBACK_MODEL") or None
    fb_url = fallback_base_url or os.environ.get("ALBUM_FALLBACK_BASE_URL") or None
    if rs == "high":
        mt = max(mt or 0, HIGH_MIN_TOKENS)
    return Provider(name, mdl, url, key, see, jm, max_tokens=mt or 16000,
                    extra_headers=headers, reasoning=rs,
                    fallback_model=fb, fallback_base_url=fb_url)


def fallback_provider(p: Provider) -> Provider | None:
    """The provider to switch to when `p` has clearly failed, or None.

    Same model-and-endpoint resolution as the primary, so a short alias works
    and the protocol is inferred the same way. The key is looked up for the
    *new* endpoint: reusing the primary's would send an OpenRouter key to
    Ollama, or the reverse. Only when the endpoint is unchanged is it shared.
    """
    if not p.fallback_model:
        return None
    url = p.fallback_base_url
    if url:
        url = ALIASES.get(url.lower(), url).rstrip("/")
    same_endpoint = not url or url == p.base_url
    q = resolve(
        # Keep the protocol unless the endpoint moved, where it is re-inferred.
        provider=p.name if same_endpoint else None,
        model=p.fallback_model,
        base_url=(p.base_url if same_endpoint else url),
        api_key=(p.api_key if same_endpoint else None),
        vision=p.vision,
        json_mode=p.json_mode,
        max_tokens=p.max_tokens,
        reasoning=p.reasoning or "default",
    )
    q.fallback_model = q.fallback_base_url = None
    return q


# --- message translation -------------------------------------------------


def _to_openai(system: str, messages: list[dict]) -> list[dict]:
    """Anthropic blocks -> OpenAI chat messages.

    Images become data URIs, which is what every OpenAI-compatible vision
    endpoint accepts; there is no upload step to get wrong.
    """
    out = [{"role": "system", "content": system}]
    for m in messages:
        content = m["content"]
        if isinstance(content, str):
            out.append({"role": m["role"], "content": content})
            continue
        parts = []
        for b in content:
            if b.get("type") == "text":
                parts.append({"type": "text", "text": b["text"]})
            elif b.get("type") == "image":
                src = b["source"]
                parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{src['media_type']};base64,{src['data']}"},
                })
        out.append({"role": m["role"], "content": parts})
    return out


def _strip_images(messages: list[dict]) -> list[dict]:
    """Drop image blocks for a text-only model, keeping the text."""
    out = []
    for m in messages:
        c = m["content"]
        if isinstance(c, str):
            out.append(m)
            continue
        kept = [b for b in c if b.get("type") != "image"]
        out.append({**m, "content": kept or [{"type": "text", "text": ""}]})
    return out


def count_images(messages: list[dict]) -> int:
    return sum(
        1 for m in messages if isinstance(m["content"], list)
        for b in m["content"] if isinstance(b, dict) and b.get("type") == "image"
    )


# --- what a call costs ---------------------------------------------------
#
# List prices in USD per million tokens (input, output), matched by substring
# of the model id, most specific first. They are what the GUI's cost line is
# made of, so keep them current; an endpoint that reports the cost itself
# (OpenRouter's `usage.cost`) always wins over this table, and a local model
# costs nothing.
PRICES: list[tuple[str, float, float]] = [
    # Current Claude models (list prices, USD per million tokens). The ids
    # appear as `claude-sonnet-5-5` (Anthropic) and `claude-sonnet-5.5`
    # (OpenRouter), so both spellings are listed.
    ("haiku-5-5", 0.10, 0.50), ("haiku-5.5", 0.10, 0.50),
    ("sonnet-5", 2.0, 10.0),
    ("opus-5-5", 4.0, 20.0), ("opus-5.5", 4.0, 20.0),
    ("opus-5", 5.0, 25.0),
    ("fable-5-2", 8.0, 40.0), ("fable-5.2", 8.0, 40.0),
    ("fable-5", 10.0, 50.0),
    ("opus-4-8", 5.0, 25.0), ("opus-4.8", 5.0, 25.0),
    ("haiku-3", 0.25, 1.25),
    ("haiku", 1.0, 5.0),
    ("opus-4-1", 15.0, 75.0),
    ("opus-4-0", 15.0, 75.0),
    ("opus-4-2025", 15.0, 75.0),
    ("opus", 5.0, 25.0),
    ("sonnet", 3.0, 15.0),
    ("fable", 3.0, 15.0),
    ("gpt-4o-mini", 0.15, 0.6),
    ("gpt-4o", 2.5, 10.0),
    ("gpt-4.1-mini", 0.4, 1.6),
    ("gpt-4.1", 2.0, 8.0),
    ("gemini-2.5-flash", 0.3, 2.5),
    ("gemini-2.5-pro", 1.25, 10.0),
]


def rate_for(p: Provider) -> tuple[tuple[float, float], bool]:
    """(USD per million input, per million output) for this provider, and
    whether that is known rather than guessed. Local models cost nothing."""
    if p.is_local:
        return (0.0, 0.0), True
    if p.price_in is not None and p.price_out is not None:
        return (float(p.price_in), float(p.price_out)), True
    m = (p.model or "").lower()
    for key, i, o in PRICES:
        if key in m:
            return (i, o), True
    # Unknown model: an honest guess at a mid-range rate, marked as such.
    return (3.0, 15.0), False


def price(p: Provider, usage) -> None:
    """Fill usage.cost_usd for one call made through provider `p`, unless the
    endpoint already reported it."""
    if usage.cost_usd is not None:
        return
    if p.is_local:
        usage.cost_usd = 0.0
        return
    rate, known = rate_for(p)
    if not known:
        usage.estimated = True
    usage.cost_usd = usage.input_tokens / 1e6 * rate[0] + usage.output_tokens / 1e6 * rate[1]


# --- completers ----------------------------------------------------------


def make_completer(p: Provider):
    """Return a Completer for this provider. Import errors surface here, once."""
    if not p.usable:
        raise SystemExit(
            f"no API key for provider '{p.name}'. Set ALBUM_API_KEY, or "
            + ("ANTHROPIC_API_KEY" if p.name == "anthropic" else "OPENAI_API_KEY / OPENROUTER_API_KEY")
            + ", or point --base-url at a local endpoint that needs none."
        )
    inner = _anthropic(p) if p.name == "anthropic" else _openai(p)

    def complete(system: str, messages: list[dict], **opts):
        # The provider's settings are the defaults; the planner's per-call opts
        # (it lowers reasoning and raises the budget to recover an empty reply)
        # win. "default" means "send nothing", the same as unset.
        if opts.get("reasoning") is None:
            opts["reasoning"] = p.reasoning
        elif opts["reasoning"] == "default":
            opts["reasoning"] = None
        if not opts.get("max_tokens"):
            opts["max_tokens"] = p.max_tokens
        reply = inner(system, messages, **opts)
        try:
            usage = reply.usage if hasattr(reply, "usage") else reply[1]
            price(p, usage)
        except Exception:
            pass
        return reply

    return complete


def make_fallback_completer(p: Provider):
    """A Completer for the role's fallback model, or None if it has none."""
    q = fallback_provider(p)
    return make_completer(q) if q is not None else None


@dataclass
class Preflight:
    ok: bool
    message: str
    seconds: float
    finish: str | None = None


def preflight(p: Provider, timeout: float = 60.0) -> Preflight:
    """One tiny request, to learn at startup that the model cannot answer.

    Finding out after a minute of planning that the key is wrong, the model
    name is a typo or the thinking budget swallows every reply is the worst
    moment to learn it. This costs one call of about a thousand tokens, does not
    use the layout schema (it only has to prove the endpoint answers), and
    never raises: the verdict is a one-line sentence naming the likely cause.
    A transient failure is reported but not treated as final, because the
    endpoint may simply be busy.
    """
    t0 = time.monotonic()

    def done(ok, message, finish=None):
        return Preflight(ok, " ".join(str(message).split())[:300],
                         round(time.monotonic() - t0, 2), finish)

    try:
        complete = make_completer(p)
    except (Exception, SystemExit) as e:
        text = str(e) or type(e).__name__
        if text.startswith("no API key"):
            text = f"no API key for {p.model} — set the key, or point at a local endpoint"
        return done(False, text)

    try:
        from planner import TransientError
    except ImportError:          # pragma: no cover - planner is always importable
        TransientError = ()

    try:
        reply = complete("Reply with JSON only.", [{"role": "user",
                         "content": 'Return exactly {"ok": true}'}],
                         max_tokens=1024, plain=True, timeout=timeout)
    except TransientError as e:
        return done(False, f"{p.model}: endpoint unreachable or busy ({str(e)[:140]}) "
                           f"— it may recover; try again in a moment")
    except Exception as e:
        status = getattr(e, "status_code", None)
        if not isinstance(status, int):
            m = re.match(r"\s*(\d{3}) from", str(e))
            status = int(m.group(1)) if m else None
        detail = _error_text(str(e))[:160]
        if status in (401, 403):
            return done(False, f"{status}: the API key was rejected for {p.model} "
                               f"— check it was exported in this shell")
        if status == 404 or re.search(r"model.{0,40}(not found|does not exist|unknown)",
                                      str(e), re.I):
            return done(False, f"unknown model {p.model!r} or wrong endpoint (404) — "
                               f"check the name: {detail}")
        return done(False, f"{p.model}: {type(e).__name__}: {detail}")

    text = getattr(reply, "text", None)
    if text is None and isinstance(reply, tuple):
        text = reply[0]
    finish = getattr(reply, "finish", None)
    refusal = getattr(reply, "refusal", None)
    if finish == "refusal" or refusal:
        return done(False, f"{p.model} refused even a trivial request"
                           + (f": {refusal[:120]}" if refusal else ""), finish)
    if isinstance(text, str) and "{" in text:
        return done(True, f"{p.model} answered", finish)
    if finish == "length":
        return done(False, f"{p.model} spent its whole output budget thinking and "
                           f"wrote nothing — set reasoning: low in models.yaml", finish)
    if not (text or "").strip():
        return done(False, f"{p.model} returned an empty reply (finish={finish}) "
                           f"— try reasoning: low, or another model", finish)
    return done(False, f"{p.model} answered, but not with JSON: {text.strip()[:80]!r}", finish)


def _usage(inp: int, out: int, imgs: int):
    from planner import Usage

    return Usage(inp, out, 1, imgs)


# Finish reasons, normalised. The planner decides what to do about an empty or
# cut-off reply from these, so each provider's own vocabulary is mapped onto one.
_OPENAI_FINISH = {"stop": "stop", "tool_calls": "stop", "function_call": "stop",
                  "length": "length", "content_filter": "filter", "error": "error"}
_ANTHROPIC_FINISH = {"end_turn": "stop", "stop_sequence": "stop",
                     "max_tokens": "length", "refusal": "refusal"}


def _anthropic(p: Provider):
    try:
        from anthropic import Anthropic
    except ImportError:
        raise SystemExit("pip install anthropic")

    # Imported one by one: which of these exist varies between SDK versions, and
    # a missing name must not take the whole provider down.
    import anthropic as _sdk

    transient_types = tuple(
        t for t in (getattr(_sdk, n, None) for n in (
            "RateLimitError", "InternalServerError", "APIConnectionError",
            "APITimeoutError")) if isinstance(t, type))
    status_error = getattr(_sdk, "APIStatusError", None)

    client = Anthropic(api_key=p.api_key, **({"base_url": p.base_url} if p.base_url else {}))

    def retry_after(exc) -> float | None:
        try:
            return float(exc.response.headers.get("retry-after"))
        except (AttributeError, TypeError, ValueError):
            return None

    def complete(system: str, messages: list[dict], **opts):
        from planner import Reply, TransientError

        msgs = messages if p.vision else _strip_images(messages)
        level = opts.get("reasoning", p.reasoning)
        extra = {}
        if level == "high":
            # Effort is the depth control on current Claude models (thinking
            # is adaptive). Sent as a raw body field so it works with any SDK
            # version; a long budget needs an explicit timeout, or the SDK
            # refuses a non-streamed call it expects to run past ten minutes.
            extra = {"extra_body": {"output_config": {"effort": "high"}},
                     "timeout": opts.get("timeout") or 900}
        elif opts.get("timeout"):
            extra = {"timeout": opts["timeout"]}

        def call(**kw):
            return client.messages.create(
                model=p.model, max_tokens=opts.get("max_tokens") or p.max_tokens,
                system=system, messages=msgs, **kw)
        try:
            try:
                resp = call(**extra)
            except Exception as e:
                # An older model that does not know `effort`: ask again without.
                if ("extra_body" in extra and getattr(e, "status_code", None) == 400
                        and re.search(r"effort|output_config", str(e))):
                    extra.pop("extra_body")
                    resp = call(**extra)
                else:
                    raise
        except Exception as e:
            # Overload (529) is not a subclass of anything we can name portably,
            # so it is recognised by status as well as by type.
            status = getattr(e, "status_code", None)
            if (isinstance(e, transient_types)
                    or (status_error is not None and isinstance(e, status_error)
                        and isinstance(status, int) and (status >= 500 or status in (408, 429)))):
                raise TransientError(str(e), retry_after(e)) from e
            raise
        text = "".join(b.text for b in resp.content if b.type == "text")
        # Thinking blocks are only returned if something enabled them, but when
        # they are present they are what explains an empty `text`.
        thinking = sum(len(getattr(b, "thinking", "") or "")
                       for b in resp.content if b.type == "thinking")
        raw_finish = getattr(resp, "stop_reason", None)
        try:
            raw = resp.model_dump()
        except Exception:
            raw = None
        return Reply(
            text=text,
            usage=_usage(resp.usage.input_tokens, resp.usage.output_tokens,
                         count_images(msgs)),
            finish=_ANTHROPIC_FINISH.get(raw_finish),
            raw_finish=raw_finish,
            reasoning_chars=thinking,
            raw=raw,
        )

    return complete


_SCHEMA_CACHE: dict = {}


# Keywords that are schema annotations rather than constraints. Stripped only
# where they are keywords — never inside a `properties` map, where the same
# words are legitimate field names. ("title" is both a JSON Schema annotation
# and a field of this project's `meta` object; an earlier version of this
# function deleted the album title from the schema because of that.)
_DROP_KEYWORDS = {"$defs", "default", "title", "discriminator", "examples",
                  # Value constraints. Anthropic's structured output rejects
                  # every one of these ("numerical constraints", "string
                  # length", "maxItems"), others ignore them, and Pydantic
                  # enforces them after decoding anyway.
                  "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
                  "multipleOf", "minLength", "maxLength", "maxItems",
                  "minProperties", "maxProperties"}

# Keys whose values are maps of name -> schema, not schemas themselves.
_SCHEMA_MAPS = {"properties", "patternProperties", "$defs", "definitions"}


def simplify_schema(node, defs=None):
    """Reduce a Pydantic JSON Schema to the subset providers actually accept.

    Structured-output support is nothing like uniform. Pydantic emits valid
    JSON Schema 2020-12 and providers implement varying fragments of it:

      prefixItems   tuple[float, float] becomes prefixItems, which Anthropic
                    served through Google Vertex rejects outright ("For
                    'array' type, property 'prefixItems' is not supported").
                    Rewritten as `items` plus minItems/maxItems, which means
                    the same thing everywhere.
      $ref / $defs  inlined, because several endpoints do not resolve them.
      default       ignored by some, rejected by others; dropped.
      minimum, maxItems, minLength, ...
                    Anthropic rejects numeric and length constraints and any
                    minItems other than 0 or 1. Dropped; minItems above 1
                    is dropped too.
      additionalProperties: true
                    Anthropic only accepts `false`. An open map is closed
                    over the properties it declares (none, for a bare dict),
                    which _layout_schema then fills in where it matters.

    The result constrains less than the original — a tuple becomes "an array
    of two numbers" rather than "exactly these two" — but the Pydantic model
    still validates the response afterwards, so nothing is really loosened.
    This only steers decoding.
    """
    if defs is None:
        defs = node.get("$defs", {}) if isinstance(node, dict) else {}

    if isinstance(node, list):
        return [simplify_schema(x, defs) for x in node]
    if not isinstance(node, dict):
        return node

    if "$ref" in node:
        name = node["$ref"].rsplit("/", 1)[-1]
        target = defs.get(name)
        if target is None:
            return {"type": "object"}
        return simplify_schema({k: v for k, v in target.items() if k != "$defs"}, defs)

    out: dict = {}
    for k, v in node.items():
        if k in _DROP_KEYWORDS:
            continue
        if k in _SCHEMA_MAPS:
            # Field names, not keywords: recurse into the values only.
            out[k] = {name: simplify_schema(sub, defs) for name, sub in v.items()}
            continue
        if k == "prefixItems":
            items = [simplify_schema(x, defs) for x in v]
            types = {i.get("type") for i in items if isinstance(i, dict)}
            out["items"] = items[0] if len(types) == 1 and items else {}
            continue
        if k == "minItems":
            if isinstance(v, int) and v <= 1:
                out[k] = v
            continue
        if k == "additionalProperties" and v is not False:
            out[k] = False
            continue
        out[k] = simplify_schema(v, defs)
    return out


def _require_all(node, keep_optional: set[str]) -> None:
    """Mark every property required except those named in `keep_optional`.

    Already-required properties stay required whatever their name (meta.title
    is required even though "title" is in the optional set elsewhere).
    Nullable fields stay nullable, so "required" only means the model writes
    `null` instead of leaving the key out — which the prompt's JSON sketch
    already shows it doing.
    """
    if isinstance(node, list):
        for x in node:
            _require_all(x, keep_optional)
        return
    if not isinstance(node, dict):
        return
    props = node.get("properties")
    if isinstance(props, dict):
        already = set(node.get("required", []))
        node["required"] = [k for k in props if k in already or k not in keep_optional]
        for sub in props.values():
            _require_all(sub, keep_optional)
    for k, v in node.items():
        if k != "properties":
            _require_all(v, keep_optional)


def _drop_optional_nulls(node) -> None:
    """For properties that are not required, `anyOf: [X, null]` becomes `X`."""
    if isinstance(node, list):
        for x in node:
            _drop_optional_nulls(x)
        return
    if not isinstance(node, dict):
        return
    props = node.get("properties")
    if isinstance(props, dict):
        required = set(node.get("required", []))
        for name, sub in props.items():
            if name in required or not isinstance(sub, dict):
                continue
            alts = sub.get("anyOf")
            if isinstance(alts, list):
                rest = [a for a in alts if not (isinstance(a, dict) and a.get("type") == "null")]
                if len(rest) == 1 and len(rest) < len(alts):
                    keep = {k: v for k, v in sub.items() if k != "anyOf"}
                    keep.update(rest[0])
                    props[name] = keep
            elif isinstance(sub.get("type"), list) and "null" in sub["type"]:
                t = [x for x in sub["type"] if x != "null"]
                sub["type"] = t[0] if len(t) == 1 else t
    for v in node.values():
        _drop_optional_nulls(v)


def union_count(schema) -> int:
    """How many parameters a schema types as a union (anyOf or a type array)."""
    n = 0
    if isinstance(schema, dict):
        if "anyOf" in schema or isinstance(schema.get("type"), list):
            n += 1
        for v in schema.values():
            n += union_count(v)
    elif isinstance(schema, list):
        for v in schema:
            n += union_count(v)
    return n


def _layout_schema():
    """The layout JSON schema, for endpoints that constrain decoding to it.

    Only used for the planning and revision calls; the annotate pass returns a
    bare array and is left alone.
    """
    if "v" in _SCHEMA_CACHE:
        return _SCHEMA_CACHE["v"]
    try:
        from schema import Layout

        schema = simplify_schema(Layout.model_json_schema())
        # `options` is a free dict in the model, which a closed schema would
        # force to {}. The only option the planner is told about is `side`.
        spread = schema["properties"]["spreads"]["items"]
        spread["properties"]["options"] = {
            "type": "object",
            "properties": {"side": {"type": "string", "enum": ["left", "right"]}},
            "additionalProperties": False,
        }
        # Anthropic refuses more than 24 optional properties; the model has
        # 27. Require the ones real layouts always carry, and leave optional
        # what is genuinely absent much of the time: text on photo spreads,
        # slots on text spreads, captions, options, and `treatment`, which
        # only the user sets.
        _require_all(schema, keep_optional={
            "change_summary", "slots", "text", "options", "side", "caption",
            "caption_position", "treatment", "eyebrow", "title", "subtitle", "body"})
        # Anthropic also refuses more than 16 union-typed parameters (every
        # nullable field is one); the model has 17. An optional field can be
        # left out instead of set to null, so it need not be nullable.
        _drop_optional_nulls(schema)
        _SCHEMA_CACHE["v"] = schema
    except Exception:
        _SCHEMA_CACHE["v"] = None
    return _SCHEMA_CACHE["v"]


_WARNED: dict[str, bool] = {}

# What structured-output mode each endpoint turned out to accept. Without this
# the degradation is re-discovered on every call, which costs two wasted
# requests each time — on a multi-batch annotate pass that is most of the
# traffic.
_MODE_CACHE: dict[tuple, str] = {}


def _error_text(raw: str) -> str:
    """The innermost error message. OpenRouter wraps the upstream provider's
    JSON error as a string inside its own, so the useful part is otherwise
    cut off behind two layers of escaping."""
    msg = raw
    for _ in range(3):
        try:
            d = json.loads(msg)
        except (ValueError, TypeError):
            break
        err = d.get("error", d) if isinstance(d, dict) else {}
        if not isinstance(err, dict):
            break
        inner = (err.get("metadata") or {}).get("raw") or err.get("message")
        if not inner or inner == msg:
            break
        msg = inner
    return str(msg)


def _openai(p: Provider):
    import httpx

    url = (p.base_url or ALIASES["openai"]).rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json", **p.extra_headers}
    if p.api_key:
        headers["Authorization"] = f"Bearer {p.api_key}"

    def post(body: dict, timeout: float = 600.0):
        """One POST. Network-level failures are worth resending, so they are
        reported as such rather than as a crash."""
        from planner import TransientError

        try:
            return httpx.post(url, headers=headers, json=body, timeout=timeout)
        except (httpx.TimeoutException, httpx.TransportError) as e:
            raise TransientError(f"{type(e).__name__} talking to {url}: {e}") from e

    def complete(system: str, messages: list[dict], **opts):
        from planner import Reply, TransientError

        msgs = messages if p.vision else _strip_images(messages)
        timeout = opts.get("timeout") or 600.0
        body = {
            "model": p.model,
            "messages": _to_openai(system, msgs),
            "max_tokens": opts.get("max_tokens") or p.max_tokens,
        }
        if "openrouter" in (p.base_url or ""):
            body["usage"] = {"include": True}       # report the real cost

        # Reasoning models can spend the whole output budget thinking and write
        # nothing. The knob for turning that down has no standard spelling.
        level = opts.get("reasoning")
        base = p.base_url or ""
        if level in ("low", "off"):
            if "openrouter" in base:
                body["reasoning"] = ({"effort": "low"} if level == "low"
                                     else {"enabled": False})
            elif level == "low":
                body["reasoning_effort"] = "low"
            elif "localhost:11434" in base or "127.0.0.1:11434" in base:
                body["think"] = False
        elif level == "high":
            if "openrouter" in base:
                body["reasoning"] = {"effort": "high"}
            elif not ("localhost:11434" in base or "127.0.0.1:11434" in base):
                body["reasoning_effort"] = "high"

        # The annotate pass returns a bare array, which the layout schema does
        # not describe; detect it and leave json_mode off for that call.
        # `plain` is the startup check: it must not touch the layout schema or
        # the remembered structured-output mode of the real calls.
        skip_schema = system.startswith("You are a picture editor") or bool(opts.get("plain"))
        mode_key = (p.base_url, p.model)
        known = _MODE_CACHE.get(mode_key)
        if opts.get("json_fallback") and p.json_mode and not skip_schema:
            # The last reply under this mode came back empty. Step one level
            # down and remember it, so later calls do not walk into it again.
            current = known or ("json_schema" if _layout_schema() is not None
                                else "json_object")
            known = {"json_schema": "json_object"}.get(current, "none")
            _MODE_CACHE[mode_key] = known
        if skip_schema or known == "none":
            pass
        elif p.json_mode:
            # Prefer a real schema over bare JSON mode. OpenAI, vLLM, Ollama
            # and llama.cpp all constrain decoding to the grammar, which turns
            # "will this model emit valid JSON" from a gamble into a near
            # certainty — and that is what makes small local models usable as
            # planners at all. Endpoints that only understand json_object are
            # handled by the retry below.
            schema = None if known == "json_object" else _layout_schema()
            if schema is not None:
                body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": "album_layout", "strict": False,
                                    "schema": schema},
                }
            else:
                body["response_format"] = {"type": "json_object"}
        # Generous timeout: a local model on CPU is slow, and a planning call
        # is one request per album, not a hot path.
        r = post(body, timeout)

        def rejected(resp) -> bool:
            # Only a complaint about the request's *content* is worth answering
            # with a weaker request. A bad key (401/403), no credit (402), an
            # unknown model (404) or a rate limit (408/429) fails the same way
            # whatever the body says; retrying it with less structure would cost
            # two more calls and wrongly record that the model cannot do JSON.
            return (400 <= resp.status_code < 500
                    and resp.status_code not in (401, 402, 403, 404, 408, 429))

        # A reasoning field the endpoint does not know is rejected like any
        # other unknown parameter. Drop it and ask again, once; the answer is
        # worth more than the hint.
        hint_keys = [k for k in ("reasoning", "reasoning_effort", "think") if k in body]
        if hint_keys and rejected(r):
            for k in hint_keys:
                body.pop(k)
            r = post(body, timeout)

        # Structured output is the least portable part of the OpenAI contract.
        # Providers reject it for reasons that name their own internals rather
        # than the field we sent — OpenRouter relaying Vertex says
        # "output_config.format.schema", not "response_format" — so matching on
        # the error text fails exactly when it is needed. Any 4xx while a
        # response_format is in play is treated as a capability problem and the
        # request is retried with progressively weaker constraints.
        if rejected(r) and body.get("response_format"):
            first = _error_text(r.text)
            if body["response_format"].get("type") == "json_schema":
                body["response_format"] = {"type": "json_object"}
                r = post(body, timeout)
            if rejected(r):
                body.pop("response_format", None)
                r = post(body, timeout)
            if r.status_code < 400:
                # Remember the weaker mode only once it is known to work; a
                # request that still failed proves nothing about structure.
                _MODE_CACHE[mode_key] = (
                    body.get("response_format", {}).get("type") or "none")
            if r.status_code < 400 and not _WARNED.get(p.model):
                _WARNED[p.model] = True
                print(f"  note: {p.model} rejected structured output, continuing "
                      f"without it. Schema failures are still caught and retried.\n"
                      f"        ({first.strip()[:300]})")
        elif r.status_code < 400 and body.get("response_format"):
            _MODE_CACHE[mode_key] = body["response_format"]["type"]

        if r.status_code in (408, 429) or r.status_code >= 500:
            after = None
            try:
                after = float(r.headers.get("Retry-After"))
            except (TypeError, ValueError):
                pass
            raise TransientError(
                f"{r.status_code} from {url}: {_error_text(r.text)[:400]}", after)
        if r.status_code >= 400:
            raise RuntimeError(f"{r.status_code} from {url}: {r.text[:400]}")

        try:
            data = r.json()
        except ValueError:
            # A 200 that is not JSON is a proxy or gateway page, not an answer.
            raise TransientError(f"non-JSON 200 from {url}: {r.text[:200]!r}")
        if not isinstance(data, dict):
            raise TransientError(f"unexpected response shape: {json.dumps(data)[:400]}")

        # OpenRouter (and some gateways) report upstream failures as HTTP 200
        # with the error in the body, either at the top or inside the choice.
        if data.get("error"):
            raise TransientError(_error_text(json.dumps({"error": data["error"]})))
        choices = data.get("choices")
        if not choices:
            raise TransientError(f"response had no choices: {json.dumps(data)[:400]}")
        choice = choices[0]
        if choice.get("error"):
            raise TransientError(_error_text(json.dumps({"error": choice["error"]})))

        msg = choice.get("message") or {}
        content = msg.get("content")
        if isinstance(content, list):
            content = "".join(
                part.get("text") or "" for part in content
                if isinstance(part, dict) and isinstance(part.get("text"), str))
        text = content if isinstance(content, str) else ""
        # Some relays implement json_schema output by forcing a tool call, so
        # the layout arrives as the call's arguments and `content` is empty.
        # Reading only `content` turns a perfectly good answer into "empty".
        if not text.strip():
            for call in msg.get("tool_calls") or []:
                args = (call.get("function") or {}).get("arguments") if isinstance(call, dict) else None
                if isinstance(args, str) and args.strip():
                    text = args
                    break

        raw_finish = choice.get("finish_reason")
        finish = _OPENAI_FINISH.get(raw_finish)
        refusal = msg.get("refusal")
        refusal = refusal if isinstance(refusal, str) and refusal else None
        if refusal and not text:
            finish = "refusal"

        reasoning = msg.get("reasoning")
        if not isinstance(reasoning, str):
            reasoning = msg.get("reasoning_content")
        reasoning_chars = len(reasoning) if isinstance(reasoning, str) else 0
        if not reasoning_chars and isinstance(msg.get("reasoning_details"), list):
            reasoning_chars = sum(
                len(d.get("text") or d.get("summary") or "")
                for d in msg["reasoning_details"] if isinstance(d, dict))

        u = data.get("usage") or {}
        details = u.get("completion_tokens_details") or {}
        usage = _usage(u.get("prompt_tokens", 0), u.get("completion_tokens", 0),
                       count_images(msgs))
        if isinstance(u.get("cost"), (int, float)):
            usage.cost_usd = float(u["cost"])        # OpenRouter reports it
        return Reply(
            text=text,
            usage=usage,
            finish=finish,
            raw_finish=raw_finish,
            reasoning_chars=reasoning_chars,
            reasoning_tokens=int(details.get("reasoning_tokens") or 0),
            refusal=refusal,
            response_format=(body.get("response_format") or {}).get("type"),
            raw=data,
        )

    return complete


# --- roles ---------------------------------------------------------------
#
# The three calls are not the same job, and paying frontier prices for all of
# them is waste while running all of them locally costs you the book.
#
#   annotate  many short calls · vision REQUIRED · tiny JSON array out ·
#             errors are cheap (one soft annotation barely moves the layout) ·
#             cached, so it runs once per album
#             -> cheapest thing that can see and emit a JSON array. Local wins.
#
#   plan      one long call · no vision · must obey a ~9,000-character brief ·
#             1.4k-6k tokens of schema-valid JSON out · decides whether the
#             book is any good
#             -> the best instruction-follower you are willing to pay for.
#
#   revise    one call · no vision · has the whole layout in context · must
#             change what was asked and leave the rest alone
#             -> instruction-following again, but less invention than plan;
#                a mid-tier model is usually enough.

ROLES = ("annotate", "plan", "revise")

ROLE_NEEDS = {
    "annotate": "vision required, many cheap calls, cached after the first run",
    "plan": "no vision, must follow a long brief, decides the album's quality",
    "revise": "no vision, targeted edits to an existing layout",
}


def _role_env(role: str, field: str) -> str | None:
    """ALBUM_PLAN_MODEL overrides ALBUM_MODEL, and so on."""
    return os.environ.get(f"ALBUM_{role.upper()}_{field}") or os.environ.get(f"ALBUM_{field}")


def load_model_config(path) -> dict:
    """models.yaml — a `default` block plus optional per-role overrides.

        default:
          base_url: openrouter
          model: anthropic/claude-sonnet-5.5
        annotate:
          base_url: ollama
          model: qwen3-vl:32b
        plan:
          model: anthropic/claude-opus-5.5

    Keys match the flag names with underscores: provider, model, base_url,
    api_key, vision, json_mode, max_tokens, reasoning (default | low | off),
    fallback_model, fallback_base_url. A role inherits anything it does not set.
    """
    import yaml

    if path is None:
        return {}
    path = __import__("pathlib").Path(path)
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text()) or {}
    return {k: (v or {}) for k, v in raw.items() if isinstance(v, dict) or v is None}


def resolve_role(role: str, args=None, config: dict | None = None) -> Provider:
    """Which model answers for this role.

    Precedence, strongest first: a role-specific flag (--plan-model), a
    role-specific environment variable (ALBUM_PLAN_MODEL), the role's block in
    models.yaml, then the generic flag/env/default chain already in resolve().
    """
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}")
    cfg = dict((config or {}).get("default", {}))
    cfg.update((config or {}).get(role, {}))

    def pick(field, flag_name=None):
        if args is not None:
            v = getattr(args, f"{role}_{field}", None)
            if v not in (None, False):
                return v
        if (v := _role_env(role, field.upper())) is not None:
            return v
        if field in cfg:
            return cfg[field]
        return None

    vision = pick("vision")
    if isinstance(vision, str):
        vision = vision not in ("0", "false", "no")
    # Planning and revision never send images, so a text-only model is fine for
    # them whatever the config says. Only annotate genuinely needs to see.
    if role in ("plan", "revise"):
        vision = False

    jm = pick("json_mode")
    if isinstance(jm, str):
        jm = jm in ("1", "true", "yes")

    # Same chain as model/base_url, with the generic flag (--max-tokens,
    # --reasoning) as the last stop before the environment default.
    def pick_generic(field):
        v = pick(field)
        if v is None and args is not None:
            v = getattr(args, field, None)
        return v

    p = resolve(
        provider=pick("provider"),
        model=pick("model"),
        base_url=pick("base_url"),
        api_key=pick("api_key"),
        vision=vision,
        json_mode=jm,
        max_tokens=pick_generic("max_tokens"),
        reasoning=pick_generic("reasoning"),
        fallback_model=pick("fallback_model"),
        fallback_base_url=pick("fallback_base_url"),
    )
    for f in ("price_in", "price_out"):
        v = pick(f)
        if v is not None:
            try:
                setattr(p, f, float(v))
            except (TypeError, ValueError):
                pass
    # A generic flag should still win over a models.yaml default, so fall back
    # to the plain resolution for anything the role did not pin.
    if args is not None:
        generic = from_args(args)
        if pick("model") is None and getattr(args, "model", None):
            p.model = generic.model
        if pick("base_url") is None and getattr(args, "base_url", None):
            p.name, p.base_url = generic.name, generic.base_url
    return p


def resolve_all(args=None, config_path=None) -> dict[str, Provider]:
    cfg = load_model_config(config_path)
    return {r: resolve_role(r, args, cfg) for r in ROLES}


def describe_roles(by_role: dict[str, Provider]) -> str:
    rows = []
    for r in ROLES:
        p = by_role[r]
        # Only annotate sends images, so a missing vision capability is only
        # worth reporting there.
        rows.append(f"  {r:9s} {p.describe(mention_vision=(r == 'annotate'))}")
    # Collapse the common case: one model doing everything.
    if len({(p.name, p.model, p.base_url, p.reasoning, p.max_tokens, p.fallback_model)
            for p in by_role.values()}) == 1:
        # Report the annotate provider: it is the only role that needs vision,
        # so its description carries the warning that matters.
        return "  model     " + by_role["annotate"].describe()
    return "\n".join(rows)


REASONING_LEVELS = ["default", "low", "off", "high"]


def add_arguments(ap) -> None:
    """Shared CLI flags, so every entry point offers the same choice."""
    g = ap.add_argument_group("model")
    g.add_argument("--provider", choices=["anthropic", "openai"], default=None,
                   help="wire protocol (default: anthropic, or openai if --base-url is given)")
    g.add_argument("--model", default=None, help="model identifier")
    g.add_argument("--base-url", default=None,
                   help="endpoint root, or a shorthand: " + ", ".join(sorted(ALIASES)))
    g.add_argument("--api-key", default=None, help="prefer an environment variable")
    g.add_argument("--no-vision", action="store_true",
                   help="the model cannot see images; skip the annotate pass")
    g.add_argument("--json-mode", action="store_true",
                   help="ask the endpoint to guarantee valid JSON (OpenAI-compatible only)")
    g.add_argument("--max-tokens", type=int, default=None,
                   help="output budget per call (default 16000)")
    g.add_argument("--reasoning", choices=REASONING_LEVELS, default=None,
                   help="'low' or 'off' turns a reasoning model's thinking down; "
                        "'default' sends nothing")
    g.add_argument("--models", default=None, metavar="FILE",
                   help="models.yaml giving a different model per role "
                        "(annotate / plan / revise); see RUNNING.md")
    for role in ROLES:
        r = ap.add_argument_group(f"model for '{role}' ({ROLE_NEEDS[role]})")
        r.add_argument(f"--{role}-model", default=None,
                       help=f"override the model for {role}")
        r.add_argument(f"--{role}-base-url", default=None,
                       help=f"override the endpoint for {role}")
        r.add_argument(f"--{role}-max-tokens", type=int, default=None,
                       help=f"output budget for {role} (default 16000)")
        r.add_argument(f"--{role}-reasoning", choices=REASONING_LEVELS, default=None,
                       help=f"how much a reasoning model may think for {role}")
        r.add_argument(f"--{role}-fallback-model", default=None,
                       help=f"model to switch to for {role} if the first one fails")


def from_args(args) -> Provider:
    return resolve(
        provider=getattr(args, "provider", None),
        model=getattr(args, "model", None),
        base_url=getattr(args, "base_url", None),
        api_key=getattr(args, "api_key", None),
        vision=(False if getattr(args, "no_vision", False) else None),
        json_mode=(True if getattr(args, "json_mode", False) else None),
        max_tokens=getattr(args, "max_tokens", None),
        reasoning=getattr(args, "reasoning", None),
    )


# --- what an endpoint offers ---------------------------------------------

_LIST_CACHE: dict[tuple, tuple[float, list[dict]]] = {}
LIST_TTL_S = 600


def list_models(p: Provider, need_vision: bool = False, timeout: float = 8.0) -> list[dict]:
    """The models an endpoint offers, for the editor's suggestions:
    [{"id", "name", "vision", "price_in", "price_out"}]. Raises RuntimeError
    with a sentence when the endpoint cannot be asked. Cached for ten minutes.

    The key goes only to the endpoint it belongs to, exactly as for a model
    call. OpenRouter and Ollama answer without one."""
    import httpx

    base = (p.base_url or ("https://api.anthropic.com/v1" if p.name == "anthropic"
                           else ALIASES["openai"])).rstrip("/")
    key = (base, bool(p.api_key))
    hit = _LIST_CACHE.get(key)
    if hit and time.time() - hit[0] < LIST_TTL_S:
        rows = hit[1]
    else:
        headers = {}
        if p.name == "anthropic" and "anthropic.com" in base:
            if not p.api_key:
                raise RuntimeError("Anthropic lists its models only with an API key.")
            headers = {"x-api-key": p.api_key, "anthropic-version": "2023-06-01"}
            url = base + "/models?limit=100"
        elif "localhost:11434" in base or "127.0.0.1:11434" in base:
            url = base.rsplit("/v1", 1)[0] + "/api/tags"
        else:
            url = base + "/models"
            if p.api_key and "openrouter" not in base:
                headers["Authorization"] = f"Bearer {p.api_key}"
        try:
            r = httpx.get(url, headers=headers, timeout=timeout)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise RuntimeError(f"{_short(base)} did not answer ({type(exc).__name__}).") from None
        if r.status_code in (401, 403):
            raise RuntimeError(f"{_short(base)} refused the key ({r.status_code}).")
        if r.status_code >= 400:
            raise RuntimeError(f"{_short(base)} answered {r.status_code}.")
        try:
            data = r.json()
        except ValueError:
            raise RuntimeError(f"{_short(base)} did not answer with a model list.") from None
        rows = []
        if "models" in data and isinstance(data["models"], list):            # Ollama
            for m in data["models"]:
                name = m.get("name") or m.get("model")
                if name:
                    fam = " ".join((m.get("details") or {}).get("families") or [])
                    rows.append({"id": name, "name": name, "price_in": 0.0, "price_out": 0.0,
                                 "vision": any(t in (name + " " + fam).lower()
                                               for t in ("vl", "vision", "llava", "clip", "gemma3"))})
        for m in data.get("data") or []:                                       # OpenAI-style
            mid = m.get("id")
            if not mid:
                continue
            arch = m.get("architecture") or {}
            mods = arch.get("input_modalities") or []
            pr = m.get("pricing") or {}

            def per_m(v):
                try:
                    return round(float(v) * 1e6, 4)
                except (TypeError, ValueError):
                    return None
            vision = ("image" in mods) if mods else (p.name == "anthropic" or None)
            rows.append({"id": mid, "name": m.get("display_name") or m.get("name") or mid,
                         "vision": vision, "price_in": per_m(pr.get("prompt")),
                         "price_out": per_m(pr.get("completion"))})
        rows.sort(key=lambda x: x["id"])
        _LIST_CACHE[key] = (time.time(), rows)
    if need_vision:
        rows = [x for x in rows if x.get("vision") is not False]
    return rows


def _short(url: str) -> str:
    return url.split("//", 1)[-1].split("/", 1)[0]
