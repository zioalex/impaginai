# Running it on your own machine

Every command here was run from a clean `git clone` on a machine with nothing
pre-installed. Paths are relative to the repository root unless a step says
otherwise.

## Install

```bash
git clone https://github.com/zioalex/photo_album_maker
cd photo_album_maker

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt
playwright install chromium        # ~150 MB browser download
```

Python 3.11 or newer. The first time anything resolves GPS, the offline place
database builds an index that takes a few seconds; warm it now so it does not
happen mid-album:

```bash
python -c "import reverse_geocoder as rg; rg.search([(47.4, 8.6)])"
```

## Try it with the demo photographs

The repository ships 22 synthetic photographs with real EXIF — four locations
over four days — so you can see the whole thing work before touching your own
pictures.

```bash
python phase2/server.py --album ./albums/demo --photos phase1/demo_photos \
       --title "Sardinien 2026"
```

Open the address it prints (`http://127.0.0.1:8000`). You should see the
photographs grouped into four places with dates. Click any one of them to write
a description, choose how big it should be, give it an edge treatment, or leave
it out.

Without an API key the editor says so plainly and everything except *Design the
album* works. To design one:

```bash
export ANTHROPIC_API_KEY=sk-ant-...     # Windows: set ANTHROPIC_API_KEY=...
python phase2/server.py --album ./albums/demo --photos phase1/demo_photos
```

Press **Design the album**, wait a minute or two, then **Open preview**.

## Use your own photographs

```bash
python phase2/server.py --album ./albums/sardinien --photos ~/Pictures/sardinien \
       --title "Sardinien 2026" --subtitle "Zehn Tage am Meer" --language de
```

`--album` is a directory that gets created for you; it holds everything about
this album and nothing about your photographs, which are never modified.
`--language` is the language the album's own titles and connective text are
written in (`de`, `it`, `en`).

Useful extras:

| Flag | What it does |
|---|---|
| `--format 30x30` | page size; must exist in `phase0/labs/saal.yaml` |
| `--gap-hours 4` | a gap longer than this starts a new group |
| `--radius-m 400` | moving this far starts a new group |
| `--no-geocode` | skip place names entirely |
| `--host 0.0.0.0 --port 8000` | reachable from other machines on your network |

### Naming places yourself

The offline database returns the nearest settlement, which is often not what you
call the place. Create `albums/sardinien/places.yaml`:

```yaml
- name: Cala Goloritzé
  lat: 40.1150
  lon: 9.6900
  radius_m: 2000
```

Any photograph taken within the radius uses your name instead. Restart the
editor to pick it up.

## The command line, if you prefer it

The editor and the CLI read and write the same files, so you can mix them
freely — set descriptions in the browser, then plan from a terminal, or the
reverse. **Run these from inside `phase1/`.**

```bash
cd phase1

# look at each photograph once; the result is cached and reused
python album.py annotate --album ../albums/sardinien --photos ~/Pictures/sardinien

# design the whole album in one call
python album.py plan --album ../albums/sardinien --photos ~/Pictures/sardinien \
       --title "Sardinien 2026" --subtitle "Zehn Tage am Meer"

# render the proof PDF
python album.py proof --album ../albums/sardinien --photos ~/Pictures/sardinien

# change something, in plain language
python album.py revise --album ../albums/sardinien --photos ~/Pictures/sardinien \
       --comment "Seite 4 ist überladen, und die Kirchenbilder früher"

python album.py history  --album ../albums/sardinien
python album.py rollback --album ../albums/sardinien --to 2
```

Add `--dry-run` to `plan` to write the exact prompt to `prompt-plan.txt` and
stop without calling the model — useful for seeing what it is actually being
asked, and free.

Captions can also come from a file instead of the editor:

```bash
python album.py plan --album ../albums/sardinien --photos ~/Pictures/sardinien \
       --captions my-captions.yaml
```

`my-captions.yaml` is `photo_id: caption`, one per line; see
`phase1/examples/demo_captions.yaml`. A description set in the editor wins over
one in that file.

## Getting the print files

The editor produces a proof — low resolution, small file, for looking at. When
you are happy with it, render the real thing from `phase0/`:

