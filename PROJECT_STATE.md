# Manhwa Recap Project State

> **OUTDATED (kept for history).** This file describes the original
> video-clip-based experiment (extracting frames from a recap video). The
> project has since pivoted to translating full manga chapters from
> `input/<novel>/chapter-XXXXX/` page images — see **CLAUDE.md** for the
> current architecture and workflow (`scripts/run_pipeline.py` runs it end to
> end). Nothing below reflects the current pipeline.

## Goal

Create a manga recap style video from the source clip by replacing existing
English manga text with Ukrainian text inside the original panels/bubbles.

The desired output is not subtitles and not large white sticker cards. The
target look is:

1. detect or reuse the original text area,
2. erase/paint over the English text,
3. render Ukrainian text back into the same area with a manga-like bold font,
4. rebuild the video with the original audio.

## Current Input

- Source video: `video/2025-12-21 19-20-06.mp4`
- Main baseline output: `video_output/translated_with_audio.mp4`
- Extracted full frames: `temp/frames/`
- Deduplicated unique frames: `temp/frames_unique/`
- Unique-frame mapping: `temp/frames_unique/frame_mapping.json`
- OCR text blocks: `temp/extracted_text.json`

The current test clip has 7 unique frames:

- `000000.png` to `000004.png`: first snow panel with two text blocks.
- `000005.png`: no detected text.
- `000006.png`: city panel with two speech bubbles.

## Scripts

- `scripts/01_extract_frames.py`: extract video frames.
- `scripts/02_deduplicate_frames.py`: deduplicate near-identical frames.
- `scripts/03_translate_frames.py`: original image translation attempt.
- `scripts/03a_extract_text.py`: OCR extraction into `temp/extracted_text.json`.
- `scripts/03b_translate_text.py`: text translation stage.
- `scripts/04_rebuild_video.py`: rebuild translated frames into a video.
- `scripts/05_render_text_variants.py`: earlier manual render variants.
- `scripts/06_generate_mit_variants.py`: manga-image-translator based variants.
- `scripts/07_generate_ocr_replace_variants.py`: current preferred variant generator.

Run scripts with the project venv:

```bash
.venv/bin/python scripts/<script>.py
```

## What Was Tested

### Original pipeline output

Output:

- `video_output/translated_with_audio.mp4`

Issue:

- Text replacement quality is bad compared with the earlier desired behavior.
- Ukrainian text appears poorly placed and does not reliably erase the English.

### Manual render variants

Script:

- `scripts/05_render_text_variants.py`

Outputs:

- `video_output/render_variants/v1_white_bubble_plain.mp4`
- `video_output/render_variants/v2_white_patch_readable.mp4`
- `video_output/render_variants/v3_subtitle_overlay.mp4`
- `video_output/render_variants/v4_condensed_upper_clean.mp4`
- `video_output/render_variants/preview_000000.jpg`
- `video_output/render_variants/preview_000006.jpg`

Result:

- Text is readable and old text is mostly covered.
- But padding was too large (`padding=90`), so the result looks like big white
  sticker blocks instead of natural manga text replacement.
- Subtitle overlay is not the desired style.

### Manga-image-translator variants

Script:

- `scripts/06_generate_mit_variants.py`

Outputs:

- `video_output/mit_variants/mit_manga2eng_noto_condensed.mp4`
- `video_output/mit_variants/mit_manga2eng_anime_ace.mp4`
- `video_output/mit_variants/mit_default_noto_clean.mp4`
- `video_output/mit_variants/preview_000000.jpg`
- `video_output/mit_variants/preview_000006.jpg`

Result:

- The library stack is useful, but its automatic region detection is unreliable
  on this clip.
- It misses or mis-sizes text regions, leaving English text visible underneath.
- It also placed some Ukrainian text in wrong/too small areas.

Conclusion:

- Do not use automatic MIT region geometry as the source of truth for this
  current clip.

## Current Direction

The most reliable current approach is OCR-box based replacement:

1. Use stable text boxes from `temp/extracted_text.json`.
2. Clear old text inside those boxes.
3. Render fixed Ukrainian translations into the same OCR boxes.
4. Reconstruct full frame sequence from `frame_mapping.json`.
5. Build MP4 with original audio.

Current generator:

