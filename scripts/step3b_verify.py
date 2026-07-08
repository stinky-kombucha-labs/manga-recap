#!/usr/bin/env python3
"""
Step 3b — Verify rendered pages: OCR each rendered PNG and report leftover
English text, so nobody has to eyeball 959 chapters.

Runs PaddleOCR over every page in temp/<novel>/<chapter>/rendered/ and flags
Latin text lines that survived the render. Lines inside blocks that were
INTENTIONALLY left as art (empty translation on a real-text block — SFX) are
listed separately as "intentional" so real leaks stand out.

Writes temp/<novel>/<chapter>/render_qa.json and prints a summary. Exit code is
0 when no leaks, 2 when any page still shows unexpected English.

With --fix, leaks that look like scanlation junk (URLs/watermarks/credits) are
appended to translations.json as `noise` blocks — the next step3_render.py run
re-renders just those pages and inpaints the leftovers away. Leaks that look
like real story text are NEVER auto-erased, only reported.

Run (after step3_render.py):
    .venv/bin/python scripts/step3b_verify.py
    .venv/bin/python scripts/step3b_verify.py --fix   # then re-run step3_render.py
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from pipeline.chapters import chapter_numbers
from pipeline import jsonfmt

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_config() -> dict:
    with (PROJECT_ROOT / "config.json").open() as f:
        return json.load(f)


def chapter_dir_name(n: int) -> str:
    return f"chapter-{n:05d}"


def _latin_letters(text: str) -> int:
    return len(re.findall(r"[A-Za-z]", text))


def _looks_english(text: str) -> bool:
    letters = _latin_letters(text)
    cyr = len(re.findall(r"[А-Яа-яІіЇїЄєҐґ]", text))
    return letters >= 4 and letters > cyr


def _center_in(bbox: list[int], cx: float, cy: float, pad: int = 12) -> bool:
    return (bbox[0] - pad) <= cx <= (bbox[2] + pad) and (bbox[1] - pad) <= cy <= (bbox[3] + pad)


def verify_chapter(chapter_num: int, cfg: dict, min_conf: float = 0.6,
                   fix: bool = False) -> dict:
    from pipeline.ocr import _get_paddle_ocr
    from step1_extract import _paddle_result_lines, _is_noise_text

    novel = cfg["novel"]
    work_dir = PROJECT_ROOT / "temp" / novel["folder"] / chapter_dir_name(chapter_num)
    rendered_dir = work_dir / "rendered"
    translations_path = work_dir / "translations.json"
    data = json.loads(translations_path.read_text())

    ocr = _get_paddle_ocr()
    report = {"chapter": chapter_num, "pages": [], "leaks": 0, "intentional": 0}

    for page in data.get("pages", []):
        idx = page["page_num"]
        img_path = rendered_dir / f"{idx:04d}.png"
        if not img_path.exists():
            report["pages"].append({"page_num": idx, "error": "no rendered page"})
            continue

        # Ukrainian is only painted INSIDE blocks that have a translation — the
        # English OCR model reads that Cyrillic as garbled Latin, so lines inside
        # rendered blocks must be ignored, not reported. Genuine leaks can only
        # live OUTSIDE all detected blocks (detection miss) or inside noise boxes
        # (inpaint failed to fully erase a URL/credit).
        rendered_boxes, art_boxes = [], []
        for b in page.get("blocks", []):
            boxes = [b["bbox"]] + list(b.get("line_bboxes") or [])
            if (b.get("translation") or "").strip():
                rendered_boxes.extend(boxes)
            elif not b.get("noise") and (b.get("original") or "").strip():
                # Deliberately left as art (blanked SFX) — English stays by design.
                art_boxes.extend(boxes)

        page_img = Image.open(img_path).convert("RGB")
        page_h = page_img.size[1]
        result = ocr.predict(np.array(page_img))
        leaks, intentional, sfx_art = [], [], []
        if result:
            for bb, text in _paddle_result_lines(result[0], 0, 0, min_conf):
                if not _looks_english(text):
                    continue
                cx, cy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
                if any(_center_in(rb, cx, cy, pad=24) for rb in rendered_boxes):
                    continue
                entry = {"text": text, "bbox": bb}
                if any(_center_in(ab, cx, cy, pad=24) for ab in art_boxes):
                    intentional.append(entry)
                elif (len(text.split()) <= 2
                        and sum(1 for ch in text if ch.isalnum()) <= 14):
                    # Short SFX-ish word the detector never boxed ("STEP",
                    # "NODS", "BANG") — stays as art by design; report it
                    # separately so real leaks stand out.
                    sfx_art.append(entry)
                else:
                    leaks.append(entry)

        # --fix: junk-looking leaks become noise blocks in translations.json, so
        # the next render pass inpaints them. Real-text leaks are only reported.
        if fix and leaks:
            # A leak wholly inside the top/bottom margin strip is junk even when
            # its text matches no pattern (garbled site logos: "AGORGON" ←
            # ac.qq.com) — story text never fits entirely in that strip.
            strip = max(40, int(page_h * 0.05))
            fixed_here = []
            for leak in leaks:
                in_margin = leak["bbox"][3] <= strip or leak["bbox"][1] >= page_h - strip
                if not (_is_noise_text(leak["text"]) or in_margin):
                    continue
                blocks = page.setdefault("blocks", [])
                pad = 8
                bb = [max(0, leak["bbox"][0] - pad), max(0, leak["bbox"][1] - pad),
                      leak["bbox"][2] + pad, leak["bbox"][3] + pad]
                blocks.append({
                    "id": max((b.get("id", -1) for b in blocks), default=-1) + 1,
                    "original": leak["text"],
                    "translation": "",
                    "detector": "qa-leak",
                    "bbox": bb,
                    "line_bboxes": [bb],
                    "noise": True,
                })
                fixed_here.append(leak)
            leaks = [l for l in leaks if l not in fixed_here]
            report["fixed"] = report.get("fixed", 0) + len(fixed_here)

        report["leaks"] += len(leaks)
        report["intentional"] += len(intentional)
        report["sfx_art"] = report.get("sfx_art", 0) + len(sfx_art)
        report["pages"].append({"page_num": idx, "leaks": leaks,
                                "intentional": intentional, "sfx_art": sfx_art})
        status = "OK" if not leaks else f"{len(leaks)} LEAK(S): " + "; ".join(l["text"] for l in leaks[:3])
        if sfx_art and not leaks:
            status += f"  ({len(sfx_art)} sfx-as-art)"
        print(f"  [{idx:>3}] {status}")

    if fix and report.get("fixed"):
        jsonfmt.write(translations_path, data)
        print(f"  {report['fixed']} junk leak(s) added as noise blocks -> re-run step3_render.py")

    out_path = work_dir / "render_qa.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  -> {out_path}  ({report['leaks']} leak(s), {report['intentional']} intentional art block(s))")
    return report


def main():
    parser = argparse.ArgumentParser(description="Verify rendered pages for leftover English.")
    parser.add_argument("--fix", action="store_true",
                        help="append junk-looking leaks to translations.json as noise blocks")
    args = parser.parse_args()
    cfg = load_config()
    total_leaks = total_fixed = 0
    for chapter_num in chapter_numbers(cfg):
        print(f"Chapter {chapter_num}:")
        report = verify_chapter(chapter_num, cfg, fix=args.fix)
        total_leaks += report["leaks"]
        total_fixed += report.get("fixed", 0)
    # Exit codes: 0 clean, 2 real leaks remain (need eyes), 3 junk leaks were
    # queued as noise blocks (re-run step3_render.py, then verify again).
    if total_fixed:
        print(f"\n{total_fixed} junk leak(s) queued for inpainting — re-run step3_render.py, then verify again.")
    if total_leaks:
        print(f"\n{total_leaks} leftover English line(s) found — check render_qa.json")
        raise SystemExit(2)
    if total_fixed:
        raise SystemExit(3)
    print("\nAll pages clean — no leftover English detected.")


if __name__ == "__main__":
    main()
