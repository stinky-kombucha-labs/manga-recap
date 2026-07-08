---
name: review-batch
description: Review flagged manga translations for all chapters in config run.chapters (step 2c at scale) — fix OCR-damaged and unnatural Ukrainian, purge russisms, apply edits to translations.json, re-render, verify. Use after scripts/run_pipeline.py finishes a batch.
---

# Batch translation review (step 2c at scale)

You are finishing a batch produced by `scripts/run_pipeline.py`. Everything is
already rendered and QA-verified for leftover English; your job is ONLY the
translation-quality pass over the small flagged lists, then a re-render.

## Efficiency rules — read FIRST (violating these burns the user's usage limit)

- **Do the whole review in THIS conversation. Never spawn agents, subagents,
  parallel tasks or workflows.** All review lists of a 50-chapter batch
  together are ~100 KB of text — they fit in one context. Fan-out multiplies
  cost ~20× (every agent re-loads its own context) and splits the terminology
  consistency you are here to enforce.
- **Batch reads**: dump ALL review_todo.json files with ONE command
  (e.g. a python loop printing chapter/page/id/reasons/original/translation).
  Do not open files one by one.
- **Batch writes**: apply fixes for MANY chapters with ONE python script call
  (dict of edits → pipeline.jsonfmt.write per file). Never one tool call per
  block.
- **Images are the expensive part.** Open a page image ONLY when a `verify`
  block is long story text whose meaning you genuinely cannot reconstruct
  from the OCR. Short garble ("d31S", "5TANDS", corner logos) → blank or
  noise WITHOUT looking. When you do look, crop to the block bbox (+margin)
  and downscale to ~800 px before viewing — never view full 4K pages.
- Don't re-run step1/step2/step2b, don't re-read what is already in context.

## Procedure

1. Read `config.json` → `run.chapters` and expand the chapter list.
2. For each chapter `temp/<novel.folder>/chapter-{N:05d}/`:
   - Read `review_todo.json`. If `blocks_to_review` is empty — skip the chapter.
   - Follow the rules in `REVIEW.md`. Edit ONLY the `translation` field of the
     matching block (`page_num` + `id`) in the sibling `translations.json`;
     write the file back with `pipeline.jsonfmt.write` (python snippet), never
     with plain `json.dump`. Never touch `original`/geometry; never add
     translations to `"noise": true` blocks.
   - `verify` blocks: the SOURCE text came from fallback OCR and may be
     misread (`WORD`↔`WORLD`). If the meaning is doubtful, open the page image
     (`pages/{page_num:04d}.png`) and read it by eye before translating.
   - Garbled short blocks in page corners (misread site logos: "АГРОГОМ",
     "AC.OO.COM" variants, "mangareader" garbles) → set `"translation": ""`
     and `"noise": true` (they get inpainted away).
   - Untranslatable single-word SFX → `"translation": ""` plus
     `"keep_empty": true` (art stays).
3. While reviewing, ALSO scan the flagged blocks for **russisms** — the local
   model produces them regularly. See `terminology.md` (next to this file) for
   the russism blacklist and the canonical glossary of names/terms; keep every
   chapter consistent with it. If you meet a NEW recurring term or name, add
   it to `terminology.md` so future batches stay consistent.
4. After ALL chapters are reviewed, run (each may take minutes — use
   background execution and wait for completion):
   - `.venv/bin/python scripts/step3_render.py` — re-renders + re-voices only
     edited pages, re-encodes only changed chapters;
   - `.venv/bin/python scripts/step3b_verify.py --fix` — exit 3 → run
     `step3_render.py` once more and verify again (junk leaks got queued for
     inpainting); exit 2 → real English leaks: text CTD missed entirely. For a
     missed SENTENCE, add a block to that page in translations.json (bbox from
     render_qa.json leak, translation by meaning) and re-run step3; short
     SFX-as-art entries are fine to leave.
5. Report per chapter: blocks fixed / blanked / marked noise, russisms
   corrected, and the final verify status.

## Resuming a partial review

An earlier interrupted run may have already fixed some blocks. If a flagged
block's current translation already reads as natural Ukrainian and matches
`terminology.md`, accept it silently and move on — do not re-translate.

## Hard rules

- Do NOT re-run `step1_extract.py` / `step2_translate.py` / `step2b_repair.py`
  for reviewed chapters — re-detection can drop reviewed translations.
- Ukrainian dialogue style: natural spoken language, «…» quotes, keep `?!…`
  (TTS reads them), sentence case is fine (render upper-cases itself).
- A translation must never be longer than the bubble can visually take —
  prefer terse spoken phrasing over literal completeness.
