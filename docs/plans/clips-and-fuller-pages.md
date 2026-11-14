# Plan: short videos as photographs, and fuller pages

Status: **agreed; building in the order below. Nothing built yet.** Decisions already taken by the owner are
under "Decided". Open points are at the end.

Two requests:

1. Many 3-second videos are really photographs taken with the live/motion
   option on. Find the best frame of each and use it as a photograph.
2. Some pages still carry a lot of white. Fill it: with the photographs
   themselves, with maps, with more expressive typography, with ornaments.

## Decided

| Question | Answer |
|---|---|
| What the videos are | A mix: some stand alone, some sit beside a photo of the same name. Handle both. |
| Who picks the frame | The vision model, from a few sharp candidates. The user can override in the editor. |
| White space | All four remedies: fill with photographs, maps and routes, typographic features, ornaments. |
| The user's own words | **Styling only. Never rewritten.** |
| First steps | Measure empty space (B0) and probe the real clips (A0). |
| Paper tone | Later, and opt-in: not before a test print exists. |
| Typography | A choice of three pairings. |
| Dark pages | Allowed when a page's photographs are night scenes; the user can override per page. |

Two rules the project already has, and both parts keep:

- The model never emits coordinates. Everything below that places something
  on a page is computed by the system and re-applied to every version.
- The user outranks the model. Every automatic choice here has an override
  that is stored in `overrides.yaml` and survives a redesign.

---

## Part A — clips

A **clip** is a video short enough to be a photograph (default: 6 seconds or
less; a setting). Longer videos are listed as "not used" with the reason.

### A0. Look before building: `python phase1/clips.py probe <folder>`

Prints, per video: container and codec, size, duration, frame rate, rotation,
HDR or not, the date and GPS it carries, and whether a photo of the same name
sits beside it. Reason: phones differ a lot here, and three things below
(dates, HDR colour, pairing) can only be judged on the real files. **This runs
on the owner's folder first; its output decides the details of A1.**

### A1. Ingest and a local best frame (works with no model)

- `phase0/imaging.py` gains `CLIP_EXTENSIONS` (`.mp4 .mov .m4v .3gp`).
- **Pairing.** `IMG_1234.MOV` next to `IMG_1234.HEIC` (iPhone Live Photo) is
  not a new photograph: the photo is used and the clip is only its source of
  alternative frames (A4). A clip with no partner becomes a photograph whose
  id is the file name, like any other.
- **Decoding** with PyAV (`av` on PyPI: wheels for Linux, macOS and Windows
  with FFmpeg inside, nothing to install separately). Added to
  `requirements.txt` and the lock. If it is missing, clips are listed as
  unusable with one sentence saying what to install.
- **Candidates**, computed locally and free. Every frame is scored on a small
  grey copy: sharpness (variance of the Laplacian), exposure (share of blown
  or crushed pixels), stillness (difference from its neighbours). The first
  and last tenth are skipped, where the phone is still being raised or
  lowered. The four best frames at least 0.4 s apart are kept, at full
  resolution, in `<album>/derivatives/frames/<id>/`.