```bash
cd phase0
python render.py print --layout ../albums/sardinien/layout-v003.json \
       --photos ~/Pictures/sardinien --out ../albums/sardinien/renders
python render.py cover --layout ../albums/sardinien/layout-v003.json \
       --photos ~/Pictures/sardinien --out ../albums/sardinien/renders
```

Use the highest-numbered `layout-v*.json` in the album directory (the editor
writes one each time it designs), or check `history` for the version you want.
`layouts/vNNN.json` holds the same content under the version store.

The cover is designed by the planner along with the pages. If `cover` renders
and tells you the layout has no cover section, that layout predates this and
needs designing again, or you can add the section by hand — the message shows
the shape. Upload `inner-print.pdf` and `cover.pdf` to
the lab as **two separate files**.

**Before ordering anything**, read `phase0/README.md`. The cover constants in
`phase0/labs/saal.yaml` are considered guesses until measured against a real
printed book, and the spine is computed from them.

## What lives where

Everything about an album is plain text in its own directory. Nothing is in a
database; nothing is hidden.

```
albums/sardinien/
  overrides.yaml      your per-photo choices — the editor writes this
  places.yaml         your own names for places (you write this)
  annotations.json    cached result of the one pass that looks at photographs
  places_cache.json   cached GPS lookups, so runs are repeatable
  versions.json       version history
  layouts/v001.json   each version of the layout
  layout-v001.json    the version handed to the renderer
  renders/            the PDFs
  derivatives/        resized copies of your photographs
```

Deleting `derivatives/` and `renders/` is always safe — they are rebuilt.
Deleting `annotations.json` means paying for the vision pass again.

## Running it as a service

For a homelab box, one container, started once and left alone:

```dockerfile
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
      libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 \
      libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libasound2 \
      fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && playwright install chromium
RUN python -c "import reverse_geocoder as rg; rg.search([(47.4, 8.6)])"
COPY . .
EXPOSE 8000
CMD ["python","phase2/server.py","--album","/data/album","--photos","/data/photos","--host","0.0.0.0"]
```

```bash
docker build -t photo-album-maker .
docker run -p 8000:8000 -v /srv/albums/sardinien:/data \
  -e ANTHROPIC_API_KEY=sk-ant-... photo-album-maker
```

Put your photographs in `/srv/albums/sardinien/photos`. Behind Traefik it is an
ordinary HTTP service — no websocket, no build step, so a plain router rule is
enough.

## Fonts, and why a rebuild can change your book

The stylesheet asks for Charter and Carlito and falls back to whatever the
system has. **A font substitution silently reflows every caption**, so a book
rendered on your laptop and re-rendered in Docker can differ. Render your final
print files in the same place every time, or vendor real WOFF2 files into
`phase0/assets/fonts/` and reference them with `@font-face`. The Docker image
above installs DejaVu so that something predictable is always present.

## Choosing the model

Nothing about sequencing a photo album is vendor-specific, so the project speaks
two wire protocols and you pick the endpoint:

| `--provider` | Talks to |
|---|---|
| `anthropic` (default) | the Anthropic Messages API |
| `openai` | anything speaking `/chat/completions` — OpenRouter, OpenAI, Together, Groq, vLLM, Ollama, LM Studio, llama.cpp |

Giving `--base-url` implies `--provider openai`, because that is almost always
what is meant. These shorthands expand for you: `openrouter`, `openai`,
`together`, `groq`, `ollama`, `lmstudio`, `vllm`, `llamacpp`.

**OpenRouter**

```bash
export OPENROUTER_API_KEY=sk-or-...
python phase2/server.py --album ./albums/sardinien --photos ~/Pictures/sardinien \
       --base-url openrouter --model anthropic/claude-sonnet-4.5
```

**A model on your own machine** — no key needed, since localhost endpoints are
treated as keyless:

```bash
ollama serve &
ollama pull qwen2.5vl
python phase2/server.py --album ./albums/sardinien --photos ~/Pictures/sardinien \
       --base-url ollama --model qwen2.5vl
```

**OpenAI**

