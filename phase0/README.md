# Phase 0 — validate the print chain

One script, no LLM, no database, no frontend. It takes a hand-written layout
JSON and a folder of photos and produces the two PDFs Saal Digital wants:
inner pages and cover, as separate files, with no crop or register marks.

The output of this phase is not software. It is a printed book with a ruler
held against it.

## Run it

```bash
pip install -r ../requirements.txt
playwright install chromium

python tools/make_placeholder_photos.py          # stand-ins, replace with real ones
python render.py check --layout examples/layout_test24.json --photos ./photos
python render.py proof --layout examples/layout_test24.json --photos ./photos --allow-fold
python render.py print --layout examples/layout_test24.json --photos ./photos --allow-fold
python render.py cover --layout examples/layout_test24.json --photos ./photos
```

`--allow-fold` is needed only for the calibration book, which deliberately prints
a panorama across the crease so you can judge how bad it really is. Production
albums never do this.

`check` runs the validator alone and exits non-zero on a blocking problem.
`proof` and `print` differ only in which image derivatives get built —
1200px/q80 versus 300 DPI at slot size/q95. Same HTML, same geometry.

## Before you order

1. **Download Saal's template for 21×21 Professional Line** and confirm
   `bleed_mm` in `labs/saal.yaml`. Everything in that file marked VERIFY is a
   considered guess, in particular the cover constants — `leaf_thickness_mm`,
   `board_mm`, `wrap_mm`. The spine is computed from them, so if they are wrong
   the cover art lands off the fold.
2. **Replace the placeholder photos with real ones**, keeping the ids `p001`–`p015`.
   Placeholders will not tell you anything about skin tones or shadow detail.
   Include at least one image with a face and one with deep shadow.
3. Run `check` and clear every error. Warnings are judgement calls; errors are not.
4. Upload `out/inner-print.pdf` and `out/cover.pdf` as two separate files.
   Professional Line, layflat, silk paper.

## What to measure when the book arrives

The test book is 24 pages, of which two spreads exist purely to be measured.

**Spread 4 (pages 7–8) — trim ruler and reproduction chart.**
- Find the lowest still-visible tick on the top and left rulers. That number is
  your real trim tolerance. If it is larger than `bleed_mm`, raise the bleed.
- The red trim rule should be gone. The blue 5mm safe rule must be intact. If
  the blue line is cut, raise `safe_margin_mm`.
- Count how many of the 11 grey steps stay separable, especially at the dark
  end. That is the shadow detail budget for every future album.
- Read the type specimens at normal viewing distance. Whatever is uncomfortable
  is your minimum caption size; adjust `--caption` sizing in `assets/style.css`.
- Note the finest hairline that survives.

**Spread 2 (pages 5–6) — gutter target, and spread 1 (pages 3–4) — panorama.**

The project rule is that no photograph crosses the fold, so both of these render
only with `--allow-fold`. They are in the test book precisely to check that
decision against paper rather than intuition: if the panorama reads well and the
crease is unobtrusive, `allow_fold_crossing` in `labs/saal.yaml` is where to
change our mind.
- The vertical rule should land in the fold. Read any offset off the ±5mm ticks.
- Look for a step in the horizontal rule and the circle where the pages meet.
- Look for a seam in the three density bands. A visible seam means the two
  halves of every spread print at different densities, which is a much bigger
  constraint on the template library than any layout decision.

**Everything else** is checking that ordinary pages look right: page 6 for
white space and caption placement, page 9 for a single-page bleed next to a
white page (the worst case for a trim sliver), page 24 for black density.

Write the findings into `labs/saal.yaml` and this README. Phase 1 should not
start until the numbers in that file are measured rather than assumed.

## How the geometry works

Templates are authored on a **spread canvas** — one surface 2 pages plus bleed
wide. At export the canvas is sliced into two single media pages by shifting it
`-page_w` for the right-hand page. Consequences worth knowing:

- A panorama is not a special case. It is a slot covering the whole canvas.
- Each page's inner bleed duplicates 3mm of the facing page's trim area. If you
  place the two exported pages side by side on screen you will see 6mm of
  repeated content at the seam. That is correct — it is consumed by the trim,
  and it is what stops a white sliver appearing at the fold.
- Slot rectangles live in `templates_lib.py` as millimetres, not CSS. The
  renderer and the resolution validator read the same numbers, so they cannot
  drift apart.

## Chromium finding — do not remove `contain` from `.sheet`

Chromium's print path shrink-to-fits **the entire document** if any page's
content extends past the page box, and the spread canvas does exactly that by
design. It scales every page uniformly, silently, and without changing the PDF
page size — so `pypdf` still reports 216×216mm while a full-bleed image covers
only 67% of the page. `overflow: hidden` does not prevent it; `contain: layout
paint size` does.

`verify_pdf()` reads the drawn width of every full-bleed image back out of the
PDF content stream and fails the render if it is smaller than the media box.
That check is the regression test for this bug. Keep it.
