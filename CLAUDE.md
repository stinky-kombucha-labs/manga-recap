# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Goal

Translate manga chapters (Tales of Demons and Gods, 959 chapters) from English to Ukrainian, then produce per-chapter 4K portrait YouTube MP4s with Oleksa TTS narration.

**STATUS — user-approved 2026-06-02:** chapter 1 rendered clean (CTD detect → LaMa render + font fallback + Oleksa TTS → 4K MP4). As of 2026-06-03 translation is a dedicated step done locally by **Lapa LLM** (step 2), replacing the earlier by-hand/Claude translation. Standard flow is now three steps (detect → translate → render); apply it to subsequent chapters.

**Confirmed best rendering approach (TOP — use this):** LaMa neural inpainting removes English text, Anime Ace font with white stroke renders Ukrainian directly on the clean background — no white fill patches. LaMa is the chosen background; `blur` exists only as a fallback variant.

**Confirmed text detection:** the manga-image-translator **CTD detector (Comic Text Detector)** gives full bubble coverage. Step1's PaddleOCR alone fragments/misses text (it left English behind and crammed translations into partial boxes). The working end-to-end code path is: **CTD detect → ocr48px → textline merge → merge split regions into one bubble → clean ALL detected text lines → glyph-match render**. Reference implementation: `scripts/demo_detect_translate_render.py` (pages 2–3 proof, LaMa + blur). Clean previews land in `image_output/`.

**Text fit rules (in `pipeline/render.py`):** Ukrainian is sized to the measured English glyph height (`fit_mode: "glyph_match"`, never bigger than the English), fits both width (incl. stroke) and height, lines are **centered** (`justify: false` — word-justify made ugly gaps in narrow bubbles), `line_spacing ≈ 1.35`.

## Three-step workflow

**One-command batch (preferred):** `scripts/run_pipeline.py` chains every step
below for all `run.chapters` — detection → translation → repair → render+TTS+
encode → QA loop (`step3b --fix` → re-render → verify). `--skip-tts` for silent
video. A failed step aborts the batch. It ends with a per-chapter summary
(MP4 size, blocks to review, English leaks). The only optional manual step is
the Claude pass over each `review_todo.json` (see step 2c) — re-run
`step3_render.py` afterwards; caches re-render/re-voice only edited pages.
step2/step2b batch ALL chapters into one worker call (one GGUF load per run,
not per chapter). step3 also skips re-encoding when no page/audio changed.

```
Step 1 — OCR + detect bubbles (once per chapter):
  .venv/bin/python scripts/step1_extract.py

  → creates temp/{novel}/chapter-XXXXX/translations.json
  → "translation" fields are left empty for step 2 to fill
  → existing translations persist across re-runs

Step 2 — Translate EN→UK with local Lapa LLM (re-runnable):
  .venv/bin/python scripts/step2_translate.py
  .venv/bin/python scripts/step2_translate.py --overwrite

  → reads translations.json, fills every empty "translation" via Lapa GGUF
  → runs the model in the Lapa venv as a subprocess (config.json → translation)
  → only fills EMPTY fields by default, so manual edits survive re-runs

Step 2b — Flag + locally repair + emit Claude review list (re-runnable):
  .venv/bin/python scripts/step2b_repair.py
  .venv/bin/python scripts/step2b_repair.py --no-repair

  → flags only suspicious blocks (pipeline/flag.py: latin/explanation/length/empty/mixed)
  → re-translates flagged blocks via Lapa with a CORRECTIVE prompt (local, free)
  → writes review_todo.json — the small list Claude reviews (see REVIEW.md)
  → keeps the Claude pass at ~1-3 blocks/chapter instead of all ~70 (scales to 959)

Step 2c (optional) — Claude reviews ONLY review_todo.json and edits translations.json
  (run Claude per REVIEW.md; skip if blocks_to_review is empty)

Step 3 — Render + encode (re-runnable):
  .venv/bin/python scripts/step3_render.py
  .venv/bin/python scripts/step3_render.py --skip-tts

  → reads translations.json
  → LaMa inpaint + stroke text per page (noise blocks: inpaint only)
  → Oleksa TTS narration per page
  → 4K portrait MP4 (2160×3840, H.264)
  → output: video_output/{novel}/chapter-XXXXX.mp4

Step 3b (optional) — Verify rendered pages (no-eyeball QA):
  .venv/bin/python scripts/step3b_verify.py
  .venv/bin/python scripts/step3b_verify.py --fix

  → PaddleOCRs every rendered page, flags leftover ENGLISH lines
  → writes temp/{novel}/{chapter}/render_qa.json; exit 2 if leaks found
  → blocks intentionally left as art (blanked SFX) are listed separately
  → --fix: junk-looking leaks (URLs/watermarks) are appended to
    translations.json as noise blocks — re-run step3 (only those pages
    re-render thanks to the cache), then verify again. Real-text leaks are
    never auto-erased, only reported.
```

