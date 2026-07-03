"""
OCR module — extracts text blocks from a manga page image.

Each block represents one logical text area (speech bubble or caption).
Returns line-level bboxes (for LaMa mask) and a single merged text string.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

_PADDLE_OCR = None


def _bbox_from_quad(quad: list) -> list[int]:
    """Convert PaddleOCR quad [[x,y]×4] to [x1,y1,x2,y2]."""
    xs = [p[0] for p in quad]
    ys = [p[1] for p in quad]
    return [int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))]


def _overlaps_or_close(a: list[int], b: list[int], gap: int = 60) -> bool:
    """True if two bboxes overlap or are within `gap` pixels vertically."""
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    x_overlap = ax1 < bx2 and bx1 < ax2
    y_close = ay1 < by2 + gap and by1 < ay2 + gap
    return x_overlap and y_close


def _group_lines(lines: list[dict], gap: int = 60) -> list[list[dict]]:
    """Merge OCR lines that are close vertically into blocks."""
    if not lines:
        return []
    groups: list[list[dict]] = [[lines[0]]]
    for line in lines[1:]:
        merged = False
        for group in groups:
            # Check against last line in group (lines are y-sorted)
            if _overlaps_or_close(group[-1]["bbox"], line["bbox"], gap):
                group.append(line)
                merged = True
                break
        if not merged:
            groups.append([line])
    return groups


def _group_bbox(lines: list[dict]) -> list[int]:
    """Bounding box that covers all lines in a group."""
    x1 = min(l["bbox"][0] for l in lines)
    y1 = min(l["bbox"][1] for l in lines)
    x2 = max(l["bbox"][2] for l in lines)
    y2 = max(l["bbox"][3] for l in lines)
    return [x1, y1, x2, y2]


def _get_paddle_ocr():
    """Lazy singleton: PaddleOCR model startup is expensive."""
    global _PADDLE_OCR
    if _PADDLE_OCR is None:
        import logging, os
        logging.disable(logging.CRITICAL)
        os.environ.setdefault("DISABLE_MODEL_SOURCE_CHECK", "True")
        from paddleocr import PaddleOCR

        # Manga pages are always upright: the doc-orientation classifier must be
        # OFF — on wide, thin crops (the margin-sweep strips) it "helpfully"
        # rotates the image 90° and returns coordinates in the rotated space,
        # which scattered phantom vertical boxes down the page edge.
        _PADDLE_OCR = PaddleOCR(
            use_textline_orientation=True,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            lang="en",
        )
    return _PADDLE_OCR


def ocr_page(image_path: Path, min_confidence: float = 0.6) -> list[dict[str, Any]]:
    """
    Run PaddleOCR on one manga page and return grouped text blocks.

    Returns list of:
        {
          "bbox":       [x1, y1, x2, y2],   # block bounding box
          "text":       "original text",      # joined text of all lines
          "confidence": float,
          "line_bboxes": [[x1,y1,x2,y2], ...] # per-line bboxes for LaMa mask
        }
    """
    ocr = _get_paddle_ocr()
    img = np.array(Image.open(image_path).convert("RGB"))
    result = ocr.predict(img)

    if not result:
        return []

    # New PaddleOCR API: result[0] is an OCRResult object (dict-like)
    ocr_res = result[0]
    texts = list(ocr_res.get("rec_texts") or [])
    scores = list(ocr_res.get("rec_scores") or [])
    polys = list(ocr_res.get("rec_polys") or ocr_res.get("rec_boxes") or [])

    if not texts:
        return []

    raw_lines = []
    for text, conf, poly in zip(texts, scores, polys):
        if float(conf) < min_confidence or not str(text).strip():
            continue
        poly_arr = np.array(poly)
        if poly_arr.ndim == 2:
            bbox = _bbox_from_quad(poly_arr.tolist())
        else:
            x1, y1, x2, y2 = [int(v) for v in poly_arr.flatten()[:4]]
            bbox = [min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)]
        raw_lines.append({
            "bbox": bbox,
            "text": str(text).strip(),
            "confidence": float(conf),
        })

    # Sort by y then x
    raw_lines.sort(key=lambda l: (l["bbox"][1], l["bbox"][0]))

    # Determine adaptive gap from median line height
    if raw_lines:
        heights = [l["bbox"][3] - l["bbox"][1] for l in raw_lines]
        median_h = float(np.median(heights))
        gap = max(30, int(median_h * 1.5))
    else:
        gap = 60

    groups = _group_lines(raw_lines, gap=gap)

    blocks = []
    for group in groups:
        text = " ".join(l["text"] for l in group)
        conf = float(np.mean([l["confidence"] for l in group]))
        blocks.append({
            "bbox": _group_bbox(group),
            "text": text,
            "confidence": conf,
            "line_bboxes": [l["bbox"] for l in group],
        })

    return blocks
