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

### On OpenRouter

Prices move constantly, so check the model's own page; these are November 2026
figures per million tokens. An album is a handful of calls, so even the
expensive options cost cents.

| Model | In / out | Why |
|---|---|---|
| `anthropic/claude-sonnet-5.5` | $2 / $10 | the default choice for planning: long-instruction adherence is what this job needs |
| `anthropic/claude-opus-5.5` | $4 / $20 | for a book that matters; same shape, better judgement |
| `anthropic/claude-haiku-5.5` | $0.10 / $0.50 | cheap, and strong enough for the annotate pass |
| `google/gemini-4-argon` | $2 / $10 | strong multimodal alternative, long context |
| `deepseek/deepseek-v4.1-flash` | $0.15 / $0.60 | cheapest credible planner; verify it respects the layout rules |
| `openai/gpt-6-luna` | $0.10 / $0.50 | cheap structured output; good annotate candidate |

Set it once:

```bash
export OPENROUTER_API_KEY=sk-or-...
export ALBUM_BASE_URL=openrouter
export ALBUM_MODEL=anthropic/claude-sonnet-5.5
```

Verify vision and structured-output support on the model's OpenRouter page
before relying on it — the capability flags there are authoritative, and
third-party summaries frequently are not.

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