**Translation (step 2) — local LLM (MamayLM).** Translation is done locally by a
Gemma-3-12B Ukrainian GGUF. Current model: **MamayLM-Gemma-3-12B-IT-v2.0-Q8_0**
(replaced `lapa-v0.1.2-instruct` in the sister project on 2026-06-05; same chat
template, same worker). It runs in the **separate Lapa project's venv**
(`/home/user/PycharmProjects/PythonProject_Lapa_LLM/.venv`, which already has
`llama-cpp-python` + the GGUF models) — the main venv never imports `llama_cpp`,
the same isolation pattern as TTS. `pipeline/lapa_worker.py` loads the model once
and translates each block with the **shortest possible prompt**
(`"Переклади українською:\n\n<text>"`). Lapa degrades if you stuff the system
prompt with instructions/glossaries, so keep it minimal. (Earlier chapters were
translated by hand/Claude; this step replaces that with the local model.)

## translations.json format

Located at `temp/{novel}/{chapter}/translations.json`. Each block's `"translation"`
is what gets rendered. Step 2 fills it from the English `"original"`; you can then
hand-edit it. Leave empty `""` to skip rendering text on that block (original text
stays as-is after inpainting).

The file is written by `pipeline/jsonfmt.py` to stay **easy to hand-edit**: each
block lists `id → original → translation` first (the fields you actually read and
edit), with the geometry (`detector`, `bbox`, `line_bboxes`) tucked at the end and
the coordinate arrays collapsed onto one line each. It's still plain JSON.

Blocks with `"noise": true` are scanlation junk (credits / URLs / watermarks,
incl. fuzzy-matched misreads like `wangareadek` ≈ mangareader.net, and page-margin
lines found by the PaddleOCR margin sweep). They are **inpainted clean in step 3
but never translated, rendered, narrated, or flagged** — leave their `translation`
empty.

```json
{
  "novel": "Tales of Demons and Gods",
  "chapter": 1,
  "pages": [
    {
      "page_num": 1,
      "source_file": "00001_original-filename.png",
      "blocks": [
        {
          "id": 0,
          "original": "OCR text (English)",
          "translation": "Ukrainian translation ← step 2 fills / you edit",
          "detector": "ctd",
          "bbox": [x1, y1, x2, y2],
          "line_bboxes": [[x1, y1, x2, y2]]
        }
      ]
    }
  ]
}
```

## Project layout

```
input/
  tales-of-demons-and-gods-manga/   ← 959 chapters, upscaled PNG pages (168 GB)
    chapter-00001/
      00001_....png
      ...
temp/
  tales-of-demons-and-gods-manga/   ← working files per chapter (auto-created)
    chapter-00001/
      pages/          ← copied source pages
      translations.json  ← step 2 fills, you review/edit
      rendered/       ← LaMa + text output
      audio/          ← Oleksa WAV per page
video_output/
  tales-of-demons-and-gods-manga/
    chapter-00001.mp4
```

## config.json settings

