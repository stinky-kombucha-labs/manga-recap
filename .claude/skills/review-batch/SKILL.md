---
name: review-batch
description: Review flagged manga translations for all chapters in config run.chapters (step 2c at scale) — fix OCR-damaged and unnatural Ukrainian, purge russisms, apply edits to translations.json, re-render, verify. Use after scripts/run_pipeline.py finishes a batch.
---

# Batch translation review (step 2c at scale)

You are finishing a batch produced by `scripts/run_pipeline.py`. Everything is
already rendered and QA-verified for leftover English; your job is ONLY the
translation-quality pass over the small flagged lists, then a re-render.

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
   - `.venv/bin/python scripts/step3b_verify.py` — exit 3 → run
     `step3_render.py` once more and verify again; exit 2 → real English
     leaks, investigate those pages.
5. Report per chapter: blocks fixed / blanked / marked noise, russisms
   corrected, and the final verify status.

## Hard rules

- Do NOT re-run `step1_extract.py` / `step2_translate.py` / `step2b_repair.py`
  for reviewed chapters — re-detection can drop reviewed translations.
- Ukrainian dialogue style: natural spoken language, «…» quotes, keep `?!…`
  (TTS reads them), sentence case is fine (render upper-cases itself).
- A translation must never be longer than the bubble can visually take —
  prefer terse spoken phrasing over literal completeness.
