"""The annotate call: look at each photograph once, in batches, and cache it.

This is where vision earns its cost. Previously the only way for the model to
know what was in a frame was to attach every thumbnail to the planning call —
which meant paying again on every schema retry, and having nothing at all to
show for it afterwards.

Instead: one pass that turns pixels into a few words per photo, written to
`annotations.json`. Everything downstream — planning, revision, re-planning at a
different length, the whole comment loop — then runs on text. The annotations
are also the thing that makes "keep the same people together" possible at all,
because they are where `people` and `subject` come from.

Deliberately small vocabulary. The model is not asked for prose about the
photograph; it is asked for the handful of facts a picture editor uses when
sequencing.
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import imaging  # noqa: F401  (registers HEIC/HEIF decoders on import)
from planner import Completer, Usage, extract_json
from photos import Photo

BATCH = 8
ANNOTATION_VERSION = 2
ANNOTATE_TRANSIENT = 3   # tries per batch on a network/server failure
_sleep = time.sleep      # module-level so tests can replace it

# How large a photograph is sent to the vision model.
#
# Vision cost is driven by DIMENSIONS, not file size: Anthropic charges about
# (w x h) / 750 tokens, and OpenAI-compatible endpoints tile at 512px. JPEG
# quality therefore changes the upload but not the bill — compressing harder
# saves bandwidth and nothing else.
#
# 512px on the long edge is the default because of what this pass is actually
# asked for: a subject in a few words, a count of faces, a scene type, a focal
# point and a 1-5 rating. That is contact-sheet work. Measured against the
# 1200px proof derivatives the project already builds, 512px costs 5.4x fewer
# tokens on Anthropic and 2.5x fewer on OpenAI, for 100 photographs:
#
#     1200px   140,000 anthropic tokens    64,000 openai
#      512px    26,000 anthropic tokens    25,500 openai
#
# A focal point read off a 512px frame is accurate to about 0.2% of the frame
# width, which is far finer than the decision it informs.
#
# Raise it if annotations come back vague on detailed photographs; 768px is the
# next sensible step. Do not lower it below 384px — faces stop being countable.
VISION_PX = 512
VISION_QUALITY = 72

SYSTEM = """You are a picture editor's assistant. For each photograph you are shown, \
return the few facts needed to sequence an album. Be terse and literal. Do not \
interpret, do not praise, do not write prose.

Return a JSON array, one object per photograph, in the order shown:

[{"id": "<the id given with the image>",
  "subject": "<3-6 words: what the photograph is of>",
  "people": <integer count of people whose faces are visible>,
  "who": "<if people are recognisable and recur, a stable short label such as \
'woman in red jacket' or 'two children'; otherwise empty string>",
  "scene": "<one of: landscape, seascape, architecture, interior, street, portrait, \
group, food, detail, animal, night>",
  "focal": [x, y],
  "strength": <1-5, how well this frame would carry a full page>,
  "landmark": "<the proper name of a landmark you can identify with confidence \
— a named glacier, lake, mountain, building, square; empty string if unsure. \
A place hint may be given with the id: use it to tell which one it is, never \
invent a name>",
  "detail": "<6-12 words: one concrete, specific thing in this frame a person \
would remember — the light, the weather, what someone is doing, a colour. \
Not a category ('mountain landscape') but an observation ('fresh snow on the \
ridge above the tongue of the glacier')>"}]

Be specific in `subject` too: "milky turquoise meltwater lake", not "lake".

`focal` is the normalised point that must survive a crop — a face, the eyes of \
the main subject, or the centre of interest. `strength` is a craft judgement: 5 is \
a photograph that can open a chapter, 1 is a record shot that belongs in a grid.