| Field | Purpose |
|-------|---------|
| `novel.source_dir` | Relative path from project root: `input/tales-of-demons-and-gods-manga` |
| `novel.total_chapters` | Total chapters (959) |
| `run.chapters` | Chapters to process: `"1"`, `"1-5"`, `"1,3,7-9"`, `"all"` |
| `detection.recover_empty_ocr` | `true` = re-OCR CTD boxes that ocr48px left blank with PaddleOCR (recovers stylized captions). Runs only when blank boxes exist |
| `detection.recover_min_confidence` | PaddleOCR min confidence for recovery (0.5) |
| `detection.margin_sweep` | `true` = PaddleOCR the top/bottom page strips for text CTD misses (bottom URL, top "Novel @ ..." line). Noise → inpainted; real text → translated |
| `detection.margin_ratio` / `margin_min_confidence` | Margin strip height as page fraction (0.05) and Paddle min confidence (0.5) |
| `translation.glossary` | `{english: ukrainian}` fixed translations, fuzzy-matched (≥0.72) against OCR text — for recurring stylized text the OCR garbles (series title on every cover) |
| `translation.lapa_python` | Lapa venv python (has `llama_cpp`): `/home/user/PycharmProjects/PythonProject_Lapa_LLM/.venv/bin/python` |
| `translation.model_path` | Lapa GGUF. `-Q8_0` (≈12 GB, higher quality, **current**) needs `n_ctx ≤ 2048` on a 16 GB card; `-Q4_K_M` (≈6.8 GB) runs fine at 4096 |
| `translation.n_ctx` / `n_gpu_layers` / `flash_attn` | llama.cpp load options. On 16 GB RTX 5080: Q8_0 peaks ~15.5 GB at n_ctx=2048 (fits, thin headroom); Q8_0 at 4096 OOMs (`Failed to create llama_context`). Manga bubbles are short, so 2048 is plenty |
| `translation.temperature/top_p/top_k/repeat_penalty` | Sampler (0.1 / 0.9 / 25 / 1.0 — low temp for faithful translation) |
| `translation.overwrite` | `true` = re-translate even non-empty fields (default false) |
| `tts.tts_python` | `/home/user/PycharmProjects/tts/.venv/bin/python3` |
| `video.page_duration_min` | Minimum seconds per page (default 5) |
| `render.font_max/font_min` | Font size search range (61/21) |
| `render.stroke_width` | White stroke width on text (7) |

## Pipeline modules

- **`pipeline/detect.py`** — CONFIRMED detection: CTD (`Detector.ctd`) + `ocr48px` + textline merge via MIT **dispatch functions directly** (the MangaTranslator wrapper class dropped most regions). Recovers OCR-dropped free-caption lines from the pre-OCR detection boxes. `step1_extract.py` uses this; `pipeline/bubbles.py::merge_into_bubbles` then unions split regions into one bubble. **ocr48px silently drops stylized/textured English captions** (e.g. the yellow "I SHOULD BE SURROUNDED BY THE SAGE EMPEROR…" narration) — the box is detected but its text is empty. `step1_extract.py::_recover_empty_text_with_paddle` re-OCRs only those empty boxes with PaddleOCR (which reads outlined captions) so they carry text into step 2. Without this the caption stays blank and Lapa has nothing to translate (this was the "page 6 not translated" regression after translation became automatic). **It crops each empty box with generous padding and OCRs the crop** (whole-page PaddleOCR mis-localizes these, and CTD boxes are often too tight and clip the text), then offsets the line boxes back and **grows the block bbox to the recovered text** so the inpaint mask covers the full English (this was the "page 2 PEOPLE LIVING… left in English" bug — a wrapping caption split into two tight boxes). Recovery also re-reads **partially OCR'd bubbles** (`ocr_gaps` from `bubbles.py`: some merged lines had text, some empty — ocr48px reading 2 of 6 caption lines produced gutted originals like the ch2 p11 frog narration); the Paddle re-read replaces the text only if it recovered more letters. Limit: heavy graffiti chapter titles ("CHAPTER 1 - REBIRTH") defeat both ocr48px AND PaddleOCR (Paddle reads only "HE"@0.37) — those stay blank and must be hand-filled if you want them translated.
- **`pipeline/ocr.py`** — legacy PaddleOCR path (kept as optional `detection.paddle_fallback`, default off). Fragments/misses bubbles — superseded by `pipeline/detect.py`.
- **`pipeline/translate.py`** — step 2 orchestrator helper. Collects blocks needing translation, calls `pipeline/lapa_worker.py` as a subprocess in the Lapa venv (config `translation.lapa_python`), returns `{key: ukrainian}`. Main venv never imports `llama_cpp`.
- **`pipeline/lapa_worker.py`** — runs INSIDE the Lapa venv. Loads the GGUF once, translates each block with the minimal prompt `"Переклади українською:\n\n<text>"`, strips stray labels, writes a result JSON. Same subprocess-worker pattern as TTS.
- **`pipeline/flag.py`** — deterministic (no-LLM) problem detector. `flag_block` returns reasons a translation is suspicious (`latin`/`explanation`/`length`/`empty`/`mixed`/`translit`/`gloss`). `translit` = single-word SFX transliterated instead of translated ("STAREEE"→"СТАРЕЕЕ"; romanize back and compare); step2b blanks these if the repair pass doesn't fix them. `gloss` = model gloss left in the text ("[СЛОВО]" or Latin in parentheses). Noise blocks are never flagged. Patterns ported from the sister Lapa project. Used by `step2b_repair.py` to keep the Claude review tiny — only flagged blocks are touched, so cost scales to 959 chapters without quality loss. Tunable via config `flag` (defaults in the module).
- **`pipeline/jsonfmt.py`** — readable serializer for translations.json (block order `id→original→translation→detector→bbox→line_bboxes`, coordinate arrays collapsed to one line). Used by step 1 and step 2.
- **`pipeline/render.py`** — background clean (`bg_mode`: `lama` default / `blur` fallback) + Anime Ace stroke text. Glyph-match fit (`english_glyph_height` → font capped to English size), width+height fit incl. stroke, centered lines (`justify` default false), optional `detect_extent` to catch OCR-missed continuation lines. `render_page` plans each block (`_plan_blocks`) then cleans + renders.
- **`scripts/demo_detect_translate_render.py`** — all-in-one reference: CTD detect → OCR → translate → `merge_into_bubbles` → clean all lines → glyph-match render → `image_output/`. This is the confirmed code shape to fold into step1/step2.
- **`scripts/step3b_verify.py`** — post-render QA: PaddleOCRs every rendered page and reports leftover English lines (`render_qa.json`, exit 2 on leaks). Lines inside rendered blocks are ignored (the English OCR model reads our Ukrainian as garbled Latin — only text OUTSIDE story blocks / inside noise boxes can be a real leak). Distinguishes real leaks from blocks intentionally left as art (blanked SFX). `--fix` closes the loop: junk leaks become noise blocks → re-run step3 → verify clean. Replaces eyeballing 959 chapters.
- **`pipeline/tts.py`** — Calls `tts_worker.py` via subprocess with `cwd=/home/user/PycharmProjects/tts/` (model files are relative). Uses Oleksa voice.
- **`pipeline/encode.py`** — `framerate=1` still-image segments + FFmpeg concat → 4K portrait MP4.