```bash
export OPENAI_API_KEY=sk-...
python phase2/server.py --album ./albums/sardinien --photos ~/Pictures/sardinien \
       --base-url openai --model gpt-4o
```

Every entry point takes the same flags, or set them once in the environment:

```bash
export ALBUM_PROVIDER=openai
export ALBUM_BASE_URL=openrouter
export ALBUM_MODEL=anthropic/claude-sonnet-4.5
export ALBUM_API_KEY=sk-or-...
export ALBUM_VISION=1          # 0 if the model cannot see images
export ALBUM_JSON_MODE=0       # 1 to ask the endpoint to guarantee JSON
```

### What the model has to be able to do

Measured on this project, not estimated:

| | 22 photographs | ~100 photographs |
|---|---|---|
| Planning call input | ~3,000 tokens | ~6,000 tokens |
| Planning call output | ~1,100–1,400 tokens | ~6,000 tokens |
| Annotate call | 8 images + ~400 tokens per batch | same, more batches |

So **context is not the constraint** — 16K is comfortable, 32K is generous.
What actually decides whether a model can do this job, in order of how often it
is the thing that fails:

1. **Emitting valid JSON against a schema, repeatedly.** Output is validated and
   rejected on failure with the error fed back, three attempts, then it gives
   up rather than write a broken album. This is the whole game. `--json-mode`
   helps on endpoints that support it.
2. **Holding a long instruction set.** The system prompt is ~9,000 characters
   of layout rules — template catalogue, grouping, pacing, the fold rule, page
   budgets. A model that follows only the last few paragraphs will produce
   valid JSON that ignores your constraints, which is worse than failing.
3. **Vision, for the annotate pass only.** Optional. Without it the planner
   works from captions, timestamps, GPS and pixel dimensions.

### A different model per job

The three calls are not the same job, and one model for all of them is either
overpaying or underperforming. What each actually needs:

| Role | Calls per album | Vision | Output | What decides success |
|---|---|---|---|---|
| `annotate` | one per 8 photographs, **then cached forever** | **required** | a small JSON array | nothing much — a soft subject line barely moves the layout |
| `plan` | exactly one | no | 1.4k–6k tokens of schema-valid JSON | obeying a ~9,000-character brief; this is what makes the book good or flat |
| `revise` | one per comment | no | a complete new layout | changing what was asked and leaving the rest alone |

So the sensible split is: **annotate on whatever is cheapest or local, plan on
the best model you will pay for, revise in the middle.** Annotation runs once
and is cached, so a local GPU doing it costs nothing per album. Planning is a
single call where quality decides the result, which makes it the one place
frontier pricing is justified — and it is one call, so it costs cents.

Put that in a `models.yaml`:

```yaml
# A `default` block, then only what each role overrides.
default:
  base_url: openrouter
  model: anthropic/claude-sonnet-5.5

annotate:
  base_url: ollama            # local GPU, no key, free
  model: qwen3-vl:32b

plan:
  model: anthropic/claude-opus-5.5    # the call that matters
  json_mode: true

# revise inherits the default
```

```bash
python phase2/server.py --album ./albums/sardinien --photos ~/Pictures/x \
       --models models.yaml
```

Both the CLI and the editor print the resolved routing at startup:

```
  annotate  qwen3-vl:32b via http://localhost:11434/v1
  plan      anthropic/claude-opus-5.5 via https://openrouter.ai/api/v1
  revise    anthropic/claude-sonnet-5.5 via https://openrouter.ai/api/v1
```

Everything is overridable without a file. Precedence, strongest first:

1. a role flag — `--plan-model`, `--annotate-base-url`
2. a role environment variable — `ALBUM_PLAN_MODEL`, `ALBUM_ANNOTATE_BASE_URL`
3. the role's block in `models.yaml`
4. the generic flag or `ALBUM_MODEL` / `ALBUM_BASE_URL`
5. the built-in default

```bash
# try a cheaper planner for one run, leaving everything else alone
python phase1/album.py plan --album ../albums/x --photos ./demo_photos \
       --models models.yaml --plan-model deepseek/deepseek-v4.1-flash
```

