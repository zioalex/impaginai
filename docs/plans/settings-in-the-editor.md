# Plan: every backend option in the editor

Status: **reviewed; decisions recorded under "Decisions" at the end.** Built in steps A–D. **All four steps are built** (Settings tab: book, grouping, places — `phase2/config.py`; AI models and keys — `phase1/keystore.py`, `/api/models`, `/api/keys`, `/api/estimate`). Step C: `open_album()`, welcome screen, folder browser, new/open/recent albums, drag-and-drop uploads (`/api/albums`, `/api/fs`, `/api/upload`). Step D: model lists from the endpoints (`/api/models/list`), *Save as models.yaml* (`/api/models/export`), where new albums are made (`/api/library`), docs. The full `open_album()` refactor moves to step C, where switching albums needs it; step A applies option changes live with `apply_options()`.

## Goal

Today the editor is started like this:

```bash
python phase2/server.py --album ./albums/engadin --photos ~/Pictures/engadin \
       --models presets/cloud-best.yaml --gap-hours 4 --vision-px 512 ...
```

Every choice that is not a photograph, caption or book size lives in command-line
flags, `models.yaml` or environment variables. To change one you stop the
server, edit a file, and start it again.

Goal: **`python phase2/server.py` and nothing else.** Everything is chosen,
changed and explained in the editor, and the settings are remembered. Flags
keep working for scripting, and a flag still wins over what the GUI says.

## What the editor will control

Grouped as they would appear: a new **Settings** tab next to *Photos* and
*Book*, with one section per group.

Every option shows **what a change costs**, because users fear the expensive
button:

| badge | meaning |
|---|---|
| `instant` | re-applied to the current design at once, no model call, free |
| `regroup` | photographs are regrouped and re-applied, free |
| `redesign` | takes effect on the next *Design again…*, about one planning call |
| `re-look` | the vision pass looks at every photograph again, roughly $0.01–0.10 per 100 photos depending on the model |

### 1. Album: photos and where the work is saved

| option | today | in the editor |
|---|---|---|
| Photo folder | `--photos` (required) | **Choose folder…** opens a folder browser served by the backend (a browser page cannot see local paths). It shows the number of photos found and how many are HEIC. |
| Album (work) folder | `--album` (required) | **New album** / **Open album** / **Recent albums** list. Each album keeps its own photos folder, settings, versions and costs, as it does now. |
| Add photos | copy files by hand | Drag and drop onto the Photos tab; files are copied into the photo folder. *(Phase C.)* |
| Captions file | `--captions` | Kept as an advanced option; the per-photo caption box already replaces it. |

Switching albums happens without restarting the server (see Architecture).

### 2. Book

