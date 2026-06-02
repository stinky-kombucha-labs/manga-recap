# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Goal

Translate manga chapters (Tales of Demons and Gods, 959 chapters) from English to Ukrainian, then produce per-chapter 4K portrait YouTube MP4s with Oleksa TTS narration.

**STATUS — user-approved 2026-06-02:** chapter 1 rendered clean via the two-step flow below (CTD detect → fill translations → LaMa render + font fallback + Oleksa TTS → 4K MP4). This is the standard workflow going forward; apply it to subsequent chapters.

**Confirmed best rendering approach (TOP — use this):** LaMa neural inpainting removes English text, Anime Ace font with white stroke renders Ukrainian directly on the clean background — no white fill patches. LaMa is the chosen background; `blur` exists only as a fallback variant.

**Confirmed text detection:** the manga-image-translator **CTD detector (Comic Text Detector)** gives full bubble coverage. Step1's PaddleOCR alone fragments/misses text (it left English behind and crammed translations into partial boxes). The working end-to-end code path is: **CTD detect → ocr48px → textline merge → merge split regions into one bubble → clean ALL detected text lines → glyph-match render**. Reference implementation: `scripts/demo_detect_translate_render.py` (pages 2–3 proof, LaMa + blur). Clean previews land in `image_output/`.

**Text fit rules (in `pipeline/render.py`):** Ukrainian is sized to the measured English glyph height (`fit_mode: "glyph_match"`, never bigger than the English), fits both width (incl. stroke) and height, lines are **centered** (`justify: false` — word-justify made ugly gaps in narrow bubbles), `line_spacing ≈ 1.35`.

## Two-step workflow

```
Step 1 — OCR + fill translations (once per chapter):
  .venv/bin/python scripts/step1_extract.py

  → creates temp/{novel}/chapter-XXXXX/translations.json
  → edit "translation" fields manually or provide to Claude for translation
  → translations persist across re-runs (manual edits are preserved)

Step 2 — Render + encode (re-runnable):
  .venv/bin/python scripts/step2_render.py
  .venv/bin/python scripts/step2_render.py --skip-tts

  → reads translations.json
  → LaMa inpaint + stroke text per page
  → Oleksa TTS narration per page
  → 4K portrait MP4 (2160×3840, H.264)
  → output: video_output/{novel}/chapter-XXXXX.mp4
```

## translations.json format

Located at `temp/{novel}/{chapter}/translations.json`. Edit the `"translation"` field for each block. Leave empty `""` to skip rendering text on that block (original text stays as-is after inpainting).

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
          "bbox": [x1, y1, x2, y2],
          "line_bboxes": [[x1,y1,x2,y2], ...],
          "original": "OCR text (English)",
          "translation": "Ukrainian translation ← edit this"
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
      translations.json  ← edit this
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
| `tts.tts_python` | `/home/user/PycharmProjects/tts/.venv/bin/python3` |
| `video.page_duration_min` | Minimum seconds per page (default 5) |
| `render.font_max/font_min` | Font size search range (61/21) |
| `render.stroke_width` | White stroke width on text (7) |

## Pipeline modules

- **`pipeline/detect.py`** — CONFIRMED detection: CTD (`Detector.ctd`) + `ocr48px` + textline merge via MIT **dispatch functions directly** (the MangaTranslator wrapper class dropped most regions). Recovers OCR-dropped free-caption lines from the pre-OCR detection boxes. `step1_extract.py` uses this; `pipeline/bubbles.py::merge_into_bubbles` then unions split regions into one bubble.
- **`pipeline/ocr.py`** — legacy PaddleOCR path (kept as optional `detection.paddle_fallback`, default off). Fragments/misses bubbles — superseded by `pipeline/detect.py`.
- **`pipeline/render.py`** — background clean (`bg_mode`: `lama` default / `blur` fallback) + Anime Ace stroke text. Glyph-match fit (`english_glyph_height` → font capped to English size), width+height fit incl. stroke, centered lines (`justify` default false), optional `detect_extent` to catch OCR-missed continuation lines. `render_page` plans each block (`_plan_blocks`) then cleans + renders.
- **`scripts/demo_detect_translate_render.py`** — all-in-one reference: CTD detect → OCR → translate → `merge_into_bubbles` → clean all lines → glyph-match render → `image_output/`. This is the confirmed code shape to fold into step1/step2.
- **`pipeline/tts.py`** — Calls `tts_worker.py` via subprocess with `cwd=/home/user/PycharmProjects/tts/` (model files are relative). Uses Oleksa voice.
- **`pipeline/encode.py`** — `framerate=1` still-image segments + FFmpeg concat → 4K portrait MP4.

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
