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
import os
import sys
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
MAX_ATTEMPTS = 3

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

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.attempts += other.attempts
        self.images += other.images

    @property
    def usd(self) -> float:
        return (
            self.input_tokens / 1e6 * USD_PER_MTOK_IN
            + self.output_tokens / 1e6 * USD_PER_MTOK_OUT
        )

    def line(self, priced: bool = True) -> str:
        base = (f"{self.attempts} call(s), {self.images} image(s), "
                f"{self.input_tokens:,} in / {self.output_tokens:,} out tokens")
        return base + (f" ≈ ${self.usd:.2f}" if priced else "")


@dataclass
class Result:
    layout: Layout
    usage: Usage = field(default_factory=Usage)
    raw: str = ""


Completer = Callable[[str, list[dict]], tuple[str, Usage]]
"""(system, messages) -> (assistant text, usage). Swappable for tests."""


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


def _run(system: str, first_user: list[dict] | str, complete: Completer,
         check: Callable[[Layout], list[str]] | None = None) -> Result:
    """One logical call, with retries that feed the validation error back.

    `check` returns blocking problems the schema cannot express. They are fed
    back like schema errors, but a layout that still has them on the last
    attempt is returned rather than discarded: it is valid, and the validator
    downstream reports exactly what is wrong with it.
    """
    messages: list[dict] = [{"role": "user", "content": first_user}]
    total = Usage()
    last_error = ""

    for attempt in range(1, MAX_ATTEMPTS + 1):
        text, usage = complete(system, messages)
        total.add(usage)
        try:
            data = json.loads(extract_json(text))
            layout = Layout.model_validate(data)
            problems = check(layout) if check else []
            if not problems or attempt == MAX_ATTEMPTS:
                return Result(layout, total, text)
            raise ValueError("the layout cannot be printed as it stands:\n"
                             + "\n".join(problems))
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            lines = last_error.splitlines()
            shown = lines[1] if len(lines) > 1 and lines[0].endswith("as it stands:") else lines[0]
            print(f"  attempt {attempt} rejected: {shown[:120]}")
            if attempt == MAX_ATTEMPTS:
                break
            messages += [
                {"role": "assistant", "content": text},
                {"role": "user", "content": P.retry_user_content(last_error)},
            ]

    raise RuntimeError(
        f"model failed to produce a valid layout in {MAX_ATTEMPTS} attempts.\n"
        f"last error:\n{last_error}"
    )


def plan(
    *, title: str, subtitle: str | None, fmt: str, language: str,
    photos: list[Photo], g, lab: dict, complete: Completer,
    target_spreads: int | None = None, thumb_dir: Path | None = None,
    groups=None,
) -> Result:
    """Plan the whole album in one call.

    `thumb_dir` attaches raw thumbnails. Prefer running the annotate pass
    instead: it looks at each photograph once and caches the result, so retries
    and every later revision are text-only.
    """
    system = P.system_prompt(g, lab, language)
    text = P.plan_user_content(title, subtitle, fmt, language, photos, target_spreads, groups)
    if thumb_dir:
        content: list[dict] | str = [{"type": "text", "text": text}] + image_blocks(photos, thumb_dir)
    else:
        content = text
    return _run(system, content, complete,
                lambda lay: resolution_errors(lay, photos, g, lab))


def revise(
    *, current: dict, comment: str, photos: list[Photo], g, lab: dict,
    language: str, complete: Completer, groups=None,
) -> Result:
    system = P.system_prompt(g, lab, language)
    return _run(system, P.revise_user_content(current, photos, comment, groups), complete,
                lambda lay: resolution_errors(lay, photos, g, lab))
