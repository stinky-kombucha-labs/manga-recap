# Manga Recap — English → Ukrainian manga translation pipeline

Translate manga pages from English to another language and render per-chapter
4K portrait videos with TTS narration. Detects speech bubbles, erases the
original text with neural inpainting (LaMa), and types the translation back into
each bubble with auto-fit, manga-style stroke text.

> You bring your own manga pages and translations. This repo ships **only the
> code** — no manga images or translated text (those are copyrighted).

## How it works

One command runs everything for the chapters in `config.json -> run.chapters`:

```
.venv/bin/python scripts/run_pipeline.py            # full batch, TTS included
.venv/bin/python scripts/run_pipeline.py --skip-tts # silent videos
```

Under the hood it chains the individual (re-runnable) steps:

```
Step 1 — detect text + extract:
    scripts/step1_extract.py
    → CTD detector + 48px OCR + bubble merge
    → PaddleOCR recovery of stylized/partially-read captions
    → margin sweep for page-edge URLs; scanlation junk (credits/URLs/
      watermarks) is kept as "noise" blocks to be erased, never translated
    → writes temp/<novel>/<chapter>/translations.json

Step 2 — translate EN→UK with a local LLM:
    scripts/step2_translate.py
    → fills empty "translation" fields via a local GGUF model (one model
      load for the whole batch); manual edits survive re-runs
    → translation.glossary pins recurring stylized text (series title)

Step 2b — flag + auto-repair suspicious translations:
    scripts/step2b_repair.py
    → deterministic checks (leftover Latin, hallucinated explanations,
      length outliers, transliterated SFX, model glosses)
    → re-translates flagged blocks locally with a corrective prompt,
      blanks untranslatable SFX, writes review_todo.json (the short list
      worth a human/Claude look — see REVIEW.md; optional)

Step 3 — render + encode:
    scripts/step3_render.py [--skip-tts]
    → LaMa inpaint (noise blocks erased, bubbles cleaned) + stroke text
    → TTS narration per page (optional) → 4K MP4 in video_output/
    → per-page render/audio caches: edits re-render only what changed

Step 3b — verify (no-eyeball QA):
    scripts/step3b_verify.py [--fix]
    → OCRs every rendered page, reports leftover English
    → --fix queues junk leaks as noise blocks; the orchestrator then
      re-renders just those pages and verifies again
```

Translation runs a local Ukrainian-tuned Gemma-3 12B GGUF (currently
MamayLM-Gemma-3-12B-IT-v2.0) via `llama-cpp-python` in its own venv — the main
venv never loads it, the same isolation as TTS. Point `translation.lapa_python`
and `translation.model_path` in `config.json` at that venv and a `.gguf` file.
The prompt is deliberately minimal (`"Переклади українською: …"`) — small local
models degrade with long instruction-heavy system prompts.

## Key features

- **Full bubble coverage** via the CTD (Comic Text Detector) model, then
  regions are unioned into bubbles so one bubble = one translation.
- **Stylized-caption recovery** — captions the main OCR reads only partially
  (or not at all) are re-read with PaddleOCR so nothing is lost in translation.
- **Scanlation junk removal** — credits, URLs and site watermarks (fuzzy-matched
  even when OCR mangles them) are erased from the page, not translated.
- **Glyph-match fit** — translated text is sized to the original lettering,
  fits both width and height (stroke included), never overflows.
- **Font fallback** — missing glyphs in the display font (e.g. Ukrainian
  І Ї Є Ґ, em dash, ellipsis) are auto-detected and drawn from a complete
  fallback font, so text is always correct.
- **Backgrounds** — LaMa neural inpaint (default) or gaussian blur fallback.
- **Self-verifying** — a post-render OCR pass reports any leftover source-language
  text and auto-queues junk leftovers for inpainting, so batches don't need
  page-by-page eyeballing.

## Requirements

- **Python 3.10** — required by the TTS engine
  ([`ukrainian-tts`](https://github.com/robinhad/ukrainian-tts) does not support
  newer Python versions), so the whole project pins 3.10.
- CUDA GPU recommended (tested on RTX 5080).
- [`manga-image-translator`](https://github.com/zyddnys/manga-image-translator)
  cloned into `./manga-image-translator/` with its detection/OCR/inpainting
  models downloaded into `manga-image-translator/models/`.
- FFmpeg in `PATH`.
- TTS narration via [`ukrainian-tts`](https://github.com/robinhad/ukrainian-tts)
  (Oleksa voice). Run it in its **own** venv and point `tts.tts_python` in
  `config.json` at that interpreter. Optional — use `--skip-tts` to render
  silent video.

## Troubleshooting

- **`CUDA error: no kernel image is available` / GPU not used.** Newer GPUs need
  a matching CUDA build of PyTorch. On an RTX 5080 (compute capability sm_120)
  the stock torch wheels fail; install **torch 2.9.1+cu128** (or newer) in both
  the main venv and the TTS venv. Older torch builds simply don't include the
  kernels for recent cards.
- **NVENC fails at large still-image resolutions** — the encoder uses
  `libx264` + `framerate=1` for the page slides instead.
- **PaddleOCR (legacy path) v3+** — use `predict()` not `ocr()`.

## Setup

```bash
python3.10 -m venv .venv
.venv/bin/pip install -r requirements.txt

# vendored library (not included here)
git clone https://github.com/zyddnys/manga-image-translator
# then download its models per its README

# TTS in its own Python 3.10 venv (optional, for narration):
#   https://github.com/robinhad/ukrainian-tts
# then set tts.tts_python in config.json to that venv's python

# put your manga pages here:
#   input/<novel-folder>/chapter-00001/*.png
```

Edit `config.json` (`novel.source_dir`, `run.chapters`, render/tts options),
then run `scripts/run_pipeline.py`.

## config.json (render block)

| Field | Purpose |
|-------|---------|
| `bg_mode` | `lama` (inpaint) or `blur` |
| `fit_mode` | `glyph_match` (size text to original) |
| `glyph_match_ratio` | 1.0 = match original size, 0.9 = smaller |
| `justify` | `false` = centered lines (recommended) |
| `line_spacing` | line height multiplier (~1.35) |
| `stroke_ratio` | white stroke width as fraction of font size |
| `mask_dilation` / `kernel_size` | LaMa mask growth around text |

## License / content

Code only. The pipeline does not include or distribute any manga images or
translated text. Use it with content you have the right to translate.
