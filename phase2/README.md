# Phase 2 — the album editor

A local web app for the person who owns the photographs, not the person who
wrote the code.

```bash
export ANTHROPIC_API_KEY=...          # only needed to design the album
python phase2/server.py --album ./albums/sardinien --photos ./photos
# open http://localhost:8000
```

## What the user can do

Four controls per photograph, and nothing else:

| Control | Effect |
|---|---|
| **Description** | printed under the photograph; saved as you type |
| **How big** | Automatic · Full page · Large · Small |
| **Edge** | None · White frame · Rounded · Vignette · Soft edge |
| **Use this photograph** | include or leave out |

Then one button: **Design the album**. After the first run it becomes *Design
again*, which asks what should change in plain language and sends that as a
revision.

That is the whole interface. The premise of the project is that nobody
positions a box, so there is no canvas, no grid, no drag-and-drop. Four
controls can be learnt in a sitting; a layout editor cannot.

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
- Spread-by-spread preview inside the page. Right now the proof opens as a PDF
  in a new tab, which is honest but not elegant.
- Version history and rollback are in the API and the CLI but not yet in the UI.