`plan` and `revise` never send images, so vision is forced off for them
whatever the config says — a text-only model is a perfectly good planner. Only
`annotate` needs to see, and if its endpoint has no key or no vision the pass
is skipped and the planner falls back to captions, timestamps, GPS and pixel
dimensions.

### Running it on a 24 GB GPU

A 24 GB card (RTX 3090, 4090, 5090) fits a capable vision model at 4-bit with
room for image spikes. Leave 1–2 GB free: high-resolution images briefly push
VRAM above the model's resting size.

| Model | Fits in 24 GB | Vision | Runner | Notes |
|---|---|---|---|---|
| Qwen3-VL 32B (Q4) | ~21 GB | yes | Ollama (`qwen3-vl:32b`) | the straightforward first try; one model for both passes |
| Qwen 3.6-27B dense (Q4_K_M) | ~17 GB | yes | llama.cpp, LM Studio | strongest local vision at this tier; **not in Ollama**, which lacks mmproj support |
| Qwen 3.6-35B-A3B MoE (Q4) | ~22 GB | yes | llama.cpp, LM Studio | faster than the 27B dense, 3B active |
| Gemma 4 31B (Q4) | ~20 GB | yes | Ollama (`gemma4:31b`) | best vision benchmarks of this group |
| Gemma 3 27B QAT (int4) | ~14 GB | yes | Ollama (`gemma3:27b`) | leaves the most headroom |

```bash
ollama serve &
ollama pull qwen3-vl:32b
python phase2/server.py --album ./albums/sardinien --photos ~/Pictures/sardinien \
       --base-url ollama --model qwen3-vl:32b --json-mode
```

**A split setup is usually the better answer.** The two jobs have different
shapes: annotate is 22+ short vision calls where a local model costs nothing
and a mistake is cheap, while planning is one long-instruction reasoning call
where quality decides whether the book is any good. So annotate locally, plan
remotely:

```bash
# once, locally and free
python phase1/album.py annotate --album ../albums/sardinien --photos ~/Pictures/x \
       --base-url ollama --model qwen3-vl:32b

# then plan with a stronger model — annotations are already cached
python phase1/album.py plan --album ../albums/sardinien --photos ~/Pictures/x \
       --base-url openrouter --model anthropic/claude-sonnet-5.5
```

Expect to test instruction-following yourself. Published benchmarks for these
models cover OCR and chart reading, not "obeys a 9,000-character layout brief",
and that is the capability this project needs. `--dry-run` shows the exact
prompt; if a model produces valid JSON that ignores the pacing and fold rules,
it has failed in the way that matters and no retry will fix it.

### On OpenRouter, and what it actually costs

Measure before optimising. Using the token counts this project really produces
— annotate at roughly 8 images plus 400 tokens per batch, planning at 6k in and
6k out — a whole album including three revisions costs:

| Model | in / out per 1M | 40 photographs | 100 photographs |
|---|---|---|---|
| `anthropic/claude-opus-5.5` | $4 / $20 | $0.79 | $1.12 |
| `anthropic/claude-sonnet-5.5` | $2 / $10 | $0.39 | $0.56 |
| `google/gemini-4-argon` | $2 / $10 | $0.39 | $0.56 |
| `deepseek/deepseek-v4.1-flash` | $0.15 / $0.60 | $0.026 | $0.038 |
| `anthropic/claude-haiku-5.5` | $0.10 / $0.50 | $0.020 | $0.028 |
| `openai/gpt-6-luna` | $0.10 / $0.50 | $0.020 | $0.028 |
| `z-ai/glm-5.3-flash` | $0.028 / $0.21 | $0.007 | $0.010 |
| `qwen/qwen3.6-35b-a3b` | $0.01 / $0.09 | $0.003 | $0.004 |
| `openai/gpt-oss-20b` | $0.008 / $0.03 | $0.001 | $0.002 |

**A whole album costs about a dollar on the most expensive model available.**
So switching to a cheap model saves roughly one dollar per book — against a
print that costs CHF 40-100. If money is the only reason to go cheaper, it is
not a good reason here.