- `scripts/07_generate_ocr_replace_variants.py`

It creates several visual variants:

- soft horizontal paint strips + bold condensed uppercase,
- soft horizontal paint strips + bold condensed italic uppercase,
- soft horizontal paint strips + sentence-case bold text,
- soft horizontal paint strips + Anime Ace visual test,
- mask-fill fallback for comparison.

Current outputs:

- `video_output/ocr_replace_variants/v1_softstrip_noto_condensed_upper.mp4`
- `video_output/ocr_replace_variants/v2_softstrip_noto_italic_upper.mp4`
- `video_output/ocr_replace_variants/v3_softstrip_noto_sentence.mp4`
- `video_output/ocr_replace_variants/v4_softstrip_anime_ace_upper.mp4`
- `video_output/ocr_replace_variants/v5_maskfill_noto_condensed_fallback.mp4`
- `video_output/ocr_replace_variants/preview_000000.jpg`
- `video_output/ocr_replace_variants/preview_000006.jpg`

## 2026-06-02 Current Feedback

Best-looking font/style so far:

- `video_output/ocr_replace_variants/v4_softstrip_anime_ace_upper.mp4`

Problem:

- The white cleanup area behind the replacement text takes too much space.
- On the first panel it visually covers too much of the original frame.
- The next test should keep the Anime Ace style but avoid full white backing.

Next direction:

1. Build blur/smudge variants that affect only the original English text.
2. Avoid large white background patches.
3. Keep the text style close to `v4_softstrip_anime_ace_upper`.
4. Compare several cleanup modes: masked blur, masked inpaint, masked lighten,
   and narrow-row blur.

Implementation started:

- Added `scripts/08_generate_blur_replace_variants.py`.
- New outputs will be written to `video_output/blur_replace_variants/`.
- This generator keeps the Anime Ace visual direction and tests cleanup that is
  limited to the old English text/rows instead of painting a large white area.

Progress update:

- First blur pass was generated, but preview inspection showed that weak blur
  leaves gray English text ghosts under the Ukrainian text.
- The next pass now tightens manual cleanup geometry to the real text rows and
  compares stronger no-big-patch modes:
  - row inpaint,
  - strong letter-mask inpaint,
  - strong letter bleach,
  - brighter row blur,
  - smaller-font row inpaint.
- The important constraint remains: do not create the large white background
  from `v4_softstrip_anime_ace_upper`; only remove or soften the original
  English text area.

Workflow update:

- User asked to stop spending time on internal visual checking and instead
  generate multiple different variants for manual selection.
- Current direction is to output several distinct cleanup/render modes quickly,
  then let the user choose the closest visual match.

Generated selection batch:

- `video_output/blur_replace_variants/v1_anime_strip_fill_clean.mp4`
- `video_output/blur_replace_variants/v2_anime_letter_wipe_max.mp4`
- `video_output/blur_replace_variants/v3_anime_strip_fill_texture.mp4`
- `video_output/blur_replace_variants/v4_anime_strip_fill_bright.mp4`
- `video_output/blur_replace_variants/v5_anime_strip_fill_small.mp4`
- `video_output/blur_replace_variants/preview_000000.jpg`
- `video_output/blur_replace_variants/preview_000006.jpg`

Next step:

- User reviews the generated batch and picks the closest variant.
- After selection, tune only that selected direction instead of comparing every
  mode again.

## 2026-06-02 MIT Replacement Research

The blur/strip approach is not the right core direction. `manga-image-translator`
does replacement as a pipeline:

1. detect text and create a raw text mask,
2. refine/expand that mask around the source text,
3. run an inpainter,
4. render translated text into the inpainted region.

Useful MIT knobs from the project README and source:

- `renderer=manga2eng` can fit text closer to a detected bubble/text area.
- `font_path=fonts/anime_ace_3.ttf` is a supported manga-like font option.
- `mask_dilation_offset=10..30` is recommended to wrap source text better.
- `kernel_size` can be increased when source text still leaks through.
- `inpainting_size` should be increased for high-resolution images, otherwise
  masks may not be fully covered.
- `inpainter=lama_large` is the default/recommended inpaint direction in the
  current local setup; the model file exists at
  `manga-image-translator/models/inpainting/lama_large_512px.ckpt`.

New implementation:

