# Instructions for Claude — translation review (between step 2b and step 3)

You fix the few translations that the automatic flagger + Lapa-repair pass
(`step2b_repair.py`) could not resolve on its own. This runs **after**
`step2_translate.py` + `step2b_repair.py` and **before** `step3_render.py`. Work
carefully — your output goes straight into the video.

## Only review the flagged blocks (do NOT read the whole chapter)

`step2b_repair.py` already checked every block and locally repaired what it could.
The blocks that still need a human-grade eye are listed in:

`temp/<novel>/<chapter>/review_todo.json` (currently
`temp/tales-of-demons-and-gods-manga/chapter-00001/review_todo.json`)

Each entry has `page_num`, `id`, `reasons` (why it was flagged), `original`,
`translation`, and `page_image`. **Read only this list.** For each entry, apply the
fix to the matching block (same `page_num` + `id`) in the sibling
`translations.json`. Do not scan the rest of the chapter — it's already fine.

If `review_todo.json` has an empty `blocks_to_review`, there is nothing to do — go
straight to `step3_render.py`.

`reasons` legend: `empty` (no translation), `latin` (leftover English),
`explanation` (Lapa explained instead of translating), `mixed` (mixed-script word),
`length` (translation too long/short vs original), `verify` (caption recovered by a
fallback OCR — the source text itself may be misread, so **open the page image** and
confirm the meaning; this is how OCR errors like `WORD`→`WORLD` ("Слово" vs "Світ") get
caught — a text-only check cannot see them).

## translations.json block shape
```json
{
  "id": 0,
  "original": "English OCR text",          // DO NOT touch
  "translation": "Ukrainian ← edit only this",
  "detector": "ctd",                        // DO NOT touch
  "bbox": [...], "line_bboxes": [...]       // DO NOT touch
}
```

## What to do

For each entry in `review_todo.json`, edit **only the `translation` field** of the
matching block in `translations.json`. Use `reasons` as the hint for what's wrong:

1. **OCR errors — translate by meaning.** `original` often has recognition defects
   (`WORD`→`WORLD`, `MOUNT AIN`→`MOUNTAIN`, glued/split words). Translate by **sense**,
   not literally from the broken text.
   - example: `original: "THE WORD OUTSIDE OF ... MOUNTAINS"` → correctly "The **world**
     beyond the mountains…" (Ukr. «Світ…»), not «Слово» ("word").
2. **"Explanations" instead of a translation.** On short/meaningless blocks (`WA`, `AH`,
   `ATP`, random letters) Lapa sometimes writes a long explanation ("WA is an
   abbreviation for Washington", "ATP — the tennis association…"). Make such a block
   **empty** (`"translation": ""`) — it's ordinary SFX/noise and is not narrated.
3. **Scanlation credits / links — never translate.** Lines like
   `Translator: ...`, `Cleaner & Redrawer: ...`, `Typesetter: ...`, `Proofreader: ...`,
   `Edited by ...`, or any URL (`http...`, `www.`, `*.com/.net/.org`, reader/webtoon
   site names) are not story text. step 1 already drops most of them, but if one slips
   through, set its `"translation": ""` (it must not be rendered or narrated).
4. **Leftover Latin / untranslated** — translate it or remove it.
5. **Naturalness.** Make the line read like living Ukrainian (manga dialogue), no
   calques. Use Ukrainian dialogue quotes «…». Keep `?!` and «…».
6. **Length sanity.** If a translation is much longer/shorter than the original, Lapa
   probably invented or dropped something — compare and fix.
7. **Empty `original`.** This is a caption the OCR could not read. If `translation` is
   also empty, **open the page** (`temp/<novel>/<chapter>/pages/<NNNN>.png`, number =
   `page_num` zero-padded to 4 digits), read the text by eye, and fill the translation.
   If it isn't text (art/sound), leave it empty.

## What NOT to do

- Don't touch `id`, `original`, `detector`, `bbox`, `line_bboxes`.
- Don't add or remove blocks or pages.
- Don't change the file structure — the output must be valid JSON of the same shape.

## Hint for ambiguous cases

You can open the page itself to understand context:
`temp/<novel>/<chapter>/pages/<page_num zero-padded to 4 digits>.png`
(e.g. page 6 → `0006.png`).

## When done

Save the file and give a short report: how many blocks you fixed and what (OCR errors,
removed "explanations", filled-in empties). Then I run `step3_render.py`.