The reasons that are good: keeping your photographs off third-party servers,
working without a connection, or not wanting an account. Those all point at a
local model, not a cheap hosted one.

Note which call dominates: **annotate, not planning**, because images are
expensive and planning is one text call. That is why the recommended split
puts annotate on your own GPU — it removes most of the cost and all of the
privacy question at once.

A reasonable cheap stack, if you want one:

```yaml
default:
  base_url: openrouter
  model: anthropic/claude-haiku-5.5    # ~$0.02 per album, all three roles
plan:
  model: deepseek/deepseek-v4.1-flash  # or keep Haiku; benchmark both
  json_mode: true
```

And the cheapest sensible arrangement, which is also the most private:

```yaml
default:
  base_url: ollama
  model: qwen3-vl:32b                  # everything local, $0.00
plan:
  base_url: openrouter
  model: anthropic/claude-sonnet-5.5   # ~$0.07 per album for the one call
  json_mode: true                      #   that decides the result
```

Verify vision and structured-output support on each model's own OpenRouter
page — the capability flags there are authoritative and third-party summaries
frequently are not. On OpenRouter you can also send
`provider.require_parameters: true` so a provider that would silently ignore
JSON mode is skipped.

### Shrinking the images, which is the real saving

Vision cost is driven by image **dimensions**, not file size. Anthropic charges
about (w x h) / 750 tokens; OpenAI-compatible endpoints tile at 512px. **JPEG
quality therefore changes your upload and not your bill** — compressing harder
saves bandwidth and nothing else, which is the opposite of most people's
intuition.

What this project sends is a separate, small copy built for the purpose
(`derivatives/vision512/`), not the 1200px proof derivatives, which exist to
render a readable proof PDF and stay that size. Measured over 100 photographs:

| Long edge | Anthropic tokens | OpenAI tokens | vs 1200px |
|---|---|---|---|
| 1200px | 140,000 | 64,000 | — |
| 768px | 59,000 | 64,000 | 0.42x |
| **512px (default)** | **26,000** | **25,500** | **0.19x** |
| 448px | 20,000 | 25,500 | 0.14x |

512px is the default because of what the pass is actually asked for: a subject
in a few words, a count of faces, a scene type, a focal point and a 1-5 rating.
That is contact-sheet work. A focal point read from a 512px frame is accurate
to about 0.2% of the frame width, far finer than the decision it informs.

Because annotation is cached, this is paid once per album either way — but it
is still the largest single lever, since images dominate the token count while
planning is one text call.

To change it:

```bash
python phase1/album.py annotate --album ../albums/x --photos ~/Pictures/x --vision-px 768
python phase2/server.py --album ./albums/x --photos ~/Pictures/x --vision-px 448
```

**How small is too small depends on your photographs, not on this table.**
Landscapes survive aggressive downscaling; group shots where the model must
count faces do not. Measure it on your own pictures:

```bash
python phase1/tools/bench_vision.py --photos ~/Pictures/sardinien \
       --base-url ollama --model qwen3-vl:32b \
       --sizes 1200 768 512 384 --sample 12
```

It annotates the same photographs at each size, treats the largest as the
reference, and reports how often the smaller ones agree:

```
    size  tok vs ref  SUBJECT   PEOPLE    SCENE    FOCAL STRENGTH
     768       0.41x     100%     100%     100%     100%     100%
     512       0.18x     100%     100%     100%     100%     100%
     384       0.10x     100%     100%     100%      60%     100%
```

Take the smallest size where **PEOPLE and FOCAL** still hold — those two decide
cropping and how prominently a photograph is placed. SUBJECT and SCENE drift
harmlessly. In the run above, 512px is free of charge relative to 384px in
quality terms and costs a fifth of the reference.

### Benchmark a model instead of trusting a table

Published benchmarks measure OCR and exam questions. None of them measure
"obeys a 9,000-character layout brief", which is the only capability that
decides whether a cheap model can replace an expensive one here. So measure it:

```bash
python phase1/tools/bench_model.py --photos phase1/demo_photos \
       --base-url openrouter \
       --compare anthropic/claude-haiku-5.5 deepseek/deepseek-v4.1-flash \
                 z-ai/glm-5.3-flash qwen/qwen3.6-35b-a3b
```