- Added `scripts/09_generate_mit_manual_mask_variants.py`.
- It keeps manual/stable text geometry for this clip, then uses MIT-style
  stages: raw text-pixel mask -> MIT mask refinement -> LaMa inpainting -> MIT
  rendering.
- It avoids relying on automatic MIT detection for this clip because earlier
  tests showed unstable/mis-sized regions, but it still uses MIT's replacement
  approach instead of rectangular blur.

Expected outputs when run:

- `video_output/mit_manual_mask_variants/v1_mit_anime_manga2eng_mask20_k3.mp4`
- `video_output/mit_manual_mask_variants/v2_mit_anime_manga2eng_mask30_k5.mp4`
- `video_output/mit_manual_mask_variants/v3_mit_anime_manga2eng_mask45_k7.mp4`
- `video_output/mit_manual_mask_variants/v4_mit_noto_manga2eng_mask30_k5.mp4`
- `video_output/mit_manual_mask_variants/v5_mit_anime_default_mask30_k5.mp4`
- `video_output/mit_manual_mask_variants/preview_000000.jpg`
- `video_output/mit_manual_mask_variants/preview_000006.jpg`

PyCharm run target:

- Script path: `scripts/09_generate_mit_manual_mask_variants.py`
- Working directory: project root, `/home/user/PycharmProjects/Manhwa_Recap`
- Python interpreter: project `.venv`
- Optional parameter to render one variant:
  `--only v2_mit_anime_manga2eng_mask30_k5`

## Notes On Fonts

- `NotoSans-CondensedBlack.ttf` supports Ukrainian and is the safest manga-like
  option currently installed.
- `NotoSans-CondensedBlackItalic.ttf` supports Ukrainian and looks closer to the
  slanted manga recap style.
- `anime_ace_3.ttf` is manga-like but lacks Ukrainian-specific letters
  `ґ/є/і/ї`; the current variant maps those to close fallback glyphs, so it is
  only a visual experiment, not the safest final font.

## 2026-06-02 Clean Pipeline — Two-Step Config-Driven Flow

### Selected approach
`v1_stroke_only_lama` confirmed as best by user review. All other variants deleted.
Core technique: LaMa neural inpaint removes English text → Anime Ace + white stroke renders Ukrainian directly on clean background. No white fill patches.

### New project structure
Old one-off scripts (01–10) replaced with a reusable pipeline:

```
scripts/
  pipeline/
    chapters.py     config.json run.chapters parser
    ocr.py          PaddleOCR → grouped text blocks (bbox + line_bboxes)
    render.py       LaMa + Word-justify stroke text (generalised v1)
    tts.py          subprocess → tts project venv → Oleksa WAV
    tts_worker.py   worker script called by tts.py
    encode.py       FFmpeg: page segments → chapter MP4
    translate.py    subprocess → Lapa LLM venv → fills translations.json
    lapa_worker.py  worker script (runs in Lapa venv) called by translate.py
    jsonfmt.py      readable translations.json serializer
  step1_extract.py    OCR → translations.json
  step2_translate.py  Lapa LLM EN→UK → fills translations.json
  step3_render.py     LaMa + text + TTS + encode

config.json         novel + run + render + tts + translation settings
```

### GPU environment
Upgraded venv to torch 2.9.1+cu128 — required for RTX 5080 (sm_120).
TTS runs in `/home/user/PycharmProjects/tts/.venv` (also torch 2.9.1+cu128).

### Novel in progress
- **Tales of Demons and Gods**, 959 chapters
- Source: `input/tales-of-demons-and-gods-manga/` (copied from manga_upscaler output, 168 GB)
- Original location: `/home/user/PycharmProjects/manga_upscaler/output/tales-of-demons-and-gods-manga`
- Currently running: chapter 1 (14 pages)
- Output: `video_output/tales-of-demons-and-gods-manga/chapter-00001.mp4`

### Run
```bash
# Edit config.json -> run.chapters first.

.venv/bin/python scripts/step1_extract.py
.venv/bin/python scripts/step2_render.py
```

### Dragoman removed — 2026-06-02

Dragoman removed from this project (VRAM conflicts with LaMa, complex deps).
Translation is now manual: step1 extracts OCR text → translations.json → user/Claude fills in `"translation"` fields.