- Until A3 exists the top-scoring candidate is the photograph.
- **Date and place** from the container (`creation_time`, the QuickTime
  location atom, Apple's `creationdate` with its time zone). Known trap:
  `creation_time` is UTC while a photo's EXIF time is local, so a clip can
  land hours away from the photos taken beside it. Order of trust: a tag that
  carries a zone; else UTC shifted by the offset seen on the paired or
  nearest photo; else the date in a `VID-20260805-WA0019` style name; else
  the file time. Tested on the probe output before it is trusted.
- **Rotation** from the display matrix is applied, as EXIF orientation is.

**What a clip can and cannot print.** A 1080p frame is 1920 × 1080, about
2 megapixels. At the lab's 200 dpi warning line that is 24 × 14 cm; a 720p
frame, 16 × 9 cm. So a clip is a good half-page or composed-page photograph
and rarely a full bleed on a 30 × 30 book. The resolution rules that already
size down small photographs apply unchanged; nothing is upscaled. 4K clips
print like photographs.

**HDR.** iPhone clips are often HDR (HLG, Dolby Vision). Decoded naively they
come out grey and flat. A1 detects it and tone-maps to sRGB; how well that
works is checked on the owner's real clips at A0, and if it does not look
right those clips are flagged, not silently printed wrong.

### A2. Choosing another frame in the editor

The photo panel of a clip shows its candidates as a small strip, "frame 2 of
4", with previous and next. The choice is stored in `overrides.yaml`
(`frame: 3`), re-applied to every version, and costs no model call.

### A3. The vision model picks

In the vision pass a clip is sent as its four candidates (small copies) with
one extra question: which frame is best — eyes open, expression, nobody
mid-blink or mid-step, composition — and the usual annotation for that frame.
Stored in `annotations.json` (`frame`), `ANNOTATION_VERSION` goes up.

- Cost: four small images per clip where a photograph sends one. For 30 clips
  on a mid-priced cloud model, a few cents; free on a local vision model.
- Privacy: those four frames go wherever the annotate role points, exactly as
  photographs do today.
- The user's own choice (A2) always wins over the model's.

### A4. Live Photos: a better moment than the still

For a paired photo the editor offers "choose another moment", showing the
clip's candidates beside the still. Picking one swaps it in (override). The
still stays the default because it has several times the resolution.

### Not in this plan

Motion photos stored *inside* a JPEG (Google, Samsung) already load as
photographs; pulling other frames out of them comes later if wanted. No
stacking of several frames into a sharper one. No video in the web album.

---

## Part B — fuller pages

### B0. Measure first

Each page gets an **empty share**: the part of its printable area not covered
by a photograph or text. The validator reports it per page with the cause:

| Cause | How it arises |
|---|---|
| fitted, not cropped | the subject would be cut, so the photo is shown whole inside a slot of another shape (`geometry.fit_rect`) |
| too few pixels | the photo is printed smaller than its slot |
| thin spread | the page minimum (24 at Saal) stretched few photos over many pages |
| text alone | a title or text page with nothing beside it |

The editor's Book tab marks pages above a threshold, the way it marks
low-resolution pages today. This tells us which remedy is worth most on a real
album before any of them is built, and gives every later step a number to
improve.

### B1. Fill it with the photographs (the real fix)

- **Justified pages.** Today a page composition is a fixed 6 × 6 grid and a
  photo of the wrong shape is centred in its cell with white around it. A new
  composition family computes the cells *from the photographs' own shapes*:
  rows whose heights are solved so that every row fills the page width
  exactly, the method photo galleries use. No cropping, no leftover.
- **Crop as far as is safe.** Cropping is all or nothing today: either the
  crop keeps enough of the frame, or the whole photo is fitted. With the
  keep-box the vision pass already supplies, the photo can be cropped up to
  the edge of that box and no further.
- **Paper tone.** What is left is not bare white: the page takes a very light
  tone drawn from its photographs (or stays white — a setting, with three or
  four fixed tones as the safe choice). Print caveat: large flat tints can
  show banding and fingerprints on some papers, and must run into the bleed.
  **This goes into the calibration book before it is offered as a default.**

### B2. Typographic features — the user's words, never rewritten

Prerequisite: the fonts are bundled. The stylesheet still asks for whatever
the machine has, which the review already lists as a defect; display type
makes it urgent. Two or three open-licensed families (OFL) are vendored into
`phase0/assets/fonts/`, offered as named pairings.

- **Pull quote.** A description of up to about 140 characters is set large in
  the page's free area instead of small under the photo. Same words, once.
- **Display dates and places.** "5 AUG", "Morteratsch" as designed elements
  on chapter openings and in a free column, from data the system already has.
- **Chapter numerals**, drop capitals on text blocks, small-caps eyebrows.

Sources of text are fixed: the user's descriptions, the title and subtitle,
places, dates, landmark names from the list on the machine. Nothing is
invented and nothing of the user's is reworded.

### B3. Maps and routes — offline, from the photographs' GPS

- **Chapter map**: the chapter's places as dots, joined in the order the
  photographs were taken, with landmark names from the gazetteer. Drawn as
  vector line art in the book's ink colour.
- **Overview map** for the title or closing page: every chapter's place,
  numbered.
- Base geometry: coastlines, borders and large lakes from Natural Earth
  (public domain, 1–2 MB, bundled). No tiles, no network.
- **Honest limit:** that base is coarse. A chapter spread over a country gets
  a real map; a chapter inside one valley gets dots, a route, names, a scale
  bar and a north arrow — a schematic, and it will be called that. A richer
  small-scale map would need OpenStreetMap data, which this owner's network
  does not reach.

### B4. Ornaments

A small, restrained set drawn as vectors: hairline rules, one section mark,
page furniture (folio with place and date), a dotted line echoing the route.
One family per book. A setting: off, subtle (default), more. Never on a page
with a full-bleed photograph.

### How B2–B4 get onto a page: one "furniture" pass

A deterministic step in `phase1/pages.py`, beside the place stamps:

1. For each page, compute the free rectangles left by its slots.
2. If one is large enough, give it **one** element, by priority: pull quote,
   chapter map (first page of a chapter), display date or place, ornament.
3. A budget keeps the book calm: never the same kind on facing pages, never
   every page.

It is recorded in `spread.options.furniture`, drawn by `phase0/render.py`,
checked by `phase0/validate.py` (inside the safe area, not over a photo), and
re-applied to every version: instant, free, no model call. Settings switch
each kind on or off; a per-page override removes one element.

---

## Order of work

| Step | What | Size | Why here |
|---|---|---|---|
| 1 | B0 measure empty share | small | Shows which remedy matters; gives a number |
| 2 | A0 probe the real clips | small | Decides dates, HDR and pairing details |
| 3 | B1 justified pages, safe crop | large | Removes most white at the cause |
| 4 | A1 + A2 clips with local best frame and editor override | medium | Usable without any model |
| 5 | Fonts bundled, then B2 typography | medium | Fonts are a prerequisite and an open defect |
| 6 | A3 vision picks the frame | small | Builds on A1 |
| 7 | B3 maps | medium–large | Self-contained |
| 8 | B1 paper tone, B4 ornaments | small each | After a paper test |
| 9 | A4 another moment for Live Photos | small | Builds on A2 |

Each step is its own pull request with tests; steps 3, 5, 7 and 8 come with
before-and-after page images, because these are judged by eye.

## What cannot be verified from the development sandbox

- The owner's real clips (codecs, HDR, dates): hence A0 on the owner's machine.
- How tints, hairlines and display type look on paper: the calibration book.
- Whether fuller pages look *better*: before-and-after images and the owner's eye.

## Open points for the owner

1. Run the A0 probe on the clip folder and share its output.