It runs the real planning prompt and scores the result against the real
validator:

```
model                          score  try    sec     tok in/out  SCHE PRIN FOLD DENS CHRO GROU COHE VARI SOFT COVE
anthropic/claude-haiku-5.5     10/10    1    8.4  3,012/1,388      ok   ok   ok   ok   ok   ok   ok   ok   ok   ok
some-cheap-model                3/10    1    2.1  3,012/1,402      ok FAIL FAIL   ok FAIL FAIL   ok FAIL FAIL FAIL
```

| Check | Question |
|---|---|
| SCHEMA | did it return JSON this project can load at all |
| PRINTABLE | does the validator pass it for print |
| FOLD | did it keep every photograph out of the gutter |
| DENSITY | did it stay inside the empty-page budget |
| CHRONOLOGY | does the album run forwards in time |
| GROUPING | is each moment kept in one run, not scattered |
| COHESION | does each spread draw from one group |
| VARIETY | did it use more than one or two templates |
| SOFTNESS | is every photograph sharp enough for the lab |
| COVER | did it design a cover |

**The failure mode to watch for is a model that passes SCHEMA and little
else** — valid JSON that ignores the brief. Retrying never fixes it, the
validator will not block it, and the result is a book that looks arbitrary. The
benchmark exists to catch exactly that, because it is invisible in a price
table. `--save DIR` writes each model's layout so you can render and look.

Run it on your own photographs rather than the demo set when deciding for real:
the demo photographs are synthetic and easier to sequence than a holiday.

## What needs the internet

Only the model calls — `annotate`, `plan` and `revise`, which is the
*Design the album* button. Everything else runs locally: reading photographs
and EXIF, place names, grouping, the editor, rendering, and every print check.

And even that is only true of a *hosted* model. Point `--base-url` at Ollama,
LM Studio, vLLM or llama.cpp on your own machine and the whole pipeline runs
with the network unplugged, start to finish.

### Installing on a machine that will never be online

On a machine **with** a connection:

```bash
mkdir -p offline-bundle && cd offline-bundle
pip download -r ../requirements.txt -d wheels
tar czf chromium.tgz -C ~ .cache/ms-playwright
```

Copy that directory across, then on the target machine:

```bash
pip install --no-index --find-links=wheels -r requirements.txt
tar xzf chromium.tgz -C ~
```

If Playwright still reports a missing browser, point `PLAYWRIGHT_BROWSERS_PATH`
at wherever you unpacked it.

## If something goes wrong

**`can't open file 'album.py'`** — the CLI lives in `phase1/`; `cd phase1` first.

**`ModuleNotFoundError`** — the virtual environment is not activated. Run
`source .venv/bin/activate` in the shell you are using, or call the interpreter
directly as `.venv/bin/python`.

**`no photos found`** — `--photos` must point at a directory containing
`.jpg`, `.jpeg`, `.png`, `.tif` or `.webp` files. HEIC from an iPhone needs
`pip install pillow-heif`.

**The editor starts but the grid is empty** — the photographs were found but
thumbnails failed. Check the terminal for the error.

**`Executable doesn't exist` from Playwright** — `playwright install chromium`
was not run inside the activated virtual environment.

**Place names are missing** — the photographs have no GPS, or
`reverse_geocoder` is not installed. Neither is fatal; the album is built
without them.

**Designing fails with an authentication error** — the key is unset or wrong in
the shell that started the server. It is read at startup, so export it first and
restart. The startup line prints which model and endpoint it resolved, which is
the quickest way to see whether it picked up what you intended.

**`no API key for provider ...`** — either set the key the message names, or
point `--base-url` at a local endpoint, which needs none.

**The model keeps failing validation** — it is not following the output schema.
Try `--json-mode` if the endpoint supports it, or a stronger model. The planner
retries three times with the validation error fed back and then stops rather
than writing a broken album.

**The preview looks shrunk, with white margins where a photograph should bleed**
— something removed `contain: layout paint size` from `.sheet` in
`phase0/assets/style.css`. Put it back; `phase0/README.md` explains why.
