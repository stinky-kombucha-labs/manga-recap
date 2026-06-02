#!/usr/bin/env python3
"""
Detect + OCR + merge into bubble-level regions using the manga-image-translator
models (the proper detector PaddleOCR-in-step1 was missing). Dumps regions
(bbox, line boxes, English text) to JSON so they can be translated and rendered.

    .venv/bin/python scripts/detect_ocr_dump.py 2 3

Output: temp/<novel>/chapter-00001/detected_<page>.json
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

MIT = Path("/home/user/PycharmProjects/Manhwa_Recap/manga-image-translator")
sys.path.insert(0, str(MIT))

from manga_translator.detection import dispatch as detect_dispatch
from manga_translator.ocr import dispatch as ocr_dispatch
from manga_translator.textline_merge import dispatch as merge_dispatch
from manga_translator.config import Detector, Ocr, OcrConfig

CH = Path("/home/user/PycharmProjects/Manhwa_Recap/temp/"
          "tales-of-demons-and-gods-manga/chapter-00001")


async def process(pnum: int) -> dict:
    img = np.array(Image.open(f"{CH}/pages/{pnum:04d}.png").convert("RGB"))
    h, w = img.shape[:2]

    textlines, mask, _ = await detect_dispatch(
        Detector.default, img, detect_size=2560, text_threshold=0.5,
        box_threshold=0.7, unclip_ratio=2.3, invert=False, gamma_correct=False,
        rotate=False, device="cuda", verbose=False)

    textlines = await ocr_dispatch(Ocr.ocr48px, img, textlines,
                                   OcrConfig(), device="cuda", verbose=False)

    regions = await merge_dispatch(textlines, w, h, verbose=False)

    out = {"page_num": pnum, "width": w, "height": h, "regions": []}
    for i, r in enumerate(regions):
        xs = [p[0] for q in r.lines for p in q]
        ys = [p[1] for q in r.lines for p in q]
        bbox = [int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))]
        line_boxes = []
        for q in r.lines:
            qx = [p[0] for p in q]; qy = [p[1] for p in q]
            line_boxes.append([int(min(qx)), int(min(qy)), int(max(qx)), int(max(qy))])
        out["regions"].append({
            "id": i,
            "bbox": bbox,
            "line_bboxes": line_boxes,
            "english": getattr(r, "text", "") or "",
            "translation": "",
        })
    return out


def annotate(data: dict):
    pnum = data["page_num"]
    im = Image.open(f"{CH}/pages/{pnum:04d}.png").convert("RGB")
    dr = ImageDraw.Draw(im)
    try:
        font = ImageFont.truetype("/usr/share/fonts/google-noto/NotoSans-Bold.ttf", 42)
    except Exception:
        font = ImageFont.load_default()
    for r in data["regions"]:
        x1, y1, x2, y2 = r["bbox"]
        dr.rectangle([x1, y1, x2, y2], outline=(255, 0, 0), width=6)
        dr.text((x1 + 4, y1 + 4), f"r{r['id']}", fill=(255, 0, 0), font=font)
    p = f"{CH}/DETREG_{pnum:04d}.png"
    im.save(p)
    print("annotated", p)


def main():
    pages = [int(a) for a in sys.argv[1:]] or [2, 3]
    for pnum in pages:
        data = asyncio.run(process(pnum))
        outp = CH / f"detected_{pnum:04d}.json"
        outp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        print(f"\n=== page {pnum}: {len(data['regions'])} regions -> {outp}")
        for r in data["regions"]:
            print(f"  r{r['id']} bbox={r['bbox']} EN={r['english']!r}")
        annotate(data)


if __name__ == "__main__":
    main()
