# Phase 1 — core loop, CLI only

`photos/ + captions.yaml` → one planning call → layout JSON → proof PDF, with a
`revise` command for the comment loop and append-only version history. No web
frontend, no database. The template library and the prompt are the two things
worth iterating on here, and both are much faster to change from a CLI.

## What the model is asked to do

| Call | Sees images? | Decides |
|---|---|---|
| `annotate` | yes, once, cached | subject, people count and who, scene, focal point, strength 1–5 |
| `plan` | no | sequence, sections, pacing, template per spread, omissions, focal points, captions, section text |
| `revise` | no | complete revised layout from a plain-language comment |

Grouping is computed before any of it: `cluster.py` turns timestamps and GPS into
moment/place groups. The planner is told a spread draws from one group, groups
stay in one run, and section breaks fall between groups. `validate.py` warns when
a spread mixes them.

## Run it

Run these **from inside `phase1/`**. `--photos` is your own folder;
`./demo_photos` is the synthetic set shipped with the repository.

```bash
cd phase1
export ANTHROPIC_API_KEY=...        # or see "Choosing the model" in RUNNING.md

python album.py annotate --album ../albums/sardinien --photos ./demo_photos

python album.py plan  --album ../albums/sardinien --photos ./demo_photos \
                      --title "Sardinien 2026" --subtitle "Zehn Tage am Meer"

python album.py proof --album ../albums/sardinien --photos ./demo_photos

python album.py revise --album ../albums/sardinien --photos ./demo_photos \
                       --comment "Seite 4 ist überladen, und die Kirchenbilder früher"

python album.py history  --album ../albums/sardinien
python album.py rollback --album ../albums/sardinien --to 2
```

Captions can come from a file with `--captions my-captions.yaml`
(`photo_id: caption`, see `examples/demo_captions.yaml`). A description set in
the editor wins over one in that file. The editor and the CLI share every file
in the album directory, so you can move between them freely.

`annotate` is the only call that pays for vision, and it pays once. Skip it and
the planner still works, but it is grouping on timestamps, GPS and your captions
alone — it cannot know who is in the frame.

`--dry-run` on `plan` writes the exact prompt to `prompt-plan.txt` and stops.
Use it to iterate on wording for free.

Run the offline test after any change to the prompt, schema or templates:

```bash
python tests/test_loop.py     # no API key, no network
python tests/test_e2e.py      # the CLI and the editor against a fake model endpoint
```

It scripts a fake model whose first answer breaks the slot-count contract, and
asserts the retry loop recovers, the versions chain correctly, and the resulting
layout survives the real validator and renderer.

## What is enforced where

| Rule | Where |
|---|---|
| One call for the whole album | `planner.plan` — no chunking, by construction |
| Schema failure → reject and retry the whole call | `planner._run`, 3 attempts, error fed back |
| Never hand-repair model JSON | there is no repair code to reach for |
| Vision once, then never again | `annotate` caches to `annotations.json`; `plan` and `revise` are text-only |
| No photograph across the fold | fold-spanning templates withheld from the catalogue; `fold-crossing` is a validator error |
| Complete JSON on revise, not a patch | `prompt.revise_user_content` |
| Print constraints | `phase0/validate.py`, run after every call |

## Cost

Every call prints a cost line — attempts, images, tokens, dollars. It exists to
keep the vision-on-revision trap visible: thumbnails roughly triple input cost,
and a revision does not need them, because the model already made the visual
decisions in the plan call and now has the layout JSON in front of it.

Rough shape at 80 photos: planning with vision is the expensive call, revisions
are cheap. If revisions stop feeling cheap, something has turned vision back on.

## Findings so far

- **The planner needs the lab's page minimum, not just the increment.** The
  first test run produced a well-formed 16-page book that no lab will print.
  The validator caught it; the prompt now states the range and tells the model
  to pad rather than come in short. The general lesson: every constraint the
  validator can express should also appear in the prompt, or every album pays
  for it with a retry.
- **Per-photo resolution ceilings belong in the prompt.** Each photo line carries
  the templates it is too small for, and a photo too small for everything is
  marked "TOO SMALL TO PRINT — leave it out". Cheaper than discovering it after
  a render.
- **The model must be told it may leave photographs out.** Without that, it
  places every frame and the book sags.
- **Grouping had to be computed, not inferred.** Asking the model to keep the
  same people and place together only works if it can tell which photographs
  those are. Captions are a weak signal — people write "Salz auf der Haut", not
  "beach, day 3, the kids". Time gaps and GPS give the skeleton for free; the
  annotate pass supplies who is in the frame.
- **Clutter is specified as counts, not adjectives.** "Do not clutter" means
  nothing to a model. "Most spreads hold one or two photographs; four is rare and
  only for minor detail shots; never two four-slot spreads within three spreads
  of each other" is checkable.
- **`two-up-equal` slots are close to square** (183 × 163 mm at 21×21), so a 4:5
  portrait loses a lot of frame to the cover-crop. First item on the
  template-library list once the printed test book arrives — resist fixing it
  from screen impressions alone.

## Not done yet

- `who` in the annotations is a per-batch description ("two children"), not
  identity recognition, so it will not reliably link one person across an album
  shot over ten days. Face embeddings computed locally, or letting the user tag
  people in the editor, are the honest options.