New three-step pipeline:
- `scripts/step1_extract.py` — OCR → `temp/{novel}/{chapter}/translations.json`
- `scripts/step2_translate.py` — local Lapa LLM EN→UK → fills translations.json
- `scripts/step3_render.py` — reads translations.json → LaMa + TTS → 4K MP4

### Chapter 1 result — 2026-06-02 (v2, 4K)
- Output: `video_output/tales-of-demons-and-gods-manga/chapter-00001.mp4`
- Resolution: **2160×3840** (portrait 4K, 9:16)
- Duration: 1 min 51 s | Size: 22 MB | Bitrate: 1.6 Mbps
- 14 pages, translations by Claude, Oleksa TTS narration
- Pipeline fixes applied:
  - PaddleOCR v3+: `predict()`, `use_textline_orientation`
  - TTS worker: `cwd=/home/user/PycharmProjects/tts/`
  - NVENC removed — `libx264` + `framerate=1` for still-image slides

## Recovery Notes

The root project docs such as `README.md`, `PROGRESS.md`, or older research
notes are currently missing.

Checked recovery paths:

- Root `.git` exists as an empty directory, but it is not a usable git repo.
- Workspace search found only `manga-image-translator` library docs.
- PyCharm Local History exists, but direct search did not find this project's
  missing md files.
- `.claude/file-history` contains other project snapshots, but no matching
  `Manhwa_Recap` documentation was found in the quick search.

Practical recovery direction:

- Use this file as the new source of truth.
- Continue from the current scripts and generated previews.

## 2026-06-02 Pro Variants — Script 10

Added `scripts/10_generate_pro_variants.py` with six approaches based on
professional scanlation techniques. All six use Anime Ace font with white
stroke (standard scanlation readability technique — black text + white
outline makes text readable on any background without a white fill).

### Six clearing strategies

| Variant | Clearing | Notes |
|---------|----------|-------|
| `v1_stroke_only_lama` | LaMa neural inpaint | Cleanest removal, no fill artefacts |
| `v2_bubble_flood_stroke` | Flood-fill bubble boundary | Fills only the speech-bubble shape |
| `v3_letteronly_stroke` | TELEA inpaint dark pixels only | Minimum footprint on original art |
| `v4_rowfill_stroke` | Per-row left↔right gradient | Good for gradient/snow backgrounds |
| `v5_lama_bubble_stroke` | LaMa + flood-fill clean | Best quality for speech bubbles |
| `v6_median_stroke` | Median of surrounding bright pixels | Simple, blends into background |

### Text rendering fix — Word-style justify + line-height (2026-06-02)

**Problem 1 — line overlap:** `line_spacing=0.94` was applied as a multiplier
of `font_size`, but the actual rendered pixel height of Cyrillic glyphs can
exceed `font_size`, causing lines to overlap.

Fix: `_measured_line_h(font, sw)` uses `textbbox("Агя|")` to measure the real
pixel height including stroke. `lh = actual_height × line_spacing` where
`line_spacing=1.18` guarantees a clear gap regardless of font metrics.

**Problem 2 — text narrower than original:** translated lines were centred in
the block, leaving unused horizontal space and making the block taller than
the original English text occupied.

Fix: Word-style full justification (`_draw_word_justify`) distributes
word-spacing evenly so every non-last line fills the full block width —
identical to how Word's "Justify" alignment works. Last line stays centred.

**Block model:** each entry in `FRAME_BLOCKS` / `FRAME_LINE_LAYOUTS` is one
logical block (contiguous text area separated from the next by whitespace in
the original panel). The entire block is cleared as one unit and the
translation is fit into that same bbox — font size reduced until it fits
vertically while always filling the full horizontal width.

### Font size −20 % (2026-06-02)

Translated text was visually exceeding the original block boundaries.
Reduced default font range by 20 %: `max_font` 76 → 61, `min_font` 26 → 21.
`fit_text` already lowers the size automatically when text overflows the bbox
height, so this just shifts the starting point down to match the original
text volume more closely.

Outputs: `video_output/pro_variants/`

Run:
```bash
.venv/bin/python scripts/10_generate_pro_variants.py
.venv/bin/python scripts/10_generate_pro_variants.py --only v1_stroke_only_lama
```