**Caches (both in the chapter's `temp/` dir):** `rendered/.render_cache.json` keys each page render by a signature of its blocks+source+render cfg; `audio/.audio_cache.json` keys each page's TTS by a SHA of its narration text. Both mean step 3 only redoes work whose input changed — edit a translation and just that page re-renders AND its audio regenerates. (Before the audio cache, TTS only checked file-exists, so edited/re-translated pages kept stale narration.)

## 4K output format

- **Resolution:** 3840×2160 (16:9 landscape) — manga page scaled to fit height 2160, pillarboxed to 3840 wide with black bars
- **Codec:** H.264 high profile, CRF 18
- **Audio:** 384 kbps AAC (Oleksa TTS)
- **Compatible:** YouTube 4K

## Dependencies

- **Python 3.10**, **torch 2.9.1+cu128** (RTX 5080 sm_120 support)
- **`manga-image-translator/`** — LaMa inpainting (models at `manga-image-translator/models/`)
- **TTS:** `/home/user/PycharmProjects/tts/.venv` — `ukrainian-tts` with Oleksa voice
- **FFmpeg** in PATH

## Known quirks

- PaddleOCR v3+: use `predict()` not `ocr()`, `use_textline_orientation` not `use_angle_cls`
- TTS worker must run with `cwd=/home/user/PycharmProjects/tts/` — model files (config.yaml, etc.) are relative paths
- NVENC fails at 2912×4120 — always use `libx264` + `framerate 1` for still-image manga slides
- LaMa + Dragoman together would exceed VRAM — Dragoman removed from this project; translation is manual
- Detection: prefer MIT **CTD** detector (`Detector.ctd`, detect_size=2048, text_threshold=0.3, box_threshold=0.4). `default`/DBNet drops low-contrast mid-bubble lines; PaddleOCR fragments bubbles. After detect+merge, union split regions into one bubble so one bubble = one translation (avoids duplicate/overlapping text)
- `temp/` was emptied to Trash once by an external process (PyCharm/cleanup), not our scripts — keep a backup of `translations.json` (see `backups/`)
- **Font glyphs:** Anime Ace renders missing glyphs (І Ї Є Ґ, em dash «—», ellipsis «…») as a "№" box. `render.py` auto-detects missing glyphs (bitmap == .notdef) and draws them per-character from NotoSans-Bold; the rest stays Anime Ace. Do NOT transliterate Ukrainian letters to Latin lookalikes (the old Ï→"П" hack was wrong). Text is upper-cased only.
