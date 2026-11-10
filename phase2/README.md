# Phase 2 — the album editor

A local web app for the person who owns the photographs, not the person who
wrote the code.

From the repository root:

```bash
export ANTHROPIC_API_KEY=...          # only needed to design the album
#   ... or: --base-url openrouter --model anthropic/claude-sonnet-4.5
#   ... or: --base-url ollama --model qwen2.5vl    (local, no key)
python phase2/server.py --album ./albums/sardinien --photos ~/Pictures/sardinien \
       --title "Sardinien 2026" --language de
# open http://127.0.0.1:8000
```

To try it first, use the synthetic set in the repository and no key at all:

```bash
python phase2/server.py --album ./albums/demo --photos phase1/demo_photos
```

`RUNNING.md` has the full guide, including flags, Docker and troubleshooting.

## What the user can do

Four controls per photograph:

| Control | Effect |
|---|---|
| **Caption** | printed with the photograph exactly as written; saved as you type |
| **How big** | Automatic · Full page · Large · Small |
| **Edge** | None · White frame · Rounded · Vignette · Soft edge |
| **Use this photograph** | include or leave out |
| **Front page** | opens the book: the photograph fills page 1 with the title set on it, and page 2 is the first page of photographs |
| **Resolution** | a photograph without the pixels to fill a page is badged **⚠ low res** (red **too small** when it cannot print at all), with the largest size it prints sharp. The Book tab outlines every spread where a photograph was set smaller to stay sharp, or prints soft. A low-resolution front photograph is set inside the margins with the title beneath it |
| **Orientation** | EXIF orientation is always applied. A photograph without one (messenger copies drop it) that the vision pass sees lying on its side is turned upright automatically; ⟲ / ⟳ turn any photograph by hand and undo an automatic turn. Both are kept in `rotations.yaml` beside the photographs and applied everywhere |
| **Composition** | *Always with other photos*: never alone on its page, even when the book is at the lab's 24-page minimum. Other photographs can still be composed with it, except one set to *Full page* (the two cannot both be on for one photograph) |

The common ones are on the photograph itself: hover a tile and use 👁 to leave it
out or bring it back, or ✎ to write its caption, or ⤢ to see it bigger (also: click the preview). Shift/Ctrl-click selects several,
and a bar appears to leave them out, include them, compose them or size them together. Each
place has *Include all* / *Leave out all*. Keys: arrows move, `X` leaves out or
includes, `Enter` edits the caption, `Space` shows the photograph large (arrows
page through, `Esc` closes), `[` / `]` turn it left / right.

Then one button: **Design the album**. After the first run it becomes *Design
again…*, which opens a dialog asking what should change in plain language (with
examples to click) and sends that as a revision.

Around it:

- **Book tab**: the pages as spreads, a larger view on click, and every version
  with *Go back to this*.
- **The preview follows every change.** A caption, a size or a photograph left out
  is applied to the current design and the pages are reprinted in a few seconds.
  The model is not asked, so this costs nothing.
- **Size**: the book format (21×21, 30×30, 28×21 cm…), from the lab config.
- **Export**: preview PDF, print-ready PDF, cover PDF, or page JPEGs at 150 or 300 DPI.
- **Cost**: what the last design session cost and the album's total. Click it for
  every session with its models, calls and tokens. The cost is the endpoint's own
  figure where it reports one (OpenRouter); otherwise it is the model's list price
  (`providers.PRICES`, or `price_in`/`price_out` in `models.yaml`). Local models
  cost nothing. Failed sessions are listed too, because they were paid for.

The premise of the project is still that nobody positions a box: there is no
canvas and no drag-and-drop.

## Decisions worth keeping

**No build step, no CDN, no npm.** One HTML file, inline CSS, vanilla
JavaScript, served by the same FastAPI process. A Next.js front end would need
`npm install` and a network at setup time, which breaks the self-hosted,
offline requirement for conveniences this UI does not need. It also means the
editor still works years from now without a dependency tree to resurrect.

**No Save button.** Every change PATCHes `overrides.yaml` immediately — the
same file the CLI reads, so the UI and the command line are interchangeable and
neither owns the data.

**Impossible choices are refused before they are made.** Each photograph is
measured against the real slot rectangles, so "Full page" and "Large" are struck
through on a photograph that lacks the pixels, with the reason on hover. The
server refuses them too, in case anyone goes round the UI. This is the
difference between catching a problem now and catching it at export with the
book otherwise finished.

**Progress is real.** Designing an album takes a minute or two, so the job
reports actual stages — preparing, looking at photographs, designing, applying
your settings, printing the preview — rather than an indeterminate spinner.

**It says what it cannot do.** With no API key the editor loads, groups, previews
and renders exactly as normal, and states plainly that the album cannot be
designed. It does not pretend, and it does not hide the button.

## Still to do

- Upload photographs from the browser. Today the directory is passed on the
  command line, which is fine for a homelab but not for a gift.
- Bringing back a photograph the current design never placed needs *Design again*;
  re-applying settings cannot invent a place for it.
