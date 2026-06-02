#!/usr/bin/env python3
"""
Page-2 experiment: COMPLETE English replacement.

Problem: OCR recorded only part of a caption (e.g. block 0 = top 2 lines) but the
English actually spans more lines. The Ukrainian is painted only over the recorded
bbox, leaving uncovered English below. Here we DETECT the full English text extent
around each block (letter-sized connected components, so big artwork is ignored),
clean that whole extent, and render the Ukrainian across it.

    .venv/bin/python scripts/fit_replace_page2.py

Writes temp/<novel>/chapter-00001/rendered_p2_<strategy>/0002.png + COMPARE collages.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).parent))
from pipeline.render import (_clean_background, render_text_on_image, ANIME_ACE,
                             _PROBE, _letter_mask_from_patch)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CH = PROJECT_ROOT / "temp" / "tales-of-demons-and-gods-manga" / "chapter-00001"
PAGE = 2


# --- English glyph height + font calibration --------------------------------
def english_glyph_height(gray: np.ndarray, block: dict) -> float:
    H, W = gray.shape[:2]
    heights = []
    for lb in (block.get("line_bboxes") or [block["bbox"]]):
        x1, y1, x2, y2 = [int(v) for v in lb]
        x1, y1, x2, y2 = max(0, x1), max(0, y1), min(W, x2), min(H, y2)
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
                if h < ph * 0.25 or h > ph * 0.95 or w > (x2 - x1) * 0.6:
                    continue
                heights.append(h)
    return float(np.median(heights)) if heights else (block["bbox"][3] - block["bbox"][1]) * 0.4


def _cap_ratio() -> float:
    f = ImageFont.truetype(str(ANIME_ACE), 100)
    bb = _PROBE.textbbox((0, 0), "ХІМШ", font=f)
    return (bb[3] - bb[1]) / 100.0


CAP_RATIO = _cap_ratio()


def font_for_glyph_height(px: float) -> int:
    return max(8, int(round(px / max(0.1, CAP_RATIO))))


# --- Detect full English text extent around a block -------------------------
def detect_text_lines(gray: np.ndarray, block: dict, eng_h: float) -> list[list[int]]:
    """Return letter-sized component boxes near the block (page coords).

    A search window is opened around the block; only components whose height is
    close to the block's English glyph height are kept, so big artwork (the
    creature, rocks) is rejected while continuation text lines are caught.
    """
    H, W = gray.shape[:2]
    bx1, by1, bx2, by2 = [int(v) for v in block["bbox"]]
    bw, bh = bx2 - bx1, by2 - by1
    # generous window: full caption tends to sit just below/around the bbox
    wx1 = max(0, bx1 - int(bw * 0.10))
    wx2 = min(W, bx2 + int(bw * 0.10))
    wy1 = max(0, by1 - int(bh * 0.30))
    wy2 = min(H, by2 + int(bh * 1.20))
    patch = gray[wy1:wy2, wx1:wx2]
    if patch.size == 0:
        return []
    mask = _letter_mask_from_patch(patch)
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    boxes = []
    for i in range(1, n):
        x = int(stats[i, cv2.CC_STAT_LEFT]); y = int(stats[i, cv2.CC_STAT_TOP])
        w = int(stats[i, cv2.CC_STAT_WIDTH]); h = int(stats[i, cv2.CC_STAT_HEIGHT])
        if h < eng_h * 0.45 or h > eng_h * 1.9:      # similar-size text only
            continue
        if w > eng_h * 6:                            # not a long bar / art edge
            continue
        boxes.append([wx1 + x, wy1 + y, wx1 + x + w, wy1 + y + h])
    return boxes


def cluster_rows(boxes: list[list[int]], eng_h: float) -> list[list[int]]:
    """Group letter boxes into text-line boxes by vertical proximity, then keep
    only rows that look like REAL text: several horizontally-aligned components
    spanning a meaningful width. Random artwork texture does not line up into
    wide multi-letter rows, so this rejects false positives on busy panels."""
    if not boxes:
        return []
    boxes = sorted(boxes, key=lambda b: (b[1] + b[3]) / 2)
    rows, cur = [], [boxes[0]]
    cy = (boxes[0][1] + boxes[0][3]) / 2
    for b in boxes[1:]:
        c = (b[1] + b[3]) / 2
        if abs(c - cy) <= eng_h * 0.7:
            cur.append(b)
        else:
            rows.append(cur); cur = [b]
        cy = c
    rows.append(cur)

    line_boxes = []
    for r in rows:
        x1 = min(b[0] for b in r); y1 = min(b[1] for b in r)
        x2 = max(b[2] for b in r); y2 = max(b[3] for b in r)
        # A genuine text line: >=4 letters and >=4 glyph-heights wide.
        if len(r) >= 4 and (x2 - x1) >= eng_h * 4:
            line_boxes.append([x1, y1, x2, y2])
    return line_boxes


def union(boxes: list[list[int]]) -> list[int]:
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def build_collage(items, name, crop=None):
    try:
        font = ImageFont.truetype("/usr/share/fonts/google-noto/NotoSans-Bold.ttf", 30)
    except Exception:
        font = ImageFont.load_default()
    W = 1620 if crop else 560
    rows = []
    for lab, path in items:
        im = Image.open(path).convert("RGB")
        if crop:
            im = im.crop(crop)
        h = int(im.height * W / im.width)
        im = im.resize((W, h))
        canvas = Image.new("RGB", (W, h + 44), (20, 20, 20))
        canvas.paste(im, (0, 44))
        ImageDraw.Draw(canvas).text((8, 8), lab, fill=(255, 255, 255), font=font)
        rows.append(canvas)
    if crop:  # stack vertically
        H = sum(r.height for r in rows) + 5 * (len(rows) - 1)
        out = Image.new("RGB", (W, H), (20, 20, 20)); y = 0
        for r in rows:
            out.paste(r, (0, y)); y += r.height + 5
    else:     # side by side
        H = max(r.height for r in rows)
        out = Image.new("RGB", (W * len(rows) + 5 * (len(rows) - 1), H), (20, 20, 20)); x = 0
        for r in rows:
            out.paste(r, (x, 0)); x += W + 5
    p = CH / name
    out.save(p)
    print("collage", p)


def main():
    data = json.loads((CH / "translations.json").read_text())
    page = next(p for p in data["pages"] if p["page_num"] == PAGE)
    blocks = [b for b in page["blocks"] if b.get("translation", "").strip()]
    src = CH / "pages" / f"{PAGE:04d}.png"
    src_rgb = np.array(Image.open(src).convert("RGB"))
    gray = cv2.cvtColor(src_rgb, cv2.COLOR_RGB2GRAY)

    cfg = json.loads((PROJECT_ROOT / "config.json").read_text())["render"]
    base = {**cfg, "bg_mode": "blur", "blur_strength": 0.6,
            "expand_render_bbox": False, "stroke_ratio": 0.10,
            "font_min": 10, "font_max": 61}

    # Per block: measure English height, detect full text extent.
    eng_h, detected, extent = {}, {}, {}
    for b in blocks:
        h = english_glyph_height(gray, b)
        eng_h[b["id"]] = h
        det = cluster_rows(detect_text_lines(gray, b, h), h)
        detected[b["id"]] = det
        all_boxes = (b.get("line_bboxes") or [b["bbox"]]) + det
        extent[b["id"]] = union(all_boxes)
        print(f"block {b['id']}: eng_h={round(h)} orig_bbox={b['bbox']} "
              f"detected_lines={len(det)} extent={extent[b['id']]}")

    def augmented_blocks():
        out = []
        for b in blocks:
            nb = copy.deepcopy(b)
            nb["line_bboxes"] = (b.get("line_bboxes") or [b["bbox"]]) + detected[b["id"]]
            out.append(nb)
        return out

    # Strategy R1: clean the FULL detected extent, render Ukrainian across it.
    img = _clean_background(Image.open(src).convert("RGB"), augmented_blocks(), base)
    for b in blocks:
        fs = font_for_glyph_height(eng_h[b["id"]])
        cfg_b = {**base, "font_max": fs, "font_min": min(base["font_min"], fs)}
        render_text_on_image(img, b["translation"], extent[b["id"]], cfg_b)
    (CH / "rendered_p2_R1_replace").mkdir(exist_ok=True)
    p1 = CH / "rendered_p2_R1_replace" / f"{PAGE:04d}.png"; img.save(p1); print("saved", p1)

    # Strategy R2: clean the FULL extent but render only in the ORIGINAL bbox
    # (leftover English just disappears into the background, art stays clean).
    img2 = _clean_background(Image.open(src).convert("RGB"), augmented_blocks(), base)
    for b in blocks:
        fs = font_for_glyph_height(eng_h[b["id"]])
        cfg_b = {**base, "font_max": fs, "font_min": min(base["font_min"], fs)}
        render_text_on_image(img2, b["translation"], list(b["bbox"]), cfg_b)
    (CH / "rendered_p2_R2_cleanonly").mkdir(exist_ok=True)
    p2 = CH / "rendered_p2_R2_cleanonly" / f"{PAGE:04d}.png"; img2.save(p2); print("saved", p2)

    crop = (0, 0, 1620, 540)
    build_collage([("ENG original", src),
                   ("R1: clean+fill extent", p1),
                   ("R2: clean+orig bbox", p2)],
                  "COMPARE_replace_top.png", crop=crop)


if __name__ == "__main__":
    main()