Return the array and nothing else."""


@dataclass
class Annotation:
    id: str
    subject: str = ""
    people: int = 0
    who: str = ""
    scene: str = ""
    focal: tuple[float, float] = (0.5, 0.5)
    strength: int = 3
    landmark: str = ""
    detail: str = ""
    # 1: subject/people/scene only. 2: adds landmark and detail, which the
    # captions need; version-1 entries are looked at again once.
    v: int = 1

    def line(self) -> str:
        bits = [self.subject or self.scene]
        if self.people:
            bits.append(f"{self.people} {'person' if self.people == 1 else 'people'}"
                        + (f" ({self.who})" if self.who else ""))
        bits.append(f"strength {self.strength}/5")
        return "; ".join(b for b in bits if b)


@dataclass
class Annotations:
    by_id: dict[str, Annotation] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "Annotations":
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text())
        known = set(Annotation.__dataclass_fields__)
        return cls({k: Annotation(**{f: x for f, x in v.items() if f in known})
                    for k, v in raw.items()})

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({k: asdict(v) for k, v in self.by_id.items()},
                       ensure_ascii=False, indent=2)
        )

    def missing(self, photos: list[Photo]) -> list[Photo]:
        return [p for p in photos
                if p.id not in self.by_id or self.by_id[p.id].v < ANNOTATION_VERSION]


def build_vision_copies(photos: list[Photo], dst: Path,
                        px: int = VISION_PX, quality: int = VISION_QUALITY) -> Path:
    """Small sRGB copies for the vision pass only.

    Kept separate from the 1200px proof derivatives, which exist to render a
    readable proof PDF and must stay that size. ICC profiles and EXIF are
    dropped: they cost bytes on every upload and the model cannot use them.
    """
    import render as R
    from PIL import Image

    dst.mkdir(parents=True, exist_ok=True)
    for p in photos:
        out = dst / f"{p.id}.jpg"
        if out.exists():
            continue
        with Image.open(p.path) as im:
            im = R.to_srgb(im)
            im.thumbnail((px, px), Image.LANCZOS)
            im.save(out, "JPEG", quality=quality, optimize=True)
    return dst


def estimate_tokens(photos: list[Photo], thumb_dir: Path) -> int:
    """Roughly what the images in an annotate pass will cost, Anthropic-style."""
    from PIL import Image

    total = 0
    for p in photos:
        f = thumb_dir / f"{p.id}.jpg"
        if not f.exists():
            continue
        with Image.open(f) as im:
            w, h = im.size
        total += (w * h) // 750
    return total


def _batch_message(batch: list[Photo], thumb_dir: Path) -> list[dict]:
    content: list[dict] = [
        {"type": "text", "text": f"{len(batch)} photographs follow."}
    ]
    for p in batch:
        f = thumb_dir / f"{p.id}.jpg"
        hint = f" (taken near {p.place})" if getattr(p, "place", None) else ""
        if getattr(p, "features", None):
            hint += " — nearby: " + "; ".join(p.features[:4])
        content.append({"type": "text", "text": f"id: {p.id}{hint}"})
        content.append(
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg",
                           "data": base64.standard_b64encode(f.read_bytes()).decode()},
            }
        )
    return content


def run(
    photos: list[Photo], thumb_dir: Path, cache: Path, complete: Completer,
    force: bool = False,
) -> tuple[Annotations, Usage]:
    """Annotate anything not already cached. Idempotent and resumable."""
    store = Annotations() if force else Annotations.load(cache)
    todo = [p for p in store.missing(photos) if (thumb_dir / f"{p.id}.jpg").exists()]
    usage = Usage()
    if not todo:
        return store, usage

    failed = answered = 0
    for i in range(0, len(todo), BATCH):
        batch = todo[i : i + BATCH]
        try:
            text, u = _ask(complete, batch, thumb_dir)
        except Exception as exc:  # annotation is optional; never lose the build over it
            failed += 1
            print(f"  batch {i // BATCH + 1} failed ({str(exc)[:160]}); "
                  f"those photos stay unannotated")
            if failed >= 2 and not answered:
                # Nothing has worked yet and the endpoint keeps failing: stop
                # paying for (and waiting on) the remaining batches. The next
                # build retries whatever is still missing.
                print("  annotation endpoint is not answering; continuing without it")
                break
            continue
        answered += 1
        usage.add(u)
        try:
            raw = extract_json_array(text)
        except ValueError as exc:
            print(f"  batch {i // BATCH + 1} unreadable ({exc}); those photos stay unannotated")
            continue
        for item in raw:
            pid = str(item.get("id", ""))
            if pid not in {p.id for p in batch}:
                continue
            focal = item.get("focal") or [0.5, 0.5]
            store.by_id[pid] = Annotation(
                id=pid,
                subject=str(item.get("subject", ""))[:80],
                people=int(item.get("people", 0) or 0),
                who=str(item.get("who", ""))[:40],
                scene=str(item.get("scene", ""))[:20],
                focal=(float(focal[0]), float(focal[1])),
                strength=max(1, min(5, int(item.get("strength", 3) or 3))),
                landmark=str(item.get("landmark", "") or "")[:60],
                detail=str(item.get("detail", "") or "")[:120],
                v=ANNOTATION_VERSION,
            )
        store.save(cache)
        print(f"  annotated {min(i + BATCH, len(todo))}/{len(todo)}")
    return store, usage


def _ask(complete: Completer, batch: list[Photo], thumb_dir: Path):
    """One batch, resending on a transient endpoint failure (429, 5xx, timeouts).

    The planner has its own retry loop; this pass used to have none, so a single
    dropped connection while looking at the photographs failed the whole build
    before planning had even started.
    """
    from planner import TransientError, _backoff

    msgs = [{"role": "user", "content": _batch_message(batch, thumb_dir)}]
    for n in range(1, ANNOTATE_TRANSIENT + 1):
        try:
            return complete(SYSTEM, msgs)
        except TransientError as exc:
            if n == ANNOTATE_TRANSIENT:
                raise
            delay = _backoff(n, exc.retry_after)
            print(f"  endpoint error ({str(exc)[:120]}); resending in {delay:g}s")
            _sleep(delay)


def extract_json_array(text: str) -> list[dict]:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1].rsplit("```", 1)[0]
    start, end = t.find("["), t.rfind("]")
    if start == -1 or end == -1:
        # a single object is a recoverable mistake
        return [json.loads(extract_json(t))]
    return json.loads(t[start : end + 1])


def apply(photos: list[Photo], anns: Annotations) -> None:
    for p in photos:
        a = anns.by_id.get(p.id)
        if a:
            p.annotation = a.line()
            p.strength = a.strength
            p.suggested_focal = a.focal
            p.landmark = a.landmark
            p.detail = a.detail
