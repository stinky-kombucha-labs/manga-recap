#!/usr/bin/env python3
"""
ALL-IN-ONE demo (pages 2 & 3): detect -> OCR -> translate -> inpaint -> render.

This is a self-contained proof of the proper approach: it uses the manga-image-
translator detector (full bubble coverage, unlike the fragmented step1 PaddleOCR),
groups text into bubbles, applies the Ukrainian translations hard-coded below
(so we can SEE the end result without editing translations.json), then renders
with the glyph-match fit from pipeline/render.py.

    .venv/bin/python scripts/demo_detect_translate_render.py
    .venv/bin/python scripts/demo_detect_translate_render.py --variants lama,blur

Output: temp/<novel>/chapter-00001/demo_<variant>/000N.png
The MAIN pipeline stays two-step (step1 editable JSON + step2 render) — this file
is only for trying the detector-based full replacement.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import numpy as np
from PIL import Image

MIT = Path("/home/user/PycharmProjects/Manhwa_Recap/manga-image-translator")
sys.path.insert(0, str(MIT))
sys.path.insert(0, str(Path(__file__).parent))

from manga_translator.detection import dispatch as detect_dispatch
from manga_translator.ocr import dispatch as ocr_dispatch
from manga_translator.textline_merge import dispatch as merge_dispatch
from manga_translator.config import Detector, Ocr, OcrConfig

from pipeline.render import render_page

CH = Path("/home/user/PycharmProjects/Manhwa_Recap/temp/"
          "tales-of-demons-and-gods-manga/chapter-00001")

# Ukrainian translations keyed by a distinctive UPPERCASE substring of the OCR
# English (OCR is noisy, so we match on a robust fragment). Empty => skip (e.g.
# the Tencent watermark). These are functional translations for this project.
TRANSLATIONS: dict[int, dict[str, str]] = {
    2: {
        "GLORY CITY": "МІСТО СЛАВИ",
        # Whole-bubble translations (a merged bubble matches ONE key, so each key
        # carries the FULL bubble text, not a fragment).
        "GOING THROUGH": "Хоч на нього часто нападали звірі, та, пройшовши незліченні спустошливі війни, місто відбудовували знову і знову.",
        "ALTHOUGH THEY WOULD": "Хоч на нього часто нападали звірі, та, пройшовши незліченні спустошливі війни, місто відбудовували знову і знову.",
        "NO ONE KNEW": "Ніхто не знав, що коїться у зовнішньому світі. Кажуть, колись на піку людство мало могутні імперії, але їх знищили. Завдяки прихованому розташуванню місто вціліло з Епохи Темряви.",
        "WIPED OUT": "Ніхто не знав, що коїться у зовнішньому світі. Кажуть, колись на піку людство мало могутні імперії, але їх знищили. Завдяки прихованому розташуванню місто вціліло з Епохи Темряви.",
        "PEOPLE LIVIN": "Люди, що живуть у горах, сотні років не мали зв'язку із зовнішнім світом.",
        "MOTLEY": "Ці строкаті мури були незламним пам'ятником людству. Його назвали...",
        "腾讯": "",
    },
    3: {
        "NEW TEACH": "Чув, новий учитель зі Священної родини, та ще й срібнорангний демон-спіритуаліст!",
        "SACRED FAM": "Священна родина? Це одна з трьох головних родин Міста Слави!",
        "HOLY OR": "Інститут Святої Орхідеї. Клас бійців-учнів.",
        "INSTITUTE": "Інститут Святої Орхідеї. Клас бійців-учнів.",
    },
}
# Fix: OCR misreads (6LORY); match on a stable fragment.
TRANSLATIONS[2]["LORY CITY"] = "МІСТО СЛАВИ"

# Manual regions for text the detector/merge dropped (bbox = [x1,y1,x2,y2]).
MANUAL_REGIONS: dict[int, list[dict]] = {
    2: [
        {"bbox": [20, 0, 1560, 300],
         "translation": "Світ за межами Стародавніх гір давно захопили Сніжні вітрові звірі."},
        {"bbox": [40, 350, 1560, 470],
         "translation": "Люди, що живуть у горах, сотні років не мали зв'язку із зовнішнім світом."},
    ],
    3: [
        # Full top-left caption (both lines) — merge only caught line 1.
        {"bbox": [400, 1130, 1135, 1270],
         "translation": "Інститут Святої Орхідеї. Клас бійців-учнів."},
    ],
}


def merge_into_bubbles(regions: list[dict], gap: int = 36) -> list[dict]:
    """Union regions that belong to the same speech bubble: two regions merge if
    their bboxes overlap when each is expanded by `gap` px. Fixes CTD splitting a
    bubble into several regions (which caused duplicate / overlapping text)."""
    def near(a, b):
        ax1, ay1, ax2, ay2 = a
        bx1, by1, bx2, by2 = b
        return (ax1 - gap < bx2 and bx1 - gap < ax2 and
                ay1 - gap < by2 and by1 - gap < ay2)

    parent = list(range(len(regions)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]; i = parent[i]
        return i

    for i in range(len(regions)):
        for j in range(i + 1, len(regions)):
            if near(regions[i]["bbox"], regions[j]["bbox"]):
                parent[find(i)] = find(j)

    groups: dict[int, list[dict]] = {}
    for i, r in enumerate(regions):
        groups.setdefault(find(i), []).append(r)

    bubbles = []
    for g in groups.values():
        # order member text top-to-bottom, left-to-right for readable concatenation
        g = sorted(g, key=lambda r: (r["bbox"][1], r["bbox"][0]))
        xs1 = [r["bbox"][0] for r in g]; ys1 = [r["bbox"][1] for r in g]
        xs2 = [r["bbox"][2] for r in g]; ys2 = [r["bbox"][3] for r in g]
        line_boxes = [lb for r in g for lb in r["line_bboxes"]]
        english = " ".join(r["english"] for r in g if r["english"]).strip()
        bubbles.append({"bbox": [min(xs1), min(ys1), max(xs2), max(ys2)],
                        "line_bboxes": line_boxes, "english": english})
    return bubbles


def match_translation(pnum: int, english: str) -> str | None:
    norm = english.upper()
    for key, ua in TRANSLATIONS.get(pnum, {}).items():
        if key.upper() in norm:
            return ua
    return None


async def detect_regions(pnum: int) -> tuple[list[dict], tuple[int, int]]:
    img = np.array(Image.open(f"{CH}/pages/{pnum:04d}.png").convert("RGB"))
    h, w = img.shape[:2]
    # CTD (Comic Text Detector) gives full bubble coverage on this manga; the
    # default DBNet dropped low-contrast mid-bubble lines, leaving English behind.
    textlines, _, _ = await detect_dispatch(
        Detector.ctd, img, detect_size=2048, text_threshold=0.3,
        box_threshold=0.4, unclip_ratio=2.3, invert=False, gamma_correct=False,
        rotate=False, device="cuda", verbose=False)
    textlines = await ocr_dispatch(Ocr.ocr48px, img, textlines, OcrConfig(),
                                   device="cuda", verbose=False)
    # EVERY detected textline box — used to clean ALL English, even lines the
    # merge step drops (which is what left English behind before).
    all_line_boxes = []
    for q in textlines:
        qx = [p[0] for p in q.pts]; qy = [p[1] for p in q.pts]
        all_line_boxes.append([int(min(qx)), int(min(qy)), int(max(qx)), int(max(qy))])

    regions = await merge_dispatch(textlines, w, h, verbose=False)
    raw = []
    for r in regions:
        xs = [p[0] for q in r.lines for p in q]
        ys = [p[1] for q in r.lines for p in q]
        bbox = [int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))]
        line_boxes = []
        for q in r.lines:
            qx = [p[0] for p in q]; qy = [p[1] for p in q]
            line_boxes.append([int(min(qx)), int(min(qy)), int(max(qx)), int(max(qy))])
        raw.append({"bbox": bbox, "line_bboxes": line_boxes,
                    "english": getattr(r, "text", "") or ""})

    bubbles = merge_into_bubbles(raw)   # union nearby regions => one bubble

    blocks = []
    for i, b in enumerate(bubbles):
        ua = match_translation(pnum, b["english"])
        status = "OK" if ua else ("skip" if ua == "" else "NO-MATCH")
        print(f"  b{i} bbox={b['bbox']} EN={b['english'][:45]!r} -> {status}")
        if ua:
            blocks.append({"id": i, "bbox": b["bbox"], "line_bboxes": b["line_bboxes"],
                           "original": b["english"], "translation": ua})

    # Manual regions for text the detector/merge dropped (e.g. top caption).
    for j, m in enumerate(MANUAL_REGIONS.get(pnum, [])):
        if m.get("translation", "").strip():
            blocks.append({"id": 1000 + j, "bbox": m["bbox"],
                           "line_bboxes": [m["bbox"]], "original": "(manual)",
                           "translation": m["translation"]})
            all_line_boxes.append(m["bbox"])
    return blocks, all_line_boxes, (w, h)


VARIANT_CFG = {
    "lama": {"bg_mode": "lama", "fit_mode": "glyph_match", "detect_extent": False,
             "glyph_match_ratio": 1.15, "stroke_ratio": 0.10, "font_min": 12,
             "justify": False, "line_spacing": 1.35,
             "inpainting_size": 2560, "mask_dilation": 48, "kernel_size": 7},
    "blur": {"bg_mode": "blur", "fit_mode": "glyph_match", "detect_extent": False,
             "glyph_match_ratio": 1.15, "stroke_ratio": 0.10, "font_min": 12,
             "justify": False, "line_spacing": 1.35,
             "blur_strength": 0.6, "blur_feather": 10},
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", default="lama,blur")
    ap.add_argument("--pages", default="2,3")
    args = ap.parse_args()
    pages = [int(p) for p in args.pages.split(",")]
    variants = [v.strip() for v in args.variants.split(",")]

    for pnum in pages:
        print(f"\n=== page {pnum} ===")
        blocks, all_line_boxes, _ = asyncio.run(detect_regions(pnum))
        # Clean-only block: erases EVERY detected English line (no text drawn).
        clean_block = {"id": 9999, "bbox": [0, 0, 1, 1],
                       "line_bboxes": all_line_boxes, "original": "", "translation": ""}
        render_blocks = [clean_block] + blocks
        src = CH / "pages" / f"{pnum:04d}.png"
        for v in variants:
            out_dir = CH / f"demo_{v}"
            out_dir.mkdir(exist_ok=True)
            outp = out_dir / f"{pnum:04d}.png"
            render_page(src, render_blocks, outp, VARIANT_CFG[v])
            print(f"  rendered [{v}] -> {outp}")


if __name__ == "__main__":
    main()