## 2026-06-02 CONFIRMED approach — CTD detect + glyph-match + LaMa

**Decision (user-confirmed): LaMa is the top background and we use exactly this
code shape.** `blur` stays only as a fallback variant.

### Root cause that was fixed
Leftover English and "crooked"/cramped Ukrainian were NOT a render bug — step1's
PaddleOCR **fragments and misses text** (ch1 p3 detected 1 of ~4 bubbles; p2 lost
the caption tail and mid-bubble lines). The renderer placed text correctly into
the boxes it was given; the boxes were incomplete.

### The working pipeline (one run does everything)
`scripts/demo_detect_translate_render.py` — self-contained:
1. **Detect** with MIT **CTD** (`Detector.ctd`, detect_size=2048,
   text_threshold=0.3, box_threshold=0.4) → full bubble coverage.
2. **OCR** (`ocr48px`) each line.
3. **Textline merge** → regions, then `merge_into_bubbles()` unions split regions
   so **one bubble = one translation** (kills duplicate/overlapping text).
4. **Clean ALL detected lines** (LaMa, `mask_dilation=48`) → no English remains.
5. **Render** via `pipeline/render.py` glyph-match: Ukrainian sized to measured
   English glyph height (never bigger), centered lines (`justify=false`),
   `line_spacing=1.35`, white stroke.

Translations for p2/p3 are hard-coded in that script (preview only).
Clean results: `image_output/page02_lama.png`, `page02_blur.png`,
`page03_lama.png`, `page03_blur.png`.

### `pipeline/render.py` upgrades
- `bg_mode`: `lama` (default) / `blur` (fallback) / `blur_box`.
- `fit_mode: glyph_match` + `english_glyph_height()` — cap font to English size.
- width+height fit incl. stroke; `_font_for_glyph_height` via cap-ratio calibration.
- `justify` (default false → centered); `line_spacing`; `stroke_ratio`.
- `detect_extent` — optional letter-detection to catch OCR-missed continuation lines.

### Open items / NEXT
- Fold CTD detect + `merge_into_bubbles` into `step1_extract.py` so
  `translations.json` carries full bubbles for manual editing (keep the two-step
  manual-translation flow). Then step2 renders the whole chapter this way.
- Optional: translucent underlay under captions that sit on artwork (no white
  bubble), e.g. p3 "Holy Orchid Institute" caption over the rooftop.
- Data-loss note: `temp/` + `video_output/` were moved to Trash at 21:55 by an
  external process (not our scripts); recovered from Trash. Backup kept at
  `backups/translations_chapter-00001.json`.

## 2026-06-02 APPROVED — standard workflow

User reviewed the chapter-1 result and approved it ("looks great"). The CTD +
glyph-match + LaMa + font-fallback two-step flow below is the standard approach
for all remaining chapters. Details follow.

## 2026-06-02 Production two-step run wired up (CTD + font fallback)

The demo approach is now in the real two-step pipeline; a clean from-scratch run
of chapter 1 succeeded (step1 → fill translations → step2 → 4K MP4 with TTS).

- **step1_extract.py**: detection switched to `pipeline/detect.py` (CTD via direct
  dispatch) + `pipeline/bubbles.merge_into_bubbles`. Free-caption lines that OCR
  drops are recovered from pre-OCR detection boxes (kept even with empty text so
  they get cleaned + can be translated by eye). `detection.paddle_fallback=false`.
- **config.json render**: `bg_mode=lama`, `fit_mode=glyph_match`,
  `glyph_match_ratio=1.15`, `detect_extent=false`, `justify=false`,
  `line_spacing=1.35`, `stroke_ratio=0.10`, `mask_dilation=48`, `kernel_size=7`,
  `expand_render_bbox=false`, `font_min=12`.
- **Font fix (important):** Anime Ace renders І Ї Є Ґ, em dash, ellipsis as a "№"
  box. `render.py` now auto-detects missing glyphs (bitmap equals the .notdef
  bitmap) and draws them per-character from NotoSans-Bold; everything else stays
  Anime Ace. The old Latin transliteration (Ï→"П") was removed.
- Workflow confirmed: `step1_extract.py` → `step2_translate.py` (Lapa LLM) →
  review/edit `translation` fields in `translations.json` → `step3_render.py`.
  All run from config `run.chapters`.
