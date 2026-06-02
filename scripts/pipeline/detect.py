"""
Detection — CTD (Comic Text Detector) + 48px OCR + textline merge, via the
manga-image-translator dispatch functions directly.

Using the dispatch functions (not the MangaTranslator wrapper class) is what gives
full bubble coverage; the wrapper's config plumbing dropped most regions. Returns
raw per-region blocks {bbox, line_bboxes, text}; caller unions them into bubbles.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import torch

_MIT_ROOT = Path(__file__).resolve().parents[2] / "manga-image-translator"
if str(_MIT_ROOT) not in sys.path:
    sys.path.insert(0, str(_MIT_ROOT))

from manga_translator.detection import dispatch as _detect_dispatch
from manga_translator.ocr import dispatch as _ocr_dispatch
from manga_translator.textline_merge import dispatch as _merge_dispatch
from manga_translator.config import Detector, Ocr, OcrConfig

_DEVICE = "cuda" if torch.cuda.is_available() else "cpu"


async def detect_page_blocks(image_rgb: np.ndarray, cfg: dict | None = None) -> list[dict]:
    """Detect + OCR + merge one page. Returns [{bbox, line_bboxes, text}]."""
    cfg = cfg or {}
    h, w = image_rgb.shape[:2]

    textlines, _mask, *_ = await _detect_dispatch(
        Detector.ctd, image_rgb,
        detect_size=int(cfg.get("detection_size", 2048)),
        text_threshold=float(cfg.get("text_threshold", 0.3)),
        box_threshold=float(cfg.get("box_threshold", 0.4)),
        unclip_ratio=float(cfg.get("unclip_ratio", 2.3)),
        invert=False, gamma_correct=False, rotate=False,
        device=_DEVICE, verbose=False)

    if not textlines:
        return []

    def _quad_box(pts):
        qx = [p[0] for p in pts]; qy = [p[1] for p in pts]
        return [int(min(qx)), int(min(qy)), int(max(qx)), int(max(qy))]

    # Detection boxes BEFORE OCR — OCR drops low-contrast lines (white-on-sky
    # captions), but we still want their boxes to clean + a block to translate.
    det_boxes = [_quad_box(q.pts) for q in textlines]

    ocr_lines = await _ocr_dispatch(Ocr.ocr48px, image_rgb, textlines,
                                    OcrConfig(), device=_DEVICE, verbose=False)
    ocr_text = {}
    for q in ocr_lines:
        ocr_text[tuple(_quad_box(q.pts))] = (getattr(q, "text", "") or "").strip()

    regions = await _merge_dispatch(ocr_lines, w, h, verbose=False)

    blocks = []
    region_boxes = []
    for r in regions:
        xs = [p[0] for q in r.lines for p in q]
        ys = [p[1] for q in r.lines for p in q]
        bbox = [int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))]
        region_boxes.append(bbox)
        line_bboxes = [_quad_box(q) for q in r.lines]
        blocks.append({
            "bbox": bbox,
            "line_bboxes": line_bboxes,
            "text": (getattr(r, "text", "") or "").strip(),
            "source": "ctd",
        })

    # Recover detection lines that merge/OCR dropped (free-floating captions).
    def _covered(box):
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        for rb in region_boxes:
            if rb[0] <= cx <= rb[2] and rb[1] <= cy <= rb[3]:
                return True
        return False

    for box in det_boxes:
        if _covered(box):
            continue
        if (box[3] - box[1]) > 160:        # skip tall non-text boxes (artwork)
            continue
        blocks.append({"bbox": box, "line_bboxes": [box],
                       "text": ocr_text.get(tuple(box), ""), "source": "ctd-line"})
    return blocks
