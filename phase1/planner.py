"""The plan and revise calls.

Rules from PLAN.md that are enforced here rather than hoped for:

- The whole album is planned in ONE call. No chunking, no page-by-page.
- Schema failure means reject and retry the entire call with the validation
  error fed back. The model repairs its own output; we never patch JSON by hand.
- Vision is on for planning and off for revision. The visual decisions were made
  in the plan call; a revision has the layout JSON in context and does not need
  to look at 80 photographs again to move a spread.
"""

from __future__ import annotations

import base64
import json
import sys
import time
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "phase0"))

from schema import Layout  # noqa: E402
import templates_lib  # noqa: E402
from geometry import effective_dpi  # noqa: E402

import prompt as P  # noqa: E402
from photos import Photo  # noqa: E402

MODEL = "claude-sonnet-5"
MAX_TOKENS = 16000
MAX_ATTEMPTS = 3      # repair attempts: the model answered, but the answer was wrong
MAX_TRANSIENT = 3     # resends of the same request after a network/server failure
MAX_RECOVERY = 2      # resends after an empty or truncated reply (changed knobs, same messages)
_sleep = time.sleep   # module-level so tests can replace it

# Rough figures for the cost line, in Anthropic's terms. They exist to make the
# vision-on-revision trap visible, not to be an invoice, and they are nonsense
# for any other provider — so the dollar figure is only shown when it means
# something. Token counts are always real.
USD_PER_MTOK_IN = 3.0
USD_PER_MTOK_OUT = 15.0


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    attempts: int = 0
    images: int = 0
    # What the calls cost, in USD, when it is known: reported by the endpoint
    # (OpenRouter does) or priced from the model's list price (providers.price).
    # None means no call so far could be priced; `estimated` that at least one
    # was priced by guesswork rather than a known rate.
    cost_usd: float | None = None
    estimated: bool = False

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.attempts += other.attempts
        self.images += other.images
        if other.cost_usd is not None:
            self.cost_usd = (self.cost_usd or 0.0) + other.cost_usd
        self.estimated = self.estimated or other.estimated

    @property
    def usd(self) -> float:
        if self.cost_usd is not None:
            return self.cost_usd
        return (
            self.input_tokens / 1e6 * USD_PER_MTOK_IN
            + self.output_tokens / 1e6 * USD_PER_MTOK_OUT
        )

    def as_dict(self) -> dict:
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "calls": self.attempts, "images": self.images,
                "usd": None if self.cost_usd is None else round(self.cost_usd, 4),
                "estimated": self.estimated}

    def line(self, priced: bool = True) -> str:
        base = (f"{self.attempts} call(s), {self.images} image(s), "
                f"{self.input_tokens:,} in / {self.output_tokens:,} out tokens")
        if self.cost_usd is not None:
            return base + f" {'≈' if self.estimated else '='} ${self.cost_usd:.3f}"
        return base + (f" ≈ ${self.usd:.2f}" if priced else "")


