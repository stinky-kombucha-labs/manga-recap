# Agent instructions (Codex / other CLI agents)

## Translation review runs

When asked to review/fix manga translations for a batch ("review batch",
"ревю перекладів"), follow EXACTLY the procedure in:

- `.claude/skills/review-batch/SKILL.md`  — the procedure and efficiency rules
- `.claude/skills/review-batch/terminology.md` — canonical names/terms and the
  russism blacklist (keep chapters consistent with it; append new recurring
  terms you standardize)

Non-negotiable rules (duplicated from the skill):

1. Edit ONLY the `translation` field of blocks listed in each chapter's
   `temp/<novel>/chapter-XXXXX/review_todo.json`; match blocks by
   `page_num` + `id` in the sibling `translations.json`.
2. Write files back via `pipeline.jsonfmt.write` (python snippet run with
   `.venv/bin/python`, `sys.path.insert(0, 'scripts')`) — never plain
   `json.dump`.
3. NEVER run `step1_extract.py`, `step2_translate.py` or `step2b_repair.py`
   on reviewed chapters — re-detection drops reviewed translations.
4. After all edits: `.venv/bin/python scripts/step3_render.py`, then
   `.venv/bin/python scripts/step3b_verify.py --fix`; if it exits 3, run
   step3_render.py once more and verify again.
5. Work sequentially in one session; no parallel agents. Batch reads and
   batch writes. If you cannot view a page image and a `verify` block's
   meaning is genuinely unrecoverable from the OCR text, do NOT guess —
   list it in your final report for a human pass.

## Starting a new manga / novel

Follow the checklist in `CLAUDE.md` → "Starting a NEW manga / novel": reset the
per-novel assets (terminology.md, tm.json, translation.glossary, config novel
block), run a 1-2 chapter pilot, fix new watermarks, only then scale.

The rest of the project is documented in `CLAUDE.md` (same file works as a
general project map for any agent).