| option | today | in the editor | badge |
|---|---|---|---|
| Title, subtitle, language, size | editor ✓ | unchanged | instant / redesign |
| Print lab | `--lab` | Dropdown of `phase0/labs/*.yaml` (only Saal today). Shows min/max pages and DPI rules, read-only. More labs come from the Swiss print-lab research story. | instant |
| **Length** | the model decides | *Automatic* or a target page count (stepper, held between the lab's min and max) | redesign |
| **Density** | fixed in the prompt | *Airy · Balanced · Dense* (about 1.5 / 2.5 / 3.5 photos per page). Feeds the prompt and how eagerly the repacker composes pages. | redesign (repacker part instant) |
| **Selection** | the model may leave photos out | *Use every photo · Best of (model picks) · Only the strongest* | redesign |
| Captions | personal, by the model | *Personal (we/our) · Descriptive · Place and date only · None*; your own captions are always kept | redesign |
| Short-book policy | blank pages at the end | *Pad with blank pages · Split compositions first* (today: split first) | instant |

Density and Selection each have an **ⓘ info pop-up** that explains the
difference between their choices in plain words, with the effect on the
number of pages: e.g. *Dense* = more photos per page, fewer pages, smaller
photos; *Best of* = the model leaves out weak or repeated frames, *Use every
photo* = nothing is left out, the book gets longer.

Length, density and selection are the three levers that most decide whether an
album feels designed. Today they are only reachable through the
*Design again…* comment box.

### 3. AI models

Three roles with different jobs (already in `providers.py`): **annotate**
(looks at photos, vision), **plan** (designs the book) and **revise** (applies
comments).

| option | today | in the editor |
|---|---|---|
| Preset | `--models presets/*.yaml` | Cards: *Best result · Budget · Hybrid (local vision) · Fully local*, each showing the measured cost for 100 photos, taken from the preset's header. Picking one fills the fields below. |
| Per role: endpoint | `base_url` | Dropdown of the known aliases (OpenRouter, Anthropic, OpenAI, Ollama, LM Studio…) plus *Custom URL*. |
| Per role: model | `model` | Text field with suggestions for that endpoint. For OpenRouter and Ollama the list comes from the endpoint itself (`/models`, `/api/tags`). |
| Per role: **effort** | `reasoning: default/low/off` | *Off · Low · Default · High*. **High is new**: it maps to Anthropic extended thinking, OpenAI `reasoning_effort: high` and OpenRouter `reasoning.effort`. **Offered for the planner only** to start with (see Decisions). |
| Per role: output budget | `max_tokens` | Number field, hidden under *Advanced*. |
| Per role: fallback model | `fallback_model` | Text field with a hint to choose another vendor. |
| Per role: JSON mode, vision | flags | Advanced toggles, set by the preset. |
| API keys | environment variables | **Write-only** password fields, one per provider in use. **A key from the environment always wins** over one entered in the GUI. The backend reports only *from environment / your key / missing* and a masked form (`sk-or-…a1b2`), never the key. See Security. |
| Test | runs at startup | **Test models** button (the existing preflight), plus a green/red dot per role. |
| Cost so far | editor ✓ | unchanged; plus an *estimated cost of the next design* next to the button |

### 4. Photos and grouping

| option | today | in the editor | badge |
|---|---|---|---|
| New moment after | `--gap-hours 4` | Slider, 1–24 h, with a live preview: "→ 7 moments" | regroup |
| New place after | `--radius-m 400` | Slider, 100 m – 5 km, live group count | regroup |
| Vision detail | `--vision-px 512` | *Standard (512) · Detailed (768)* with the cost difference | re-look (new photos only, unless forced) |
| Auto-rotate sideways photos | always on | Toggle | re-look |
| Look again at all photos | `annotate --force` | Button with a confirmation and estimated cost | re-look |

### 5. Places and privacy

| option | today | in the editor |
|---|---|---|
| Place names | `--no-geocode` | Toggle: names towns from GPS, offline database |
| Named landmarks (lakes, glaciers, peaks) | `--offline-places` | Toggle, with the plain-language note that it **sends photo coordinates to overpass-api.de** |
| Album language for place names | follows album language | unchanged |

### 6. Shown, not editable

- **Host and port**: the server is already listening, so these stay as flags.
  The Settings tab shows the address and warns if it is not `127.0.0.1`
  (keys and folders are exposed to whoever can reach it).
- DPI thresholds, bleed, trim: they belong to the lab file. Shown read-only
  with the lab.

## Where settings are stored, and which one wins

| scope | file | holds |
|---|---|---|
| per album | `<album>/settings.json` (exists; extended) | photo folder, title, size, language, front photo, length, density, selection, captions style, grouping, vision detail, place options, chosen preset and models |
| per user | `~/.config/photo_album_maker/config.json` | recent albums, last preset and models, default options for new albums |
| per user, secret | `~/.config/photo_album_maker/keys.json`, mode `0600` | API keys. Never in an album folder, so an album can be zipped and shared safely. |

Precedence, highest first: **command-line flag → environment variable →
album settings → user config → built-in default**. A setting pinned by a flag
or environment variable is shown in the GUI, locked, with *"set by
--gap-hours when the server was started"*, so the GUI never silently fails to
apply something.

`models.yaml` and the presets keep working: `--models` pins the model section
for that run, and **Save as models.yaml…** exports the GUI choice for the CLI.

## Architecture

1. **One `Config` object** (`phase2/config.py`): a dataclass of every option
   above with its default, its source and its effect badge, plus
   load/merge/save. `main()` builds it from flags, environment and files;
   nothing reads `args` after that.
2. **`open_album(config)`** replaces the startup code at the bottom of
   `main()` (load lab, geometry, providers, photos, groups, thumbnails,
   preflight). That is what lets the editor switch albums, and change models,
   grouping or the photo folder, without a restart. It is refused while a job
   is running (as settings are now: 409 busy).
3. **`providers.resolve_all` from a dict** as well as from args and
   `models.yaml`, so the GUI's model choices go through the same validation,
   aliases and inheritance (`default` → role).
4. **API**
   - `GET /api/config`: every option with its value, source, whether it is
     locked, its effect badge and the allowed values.
   - `PUT /api/config`: partial update. Validates, saves to the right file,
     applies, and returns what the change needs (`rerender`, `regroup`,
     `redesign`, `relook`), so the UI does the right next step.
   - `GET /api/fs?path=`: lists folders (and a count of photos in each) for
     the folder browser. Starts at the home directory, follows no symlinks out
     of it, and lists directories only.
   - `GET /api/albums`, `POST /api/albums` (create or open).
   - `PUT /api/keys`: write-only; `GET /api/keys` returns only
     `{provider: "set" | "missing"}`.
   - `GET /api/models?role=`: model suggestions from the chosen endpoint.
   - `POST /api/estimate`: the cost of the next design or re-look, from photo
     count and model prices (`providers.PRICES`).
5. **First run.** `--album` and `--photos` become optional. Started bare, the
   editor opens a short **welcome flow**: *choose your photos → name the album
   → pick a preset → paste a key (or choose Fully local) → Test → Design.*
   That is the whole "install to first album" path, with no terminal after
   the first command.
6. **Effort "high"**: `providers._reasoning` and the three request builders
   gain the level, with the per-provider mapping above and the existing
   "unknown field → retry without it" safety net.

## Security

The editor is a **local** tool today, but it should be possible to run it as a
service later, where each user brings their own key. So:

- **Precedence:** a key from the environment (`ALBUM_API_KEY`,
  `OPENROUTER_API_KEY`, …) always wins over a key entered in the GUI. As a
  service, that means the operator's key is used when set, and a user's own
  key is used only when the operator has not set one.
- **Never logged or shown.** Keys are write-only over the API, stored `0600`
  outside album folders, and never included in exports, error messages,
  model-call logs (`<album>/logs/`) or the costs file. Wherever a key has to
  be identified (Settings, debug output, the startup banner) it is **masked**:
  the first characters of its prefix and the last four, e.g. `sk-or-…a1b2`.
- A single redaction function is applied to every log line and every error
  text that can contain request headers or URLs, and a test asserts that a
  known key never appears in any log, response or file the editor writes.
- Service mode needs per-user storage and sign-in, which this plan does not
  build. Keys are stored per user (`~/.config/...`) so that move stays open.
- The folder browser lists directories only, never file contents, and
  starts under the user's home directory.
- If the server is started with a non-local `--host`, the key form and folder
  browser are disabled unless `--allow-remote-settings` is also passed, and
  the UI says why.

## Delivery, in reviewable steps

Each step is its own PR, usable on its own, with tests (unit tests for
`Config` precedence and validation; e2e through the fake model for every
endpoint; a browser check of the Settings tab).

| step | contents | why first |
|---|---|---|
| **A** | `Config` + `open_album` refactor, `GET/PUT /api/config`, Settings tab with sections 2 (book), 4 (grouping) and 5 (places) | no new security surface; the three biggest levers (length, density, selection) arrive immediately |
| **B** | AI models section: presets, per-role endpoint/model/effort (incl. *High*), keys, Test, cost estimate | the most asked-for, and needs the key-storage design settled |
| **C** | Album section: folder browser, new/open/recent albums, the first-run welcome flow, drag-and-drop photos | depends on A's live re-open |
| **D** | Polish: *Save as models.yaml*, model lists from the endpoints, docs (README, RUNNING, phase2/README), screenshots | |

## Decisions (review of 2026-11-10)

1. **Keys:** a user may enter their own key; an environment key always has
   priority. Keys are never logged or shown, only masked (see Security).
2. **Defaults:** Density *Balanced*, Selection *Best of*, each with an info
   pop-up explaining the difference between the choices.
3. **Effort High:** planner only, to start with. What it means:
   - the model thinks longer before it lays out the book, which helps most on
     large albums with many places and people;
   - the planning call is slower, typically 2–5× (one to three minutes
     instead of 20–40 seconds), and the thinking is billed as output tokens,
     roughly +$0.05–0.30 per design on Sonnet/Opus-class models;
   - it needs a larger output budget, so choosing High raises the planner's
     `max_tokens` to at least 32,000;
   - models without a reasoning setting ignore it.
   The cost estimate next to *Design again…* shows the difference before you
   choose. These figures are estimates; step B measures them.
4. **Drag-and-drop upload:** yes, in step C.
5. **Print-lab picker:** yes, in step A (Saal only for now). Finding labs
   that print and ship such books in Switzerland is a separate research
   story.
6. Nothing else for now.

## Questions for you (answered above)

1. **Keys in the GUI**, or keep them in environment variables only and just
   show *set / missing*? (The plan stores them in `~/.config/...` with `0600`.)
2. **Defaults** for Density and Selection: *Balanced* and *Best of*?
3. **Effort High** costs more and is slower (a planning call can take a
   minute or two). Offer it for the planner only, or for every role?
4. **Drag-and-drop upload** (copies files into the photo folder): wanted, or
   is choosing a folder enough?
5. **Other print labs**: worth a lab picker now (only Saal is defined), or
   later?
6. Anything you change today by editing a file or restarting that is missing
   here?