class TransientError(RuntimeError):
    """Worth resending the same request after a pause: 429, 5xx, timeouts,
    connection errors, a 200 whose body carries an `error` object."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class PlanningError(RuntimeError):
    """The planner gave up. The message says why and where the logs are."""


@dataclass
class Reply:
    """What a completer hands back. Beyond the text it carries everything
    needed to tell *why* a reply is empty — a model mistake, a spent token
    budget, a refusal and a provider error all need different handling."""
    text: str
    usage: "Usage"
    finish: str | None = None      # "stop" | "length" | "refusal" | "filter" | "error" | None (unknown)
    raw_finish: str | None = None  # the provider's own string
    reasoning_chars: int = 0
    reasoning_tokens: int = 0
    refusal: str | None = None
    error: str | None = None       # provider error carried in an otherwise-200 body
    response_format: str | None = None  # "json_schema" | "json_object" | None — what was sent
    raw: dict | None = None        # parsed body, for logging failed attempts

    def __iter__(self):            # `text, usage = complete(...)` keeps working
        yield self.text
        yield self.usage


@dataclass
class Result:
    layout: Layout
    usage: Usage = field(default_factory=Usage)
    raw: str = ""


Completer = Callable[..., "Reply | tuple[str, Usage]"]
"""(system, messages, **opts) -> Reply, or the older (text, usage) tuple.
opts: max_tokens, reasoning ("low"|"off"), json_fallback. Swappable for tests."""


def anthropic_completer(model: str = MODEL) -> Completer:
    """Kept for callers that want the default provider and nothing else.

    New code should go through providers.make_completer, which supports any
    OpenAI-compatible endpoint as well.
    """
    import providers

    return providers.make_completer(providers.resolve(provider="anthropic", model=model))


def image_blocks(photos: list[Photo], thumb_dir: Path) -> list[dict]:
    """1200px proof derivatives as image blocks, in the order the ids are listed.

    Roughly triples input cost. Worth it for hero selection and focal points on
    the initial plan; never worth it on a revision.
    """
    blocks = []
    for p in photos:
        f = thumb_dir / f"{p.id}.jpg"
        if not f.exists():
            continue
        blocks.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": base64.standard_b64encode(f.read_bytes()).decode(),
                },
            }
        )
        blocks.append({"type": "text", "text": p.id})
    return blocks


def extract_json(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        t = t.rsplit("```", 1)[0]
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end == -1:
        # Say what came back instead: an empty reply (output spent on
        # reasoning, or a refusal) needs a different fix than prose.
        if not t:
            raise ValueError("no JSON object in the response (it was empty)")
        snippet = " ".join(t.split())[:100]
        raise ValueError(f"no JSON object in the response, which began: {snippet!r}")
    return t[start : end + 1]


def resolution_errors(layout: Layout, photos: list[Photo], g, lab: dict) -> list[str]:
    """Placements the validator would block for resolution, as feedback lines.

    The planner is told which slots each photograph is too small for, but
    that is a hint; this is the same measurement validate.check makes, run
    before the layout leaves the retry loop so the model gets to fix it
    instead of the render being refused afterwards.
    """
    block = float(lab["resolution"]["block_below_dpi"])
    warn = float(lab["resolution"]["warn_below_dpi"])
    by_id = {p.id: p for p in photos}
    out = []
    import overrides as OV

    for sp in layout.spreads:
        for i, slot in enumerate(sp.slots, 1):
            if slot.photo_id not in by_id and OV.match_id(slot.photo_id, by_id) is None:
                out.append(
                    f"spread {sp.index} ({sp.template}) slot {i}: {slot.photo_id!r} is not a photo "
                    f"id. Copy ids exactly as the photo list gives them (the first column), "
                    f"never shortened.")
    for sp in layout.spreads:
        if sp.template not in templates_lib.REGISTRY:
            continue
        rendered = templates_lib.build(sp.template, g, sp.options)
        for i, (slot, box) in enumerate(zip(sp.slots, rendered.slots), 1):
            ph = by_id.get(slot.photo_id)
            if ph is None:
                continue
            dpi = effective_dpi(ph.width, ph.height, box.rect)
            if dpi < block:
                out.append(
                    f"spread {sp.index} ({sp.template}) slot {i}: {ph.id} would print at "
                    f"{dpi:.0f} DPI in a {box.rect.w:.0f}x{box.rect.h:.0f}mm slot, below the "
                    f"{block:.0f} DPI the lab accepts. Move it to a smaller slot or a "
                    f"template it is not listed as too small for (it needs {warn:.0f} DPI to "
                    f"look sharp), or leave it out.")
    return out


def _backoff(n: int, retry_after: float | None) -> float:
    """2s, 4s, 8s — or what the server asked for if that is longer; never over 30s."""
    return min(30.0, max(2.0 * 2 ** (n - 1), retry_after or 0.0))


def _write_log(log_dir: Path | None, kind: str, n: int, diagnosis: str,
               reply: Reply | None, text: str, error: str | None = None) -> str | None:
    """Keep what the model actually said on a failed attempt. A failed plan is
    otherwise undiagnosable after the fact. Logging must never break planning."""
    if log_dir is None:
        return None
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        path = log_dir / f"{kind}-{stamp}-attempt{n}.json"
        r = reply
        body = {
            "kind": kind, "attempt": n, "diagnosis": diagnosis,
            "finish": r.finish if r else None, "raw_finish": r.raw_finish if r else None,
            "reasoning_chars": r.reasoning_chars if r else 0,
            "reasoning_tokens": r.reasoning_tokens if r else 0,
            "refusal": r.refusal if r else None,
            "error": (r.error if r else None) or error,
            "response_format": r.response_format if r else None,
            "text": text[:20000], "raw": r.raw if r else None,
        }
        import keystore

        # A model's error text can echo a request, and a request can carry a
        # key: nothing reaches the log directory unredacted.
        path.write_text(keystore.redact(json.dumps(body, ensure_ascii=False, indent=2,
                                                   default=str)), encoding="utf-8")
        return str(path)
    except Exception:
        return None


def _run(system: str, first_user: list[dict] | str, complete: Completer,
         check: Callable[[Layout], list[str]] | None = None,
         log_dir: Path | None = None, kind: str = "plan",
         fallback: Completer | None = None,
         on_event: Callable[[str], None] | None = None) -> Result:
    """One logical call, with three separate retry budgets.

    * repair (MAX_ATTEMPTS): the model answered but the answer was wrong; the
      error is fed back. `check` returns blocking problems the schema cannot
      express; a layout that still has them on the last attempt is returned
      rather than discarded — it is valid, and the validator downstream reports
      exactly what is wrong with it.
    * transient (MAX_TRANSIENT): the request failed in transit; resend it
      unchanged after a pause.
    * recovery (MAX_RECOVERY): the reply was empty or cut off. Telling the model
      "you sent nothing" is pointless — it is the request that has to change
      (less reasoning, more room, weaker structured-output mode).

    When a budget runs out (or the endpoint rejects the request outright, as with
    an unknown model) and a `fallback` completer was given, the call switches to it
    once: transient/recovery counters and sticky knobs start afresh, the repair
    count and the messages carry over. Refusals never switch.

    `on_event` hears a short sentence for each retry, recovery and switch.

    Only the repair path ever touches `messages`.
    """
    messages: list[dict] = [{"role": "user", "content": first_user}]
    total = Usage()
    last_error = ""
    logs: list[str] = []
    opts: dict = {}          # sticky knobs: max_tokens, reasoning
    one_shot: dict = {}      # json_fallback applies to the next call only
    repair = transient = recovery = calls = 0
    length_hit = False
    switched = False

    def event(msg: str):
        if on_event is None:
            return
        try:
            on_event(msg)
        except Exception:
            pass

    def fail(msg: str):
        tried = "\n(the fallback model was tried too)" if switched else ""
        # One folder, not every file: the UI shows this, and four long paths
        # bury the sentence that matters.
        where = (f"\nthe raw replies are in {Path(logs[-1]).parent}") if logs else ""
        raise PlanningError(msg + tried + where)

    def try_fallback() -> bool:
        """Switch to the fallback model, once. True if the caller should resend."""
        nonlocal complete, switched, transient, recovery, length_hit, opts, one_shot
        if fallback is None or switched:
            return False
        switched = True
        complete = fallback
        transient = recovery = 0
        length_hit = False
        opts, one_shot = {}, {}
        print("  switching to the fallback model")
        event("Switching to the fallback model")
        return True

    def note(diagnosis: str, reply: Reply | None, text: str, error: str | None = None):
        print(f"  attempt {calls} rejected: {diagnosis}")
        p = _write_log(log_dir, kind, calls, diagnosis, reply, text, error)
        if p:
            logs.append(p)

    def facts(r: Reply) -> str:
        bits = [f"finish={r.finish}"]
        if r.raw_finish and r.raw_finish != r.finish:
            bits.append(f"raw={r.raw_finish}")
        if r.reasoning_tokens:
            bits.append(f"{r.reasoning_tokens:,} reasoning tokens")
        elif r.reasoning_chars:
            bits.append(f"{r.reasoning_chars:,} reasoning chars")
        return ", ".join(bits)

    def transient_failure(msg: str, retry_after: float | None, reply: Reply | None):
        nonlocal transient
        transient += 1
        if transient > MAX_TRANSIENT:
            note(f"endpoint error: {msg[:200]}", reply, "", msg)
            if try_fallback():
                return
            fail(f"the endpoint kept failing: {msg}")
        delay = _backoff(transient, retry_after)
        note(f"endpoint error: {msg[:200]} — resending in {delay:g}s", reply, "", msg)
        event(f"The model endpoint is busy — retrying in {delay:g}s")
        _sleep(delay)

    while True:
        calls += 1
        try:
            got = complete(system, messages, **opts, **one_shot)
        except TransientError as exc:
            one_shot = {}
            transient_failure(str(exc), exc.retry_after, None)
            continue
        except PlanningError:
            raise
        except Exception as exc:
            # Not worth resending (unknown model, bad request, no key...). Any
            # exception, not just RuntimeError: SDK errors such as
            # anthropic.NotFoundError do not derive from it.
            one_shot = {}
            note(f"endpoint error: {str(exc)[:200]}", None, "", str(exc))
            if try_fallback():
                continue
            fail(str(exc))
        one_shot = {}
        reply = got if isinstance(got, Reply) else Reply(got[0], got[1])
        total.add(reply.usage)
        text = reply.text or ""

        if reply.finish == "error" or (reply.error and not text.strip()):
            transient_failure(reply.error or f"the provider reported {reply.raw_finish or 'an error'}",
                              None, reply)
            continue

        if reply.finish in ("refusal", "filter") or (reply.refusal and not text.strip()):
            why = reply.refusal or f"blocked by the provider ({reply.raw_finish or reply.finish})"
            note(f"the model refused ({facts(reply)})", reply, text)
            fail(f"the model refused: {why}")

        if reply.finish == "length":
            cur = opts.get("max_tokens")
            reasoned = bool(reply.reasoning_tokens or reply.reasoning_chars)
            if length_hit or recovery >= MAX_RECOVERY:
                budget = f"{cur:,} tokens" if cur else "its output budget"
                note(f"{'empty reply' if not text.strip() else 'reply cut off'} ({facts(reply)})",
                     reply, text)
                if try_fallback():
                    continue
                if not text.strip() and reasoned:
                    fail(f"the planner spent its whole output budget ({budget}) on reasoning "
                         f"and wrote nothing — set a lower reasoning level or a larger "
                         f"max_tokens for the {kind} role")
                fail(f"the planner's answer was cut off at {budget} — set a larger "
                     f"max_tokens for the {kind} role")
            length_hit = True
            recovery += 1
            new = 32000 if not cur else min(64000, max(cur * 2, 32000))
            opts = {**opts, "reasoning": "low", "max_tokens": new}
            event("The reply was cut off — retrying with less reasoning and more room"
                  if text.strip() else "The reply was empty — retrying with less reasoning")
            note(f"{'empty reply' if not text.strip() else 'reply cut off'} ({facts(reply)}) "
                 f"— retrying with reasoning=low, max_tokens={new}", reply, text)
            continue

        if not text.strip():
            if recovery >= MAX_RECOVERY:
                note(f"empty reply ({facts(reply)})", reply, text)
                if try_fallback():
                    continue
                fail(f"the planner returned nothing, repeatedly ({facts(reply)}) — "
                     f"try another model or turn json_mode off for the {kind} role")
            recovery += 1
            event("The reply was empty — retrying")
            if reply.response_format:
                one_shot = {"json_fallback": True}
                note(f"empty reply ({facts(reply)}) — retrying without strict "
                     f"{reply.response_format} output", reply, text)
            else:
                note(f"empty reply ({facts(reply)}) — resending unchanged", reply, text)
            continue

        repair += 1
        try:
            data = json.loads(extract_json(text))
            layout = Layout.model_validate(data)
            problems = check(layout) if check else []
            if not problems or repair == MAX_ATTEMPTS:
                return Result(layout, total, text)
            raise ValueError("the layout cannot be printed as it stands:\n"
                             + "\n".join(problems))
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            lines = last_error.splitlines()
            shown = lines[1] if len(lines) > 1 and lines[0].endswith("as it stands:") else lines[0]
            p = _write_log(log_dir, kind, calls, shown[:300], reply, text)
            if p:
                logs.append(p)
            print(f"  attempt {repair} rejected: {shown[:120]}")
            if repair == MAX_ATTEMPTS:
                break
            event(f"The layout needed corrections — asking the model to fix it "
                  f"(attempt {repair + 1} of {MAX_ATTEMPTS})")
            messages += [
                {"role": "assistant", "content": text},
                {"role": "user", "content": P.retry_user_content(last_error)},
            ]

    fail(f"model failed to produce a valid layout in {MAX_ATTEMPTS} attempts.\n"
         f"last error:\n{last_error}")


def plan(
    *, title: str, subtitle: str | None, fmt: str, language: str,
    photos: list[Photo], g, lab: dict, complete: Completer,
    target_spreads: int | None = None, thumb_dir: Path | None = None,
    groups=None, log_dir: Path | None = None,
    fallback: Completer | None = None,
    on_event: Callable[[str], None] | None = None,
    prefs: dict | None = None,
) -> Result:
    """Plan the whole album in one call.

    `thumb_dir` attaches raw thumbnails. Prefer running the annotate pass
    instead: it looks at each photograph once and caches the result, so retries
    and every later revision are text-only.
    """
    system = P.system_prompt(g, lab, language)
    text = P.plan_user_content(title, subtitle, fmt, language, photos, target_spreads, groups,
                               prefs)
    if thumb_dir:
        content: list[dict] | str = [{"type": "text", "text": text}] + image_blocks(photos, thumb_dir)
    else:
        content = text
    return _run(system, content, complete,
                lambda lay: resolution_errors(lay, photos, g, lab),
                log_dir=log_dir, kind="plan", fallback=fallback, on_event=on_event)


def revise(
    *, current: dict, comment: str, photos: list[Photo], g, lab: dict,
    language: str, complete: Completer, groups=None,
    log_dir: Path | None = None,
    fallback: Completer | None = None,
    on_event: Callable[[str], None] | None = None,
    prefs: dict | None = None,
    new_photos=None,
) -> Result:
    system = P.system_prompt(g, lab, language)
    return _run(system, P.revise_user_content(current, photos, comment, groups, prefs, new_photos),
                complete,
                lambda lay: resolution_errors(lay, photos, g, lab),
                log_dir=log_dir, kind="revise", fallback=fallback, on_event=on_event)
