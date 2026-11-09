"""Which model answers, and over which wire protocol.

The planner never knew what it was talking to: its one requirement is a
`Completer`, a function taking (system prompt, messages) and returning (text,
usage). What was missing was a second implementation. There is nothing
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
                     OPENROUTER_API_KEY depending on the provider
    ALBUM_VISION     "0" to declare the model cannot see images
"""

from __future__ import annotations

import base64
import json
import os
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
        return " · ".join(bits)


def resolve(provider=None, model=None, base_url=None, api_key=None,
            vision=None, json_mode=None) -> Provider:
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

    key = api_key or os.environ.get("ALBUM_API_KEY")
    if not key:
        for env in (("ANTHROPIC_API_KEY",) if name == "anthropic"
                    else ("OPENROUTER_API_KEY", "OPENAI_API_KEY")):
            if os.environ.get(env):
                key = os.environ[env]
                break

    see = vision
    if see is None:
        see = os.environ.get("ALBUM_VISION", "1") not in ("0", "false", "no")

    jm = json_mode
    if jm is None:
        jm = os.environ.get("ALBUM_JSON_MODE", "0") in ("1", "true", "yes")

    headers = {}
    if url and "openrouter" in url:
        # OpenRouter attributes traffic by these; harmless elsewhere.
        headers = {"HTTP-Referer": "https://github.com/zioalex/photo_album_maker",
                   "X-Title": "photo_album_maker"}
    return Provider(name, mdl, url, key, see, jm, extra_headers=headers)


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


# --- completers ----------------------------------------------------------


def make_completer(p: Provider):
    """Return a Completer for this provider. Import errors surface here, once."""
    if not p.usable:
        raise SystemExit(
            f"no API key for provider '{p.name}'. Set ALBUM_API_KEY, or "
            + ("ANTHROPIC_API_KEY" if p.name == "anthropic" else "OPENAI_API_KEY / OPENROUTER_API_KEY")
            + ", or point --base-url at a local endpoint that needs none."
        )
    return _anthropic(p) if p.name == "anthropic" else _openai(p)


def _usage(inp: int, out: int, imgs: int):
    from planner import Usage

    return Usage(inp, out, 1, imgs)


def _anthropic(p: Provider):
    try:
        from anthropic import Anthropic
    except ImportError:
        raise SystemExit("pip install anthropic")

    client = Anthropic(api_key=p.api_key, **({"base_url": p.base_url} if p.base_url else {}))

    def complete(system: str, messages: list[dict]):
        msgs = messages if p.vision else _strip_images(messages)
        resp = client.messages.create(model=p.model, max_tokens=p.max_tokens,
                                      system=system, messages=msgs)
        text = "".join(b.text for b in resp.content if b.type == "text")
        return text, _usage(resp.usage.input_tokens, resp.usage.output_tokens,
                            count_images(msgs))

    return complete


_SCHEMA_CACHE: dict = {}


def _layout_schema():
    """The layout JSON schema, for endpoints that constrain decoding to it.

    Only used for the planning and revision calls; the annotate pass returns a
    bare array and is left alone.
    """
    if "v" in _SCHEMA_CACHE:
        return _SCHEMA_CACHE["v"]
    try:
        from schema import Layout

        _SCHEMA_CACHE["v"] = Layout.model_json_schema()
    except Exception:
        _SCHEMA_CACHE["v"] = None
    return _SCHEMA_CACHE["v"]


def _openai(p: Provider):
    import httpx

    url = (p.base_url or ALIASES["openai"]).rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json", **p.extra_headers}
    if p.api_key:
        headers["Authorization"] = f"Bearer {p.api_key}"

    def complete(system: str, messages: list[dict]):
        msgs = messages if p.vision else _strip_images(messages)
        body = {
            "model": p.model,
            "messages": _to_openai(system, msgs),
            "max_tokens": p.max_tokens,
        }
        # The annotate pass returns a bare array, which the layout schema does
        # not describe; detect it and leave json_mode off for that call.
        if system.startswith("You are a picture editor"):
            body["_skip_schema"] = True
        if body.pop("_skip_schema", False):
            pass
        elif p.json_mode:
            # Prefer a real schema over bare JSON mode. OpenAI, vLLM, Ollama
            # and llama.cpp all constrain decoding to the grammar, which turns
            # "will this model emit valid JSON" from a gamble into a near
            # certainty — and that is what makes small local models usable as
            # planners at all. Endpoints that only understand json_object are
            # handled by the retry below.
            schema = _layout_schema()
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
        r = httpx.post(url, headers=headers, json=body, timeout=600.0)
        # Not every endpoint understands json_schema. Fall back rather than
        # fail: a 400 mentioning response_format is a capability problem, not a
        # prompt problem.
        if r.status_code >= 400 and "response_format" in body and "response_format" in r.text:
            body["response_format"] = {"type": "json_object"}
            r = httpx.post(url, headers=headers, json=body, timeout=600.0)
            if r.status_code >= 400:
                body.pop("response_format")
                r = httpx.post(url, headers=headers, json=body, timeout=600.0)
        if r.status_code >= 400:
            raise RuntimeError(f"{r.status_code} from {url}: {r.text[:400]}")
        data = r.json()
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError):
            raise RuntimeError(f"unexpected response shape: {json.dumps(data)[:400]}")
        u = data.get("usage") or {}
        return text, _usage(u.get("prompt_tokens", 0), u.get("completion_tokens", 0),
                            count_images(msgs))

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
    api_key, vision, json_mode. A role inherits anything it does not set.
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

    p = resolve(
        provider=pick("provider"),
        model=pick("model"),
        base_url=pick("base_url"),
        api_key=pick("api_key"),
        vision=vision,
        json_mode=jm,
    )
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
    if len({(p.name, p.model, p.base_url) for p in by_role.values()}) == 1:
        # Report the annotate provider: it is the only role that needs vision,
        # so its description carries the warning that matters.
        return "  model     " + by_role["annotate"].describe()
    return "\n".join(rows)


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
    g.add_argument("--models", default=None, metavar="FILE",
                   help="models.yaml giving a different model per role "
                        "(annotate / plan / revise); see RUNNING.md")
    for role in ROLES:
        r = ap.add_argument_group(f"model for '{role}' ({ROLE_NEEDS[role]})")
        r.add_argument(f"--{role}-model", default=None,
                       help=f"override the model for {role}")
        r.add_argument(f"--{role}-base-url", default=None,
                       help=f"override the endpoint for {role}")


def from_args(args) -> Provider:
    return resolve(
        provider=getattr(args, "provider", None),
        model=getattr(args, "model", None),
        base_url=getattr(args, "base_url", None),
        api_key=getattr(args, "api_key", None),
        vision=(False if getattr(args, "no_vision", False) else None),
        json_mode=(True if getattr(args, "json_mode", False) else None),
    )
