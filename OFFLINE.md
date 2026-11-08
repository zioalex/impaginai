# Running without an internet connection

Everything in this project runs on your own machine. Exactly one part needs the
internet, and it is worth being precise about which:

| Step | Needs the internet? |
|---|---|
| Reading photographs, EXIF, GPS | no |
| Place names from GPS | **no** — offline database, see below |
| Grouping by time and place | no |
| The web editor: previews, descriptions, sizes, treatments | no |
| Rendering the proof and print PDFs | no |
| Validation: resolution, bleed, page count, spine, chronology | no |
| **Designing the album** (the `annotate`, `plan` and `revise` calls) | **yes** — Anthropic API |

So: you can prepare an entire album, describe every photograph, decide what is
big and what is left out, and render a PDF, all offline. The one thing that
needs a connection is asking the model to sequence and lay it out. There is no
offline substitute for that, and pretending otherwise would be dishonest —
but note that it is a handful of API calls per album, not a dependency on being
online while you work.

## One-time setup, with a connection

```bash
git clone https://github.com/zioalex/photo_album_maker
cd photo_album_maker
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium          # ~150 MB, downloads a browser build
python -c "import reverse_geocoder as rg; rg.search([(47.4,8.6)])"   # warms the geocoder
```

That last line matters: `reverse_geocoder` builds a cached index on first use.
Doing it now means the first album does not stall.

## Setup on a machine that has never been online

On a machine **with** a connection, build a bundle:

```bash
mkdir -p offline-bundle && cd offline-bundle
pip download -r ../requirements.txt -d wheels
# Chromium for Playwright — copy the whole browser cache
tar czf chromium.tgz -C ~ .cache/ms-playwright
```

Copy `offline-bundle/` to the target machine, then:

```bash
pip install --no-index --find-links=wheels -r requirements.txt
tar xzf chromium.tgz -C ~
```

Check `~/.cache/ms-playwright` exists afterwards. If Playwright still reports a
missing browser, set `PLAYWRIGHT_BROWSERS_PATH` to wherever you unpacked it.

## Docker

The homelab target. One image, no network at run time except for the API call.

```dockerfile
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
      libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 \
      libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libasound2 \
      fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && playwright install chromium
RUN python -c "import reverse_geocoder as rg; rg.search([(47.4,8.6)])"
COPY . .
EXPOSE 8000
CMD ["python","phase2/server.py","--album","/data/album","--photos","/data/photos","--host","0.0.0.0"]
```

Mount your photographs and album directory at `/data`, and pass the key:

```bash
docker run -p 8000:8000 -v /srv/albums/sardinien:/data \
  -e ANTHROPIC_API_KEY=... photo-album-maker
```

Behind Traefik, the editor is a single HTTP service with no websocket and no
build step — a plain reverse-proxy rule is enough.

## Fonts

The stylesheet asks for Charter and Carlito and falls back to whatever the
system has. **A font substitution silently reflows every caption**, so a book
rendered on your laptop and re-rendered in Docker can differ. Before ordering a
print, render in the same container you will always use, or vendor real WOFF2
files into `phase0/assets/fonts/` and reference them with `@font-face`. The
Docker file above installs DejaVu so that something predictable is present.

## Checking it works offline

```bash
python phase1/tools/make_demo_photos.py
python phase2/server.py --album /tmp/check --photos phase1/demo_photos
```

Open the address it prints. You should see 22 photographs grouped into four
places with dates, and be able to edit descriptions and sizes. The editor will
tell you plainly that the album cannot be designed if no API key is set —
everything else still works.

To check the render chain without any key at all:

```bash
cd phase0
python tools/make_placeholder_photos.py
python render.py proof --layout examples/layout_test24.json --photos ./photos --allow-fold
```

## What is stored where

Everything about an album lives in its own directory, as plain text you can read
and edit by hand:

```
albums/sardinien/
  overrides.yaml      your per-photo choices — the editor writes this
  places.yaml         your own names for places (overrides the geocoder)
  annotations.json    cached result of the one call that looks at photographs
  places_cache.json   cached GPS lookups, so runs are repeatable
  versions.json       version history
  layouts/v001.json   each version of the layout
  renders/            the PDFs
  derivatives/        resized copies of your photographs
```

Nothing is in a database, nothing is hidden. Deleting `derivatives/` and
`renders/` is always safe — they are rebuilt. Deleting `annotations.json` means
paying for the vision pass again.
