# Manga Recap — English → Ukrainian manga translation pipeline

Translate manga pages from English to another language and render per-chapter
4K portrait videos with TTS narration. Detects speech bubbles, erases the
original text with neural inpainting (LaMa), and types the translation back into
each bubble with auto-fit, manga-style stroke text.

> You bring your own manga pages and translations. This repo ships **only the
> code** — no manga images or translated text (those are copyrighted).

## How it works

```
Step 1 — detect text + extract (once per chapter):
    .venv/bin/python scripts/step1_extract.py
    → CTD detector + 48px OCR + bubble merge
    → writes temp/<novel>/<chapter>/translations.json

Edit translations.json — fill the "translation" field of each block.

Step 2 — render + encode (re-runnable):
    .venv/bin/python scripts/step2_render.py
    .venv/bin/python scripts/step2_render.py --skip-tts
    → LaMa inpaint + stroke text per page
    → TTS narration per page (optional)
    → 4K portrait MP4 in video_output/
```

## Key features

- **Full bubble coverage** via the CTD (Comic Text Detector) model, then
  regions are unioned into bubbles so one bubble = one translation.
- **Glyph-match fit** — translated text is sized to the original lettering,
  fits both width and height (stroke included), never overflows.
- **Font fallback** — missing glyphs in the display font (e.g. Ukrainian
  І Ї Є Ґ, em dash, ellipsis) are auto-detected and drawn from a complete
  fallback font, so text is always correct.
- **Backgrounds** — LaMa neural inpaint (default) or gaussian blur fallback.

## Requirements

- Python 3.10, CUDA GPU recommended (tested on RTX 5080, torch 2.9.1+cu128).
- [`manga-image-translator`](https://github.com/zyddnys/manga-image-translator)
  cloned into `./manga-image-translator/` with its detection/OCR/inpainting
  models downloaded into `manga-image-translator/models/`.
- FFmpeg in `PATH`.
- A TTS engine for narration (this project used a local `ukrainian-tts` venv;
  optional — use `--skip-tts` to skip).

## Setup

```bash
python3.10 -m venv .venv
.venv/bin/pip install -r requirements.txt

# vendored library (not included here)
git clone https://github.com/zyddnys/manga-image-translator
# then download its models per its README

# put your manga pages here:
#   input/<novel-folder>/chapter-00001/*.png
```

Edit `config.json` (`novel.source_dir`, `run.chapters`, render/tts options),
then run step 1 and step 2.

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
