#!/usr/bin/env python3
"""
Page-2-only experiment: keep Ukrainian text INSIDE the original English footprint.

The complaint: the overlay is bigger/wider than the English it replaces. Cause is
the expanded render bbox + a font_max that lets glyphs grow taller than the English
lettering. Here we try several strategies that constrain the overlay to the original
OCR bbox (the English footprint) and optionally cap the font to the English line height.

    .venv/bin/python scripts/fit_experiments_page2.py

Writes temp/<novel>/chapter-00001/rendered_p2_<strategy>/0002.png and a COMPARE collage.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).parent))
from pipeline.render import _clean_background, render_text_on_image, ANIME_ACE, _PROBE


# --- Measure the actual English glyph height (caps) in a block ---------------
def english_glyph_height(image_rgb: np.ndarray, block: dict) -> float:
    """Median height of letter-sized connected components inside the block's
    OCR line boxes = the real pixel height of the English lettering."""
    gray = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2GRAY)
    H, W = gray.shape[:2]
    heights = []
    for lb in (block.get("line_bboxes") or [block["bbox"]]):
        x1, y1, x2, y2 = [int(v) for v in lb]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(W, x2), min(H, y2)
        if x2 <= x1 or y2 <= y1:
            continue
        patch = gray[y1:y2, x1:x2]
        ph = patch.shape[0]
        for inv in (cv2.THRESH_BINARY_INV, cv2.THRESH_BINARY):
            _, th = cv2.threshold(patch, 0, 255, inv + cv2.THRESH_OTSU)
            n, _, stats, _ = cv2.connectedComponentsWithStats(th, connectivity=8)
            for i in range(1, n):
                h = int(stats[i, cv2.CC_STAT_HEIGHT])
                w = int(stats[i, cv2.CC_STAT_WIDTH])
                area = int(stats[i, cv2.CC_STAT_AREA])
                # plausible letter: not noise, not the whole bubble
                if h < ph * 0.25 or h > ph * 0.95:
                    continue
                if w > (x2 - x1) * 0.6 or area < 6:
                    continue
                heights.append(h)
    return float(np.median(heights)) if heights else float(block["bbox"][3] - block["bbox"][1]) * 0.4


# Calibrate Anime Ace: rendered cap height as a fraction of the nominal font size.
def _cap_ratio() -> float:
    f = ImageFont.truetype(str(ANIME_ACE), 100)
    bb = _PROBE.textbbox((0, 0), "ХІМШ", font=f)
    return (bb[3] - bb[1]) / 100.0


CAP_RATIO = _cap_ratio()


def font_for_glyph_height(px: float) -> int:
    """Font size whose rendered caps are ~px tall."""
    return max(8, int(round(px / max(0.1, CAP_RATIO))))

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CH = PROJECT_ROOT / "temp" / "tales-of-demons-and-gods-manga" / "chapter-00001"
PAGE = 2


def _median_line_h(block: dict) -> float:
    hs = [(lb[3] - lb[1]) for lb in (block.get("line_bboxes") or [block["bbox"]])
          if (lb[3] - lb[1]) > 0]
    return float(np.median(hs)) if hs else float(block["bbox"][3] - block["bbox"][1])


def _inset(bbox, frac):
    x1, y1, x2, y2 = bbox
    dx = int((x2 - x1) * frac)
    dy = int((y2 - y1) * frac)
    return [x1 + dx, y1 + dy, x2 - dx, y2 - dy]


# Strategies as (cap_multiplier, bbox_inset, allow_grow_min_factor).
# cap_multiplier: Ukrainian caps = mult × measured English caps (≤1 => not bigger).
# bbox_inset: shrink the box inward (fraction) so text sits strictly inside.
# The font auto-fit still shrinks BELOW the cap if the box is too small — it never
# grows above the cap, so the overlay is never larger than the English.
STRATEGIES = {
    # match English glyph height exactly, original tight bbox
    "M1_match": dict(mult=1.0, inset=0.0),
    # 10% smaller than English — guarantees it reads as "not bigger"
    "M2_90pct": dict(mult=0.90, inset=0.0),
    # match English + 6% inset so it stays clear of the bubble edge
    "M3_inset": dict(mult=1.0, inset=0.06),
    # English size with a little headroom (105%) for sparse blocks
    "M4_105pct": dict(mult=1.05, inset=0.0),
}


def main():
    data = json.loads((CH / "translations.json").read_text())
    page = next(p for p in data["pages"] if p["page_num"] == PAGE)
    blocks = [b for b in page["blocks"] if b.get("translation", "").strip()]
    src = CH / "pages" / f"{PAGE:04d}.png"
    src_rgb = np.array(Image.open(src).convert("RGB"))

    cfg = json.loads((PROJECT_ROOT / "config.json").read_text())["render"]
    base = {**cfg, "bg_mode": "blur", "blur_strength": 0.6,
            "expand_render_bbox": False, "stroke_ratio": 0.10,
            "font_min": 10, "font_max": 61}

    # Measure English glyph height per block once.
    eng_h = {block["id"]: english_glyph_height(src_rgb, block) for block in blocks}
    print("English glyph heights:", {k: round(v) for k, v in eng_h.items()},
          " cap_ratio=", round(CAP_RATIO, 3))

    produced = []
    for name, st in STRATEGIES.items():
        image = _clean_background(Image.open(src).convert("RGB"), blocks, base)
        for block in blocks:
            cap_px = eng_h[block["id"]] * st["mult"]
            fs_cap = font_for_glyph_height(cap_px)
            bbox = _inset(block["bbox"], st["inset"]) if st["inset"] else list(block["bbox"])
            cfg_b = {**base, "font_max": fs_cap, "font_min": min(base["font_min"], fs_cap)}
            render_text_on_image(image, block["translation"], bbox, cfg_b)
        out = CH / f"rendered_p2_{name}"
        out.mkdir(exist_ok=True)
        p = out / f"{PAGE:04d}.png"
        image.save(p)
        produced.append((name, p))
        print("saved", p)

    # Collage
    try:
        font = ImageFont.truetype("/usr/share/fonts/google-noto/NotoSans-Bold.ttf", 26)
    except Exception:
        font = ImageFont.load_default()
    W = 560
    cols = []
    for name, p in produced:
        im = Image.open(p).convert("RGB")
        h = int(im.height * W / im.width)
        im = im.resize((W, h))
        canvas = Image.new("RGB", (W, h + 46), (20, 20, 20))
        canvas.paste(im, (0, 46))
        ImageDraw.Draw(canvas).text((8, 10), name, fill=(255, 255, 255), font=font)
        cols.append(canvas)
    H = max(c.height for c in cols)
    out = Image.new("RGB", (W * len(cols) + 5 * (len(cols) - 1), H), (20, 20, 20))
    x = 0
    for c in cols:
        out.paste(c, (x, 0))
        x += W + 5
    cp = CH / "COMPARE_page02_fit.png"
    out.save(cp)
    print("collage", cp)


if __name__ == "__main__":
    main()
