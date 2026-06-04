#!/usr/bin/env python3
"""
Step 1 — Detect manga speech bubbles and extract text, then save to translations.json.

Uses manga-image-translator's own detector (DBConvNeXt — trained on manga) + 48px OCR,
which finds actual speech bubbles and ignores page numbers/noise that PaddleOCR picks up.

After running, open translations.json, fill in "translation" for each block,
then run step2_translate.py (Lapa LLM) and step3_render.py to produce the final video.

Run:
    .venv/bin/python scripts/step1_extract.py
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np
import torch
from PIL import Image

from pipeline.chapters import chapter_numbers
from pipeline.bubbles import merge_into_bubbles
from pipeline.detect import detect_page_blocks
from pipeline import jsonfmt

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MIT_ROOT = PROJECT_ROOT / "manga-image-translator"
sys.path.insert(0, str(MIT_ROOT))

from manga_translator.config import Config
from manga_translator.manga_translator import MangaTranslator


def load_config() -> dict:
    with (PROJECT_ROOT / "config.json").open() as f:
        return json.load(f)


def chapter_dir_name(n: int) -> str:
    return f"chapter-{n:05d}"


# ---------------------------------------------------------------------------
# MIT detect-only translator
# ---------------------------------------------------------------------------

class _DetectOCRTranslator(MangaTranslator):
    """Runs MIT detection + OCR only. Skips translation, inpainting, rendering."""

    def __init__(self):
        super().__init__({
            "use_gpu": bool(torch.cuda.is_available() or torch.backends.mps.is_available()),
            "kernel_size": 3,
            "font_path": str(MIT_ROOT / "fonts" / "anime_ace_3.ttf"),
            "model_dir": str(MIT_ROOT / "models"),
            "input": [],
            "ignore_errors": False,
            "models_ttl": 0,
        })

    async def _run_text_translation(self, config, ctx):
        return ctx.text_regions

    async def _run_inpainting(self, config, ctx):
        return ctx.img_rgb

    async def _run_text_rendering(self, config, ctx):
        return ctx.img_rgb


_DETECT_CONFIG = Config(**{
    "detector": {
        "detector": "ctd",              # Comic Text Detector — full bubble coverage
        "detection_size": 2048,
        "text_threshold": 0.3,
        "box_threshold": 0.4,
    },
    "ocr": {
        "ocr": "48px",                  # English OCR
        "min_text_length": 1,
        "ignore_bubble": 0,
    },
    "translator": {"translator": "none", "target_lang": "ENG",
                   "enable_post_translation_check": False},
    "inpainter": {"inpainter": "none", "inpainting_size": 1},
    "render": {"renderer": "none"},
    "mask_dilation_offset": 0,
    "kernel_size": 3,
})

_translator: _DetectOCRTranslator | None = None


def _get_translator() -> _DetectOCRTranslator:
    global _translator
    if _translator is None:
        _translator = _DetectOCRTranslator()
    return _translator


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def _clip_bbox(box: list[int], width: int, height: int) -> list[int]:
    x1, y1, x2, y2 = [int(v) for v in box]
    x1 = _clamp(x1, 0, max(0, width - 1))
    y1 = _clamp(y1, 0, max(0, height - 1))
    x2 = _clamp(x2, 1, width)
    y2 = _clamp(y2, 1, height)
    return [min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)]


def _bbox_area(box: list[int]) -> int:
    return max(0, int(box[2]) - int(box[0])) * max(0, int(box[3]) - int(box[1]))


def _bbox_iou(a: list[int] | None, b: list[int] | None) -> float:
    if not a or not b:
        return 0.0
    ax1, ay1, ax2, ay2 = [int(v) for v in a]
    bx1, by1, bx2, by2 = [int(v) for v in b]
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = _bbox_area([ix1, iy1, ix2, iy2])
    if inter <= 0:
        return 0.0
    union = _bbox_area(a) + _bbox_area(b) - inter
    return inter / union if union > 0 else 0.0


def _center_distance_ratio(a: list[int] | None, b: list[int] | None) -> float:
    if not a or not b:
        return 1.0
    acx, acy = (a[0] + a[2]) * 0.5, (a[1] + a[3]) * 0.5
    bcx, bcy = (b[0] + b[2]) * 0.5, (b[1] + b[3]) * 0.5
    aw, ah = max(1, a[2] - a[0]), max(1, a[3] - a[1])
    bw, bh = max(1, b[2] - b[0]), max(1, b[3] - b[1])
    scale = max(32.0, ((aw * aw + ah * ah) ** 0.5 + (bw * bw + bh * bh) ** 0.5) * 0.5)
    return (((acx - bcx) ** 2 + (acy - bcy) ** 2) ** 0.5) / scale


def _normalized_text(text: str) -> str:
    return "".join(ch.lower() for ch in text if ch.isalnum())


def _text_similarity(a: str, b: str) -> float:
    na, nb = _normalized_text(a), _normalized_text(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


def _is_noise_text(text: str) -> bool:
    text = (text or "").strip()
    if not text:
        return True
    tl = text.lower()
    # Scanlation site addresses / reader links.
    if any(w in tl for w in (".net", ".com", ".org", "http", "www.", "reader", "scans", "webtoon")):
        return True
    # Scanlation credit lines (translator/editor/cleaner/typesetter/etc.) — not story
    # text, never narrated, so drop them before they reach translations.json.
    if any(w in tl for w in ("translator", "translation:", "redraw", "redrawer", "cleaner",
                             "typesetter", "proofread", "scanlat", "uploader", "raws",
                             "edited by", "edit:", "credits")):
        return True
    cjk = sum(1 for c in text if unicodedata.east_asian_width(c) in ("W", "F"))
    if cjk and cjk / max(1, len(text)) > 0.5:
        return True
    useful = sum(1 for c in text if c.isalnum())
    expressive = any(c in text for c in "!?...")
    return useful == 0 and not expressive


def _normalize_detected_block(raw: dict, width: int, height: int, source: str) -> dict | None:
    text = (raw.get("text") or raw.get("original") or "").strip()
    if _is_noise_text(text):
        return None

    line_bboxes = []
    for lb in raw.get("line_bboxes") or []:
        clipped = _clip_bbox(lb, width, height)
        if _bbox_area(clipped) > 0:
            line_bboxes.append(clipped)

    if raw.get("bbox"):
        bbox = _clip_bbox(raw["bbox"], width, height)
    elif line_bboxes:
        bbox = [
            min(b[0] for b in line_bboxes),
            min(b[1] for b in line_bboxes),
            max(b[2] for b in line_bboxes),
            max(b[3] for b in line_bboxes),
        ]
    else:
        return None

    if _bbox_area(bbox) <= 0:
        return None
    if not line_bboxes:
        line_bboxes = [bbox]

    return {
        "bbox": bbox,
        "line_bboxes": line_bboxes,
        "text": text,
        "source": source,
    }


def _expanded_contains(container: list[int], inner: list[int], ratio: float = 0.25) -> bool:
    x1, y1, x2, y2 = container
    w, h = max(1, x2 - x1), max(1, y2 - y1)
    pad_x = max(12, int(w * ratio))
    pad_y = max(8, int(h * ratio))
    cx, cy = (inner[0] + inner[2]) * 0.5, (inner[1] + inner[3]) * 0.5
    return (x1 - pad_x) <= cx <= (x2 + pad_x) and (y1 - pad_y) <= cy <= (y2 + pad_y)


def _is_duplicate_block(candidate: dict, existing: list[dict], iou_threshold: float) -> bool:
    cb = candidate["bbox"]
    ct = candidate.get("text", "")
    for block in existing:
        eb = block["bbox"]
        if _bbox_iou(cb, eb) >= iou_threshold:
            return True
        if _expanded_contains(eb, cb) and _text_similarity(ct, block.get("text", "")) >= 0.25:
            return True
        if _center_distance_ratio(cb, eb) <= 0.28 and _text_similarity(ct, block.get("text", "")) >= 0.70:
            return True
    return False


def _sort_blocks(blocks: list[dict]) -> list[dict]:
    return sorted(blocks, key=lambda b: (b["bbox"][1], b["bbox"][0], b["bbox"][3], b["bbox"][2]))


def _merge_paddle_fallback(
    image_path: Path,
    blocks: list[dict],
    image_size: tuple[int, int],
    detection_cfg: dict,
) -> list[dict]:
    if not detection_cfg.get("paddle_fallback", True):
        return _sort_blocks(blocks)

    try:
        from pipeline.ocr import ocr_page as paddle_ocr_page
    except Exception as exc:
        print(f"\n    Paddle fallback unavailable: {exc}", end=" ")
        return _sort_blocks(blocks)

    min_conf = float(detection_cfg.get("paddle_min_confidence", 0.64))
    duplicate_iou = float(detection_cfg.get("fallback_duplicate_iou", 0.18))
    min_area = int(detection_cfg.get("min_block_area", 24))
    max_area_ratio = float(detection_cfg.get("max_block_area_ratio", 0.20))
    width, height = image_size
    image_area = max(1, width * height)

    try:
        fallback_raw = paddle_ocr_page(image_path, min_confidence=min_conf)
    except Exception as exc:
        print(f"\n    Paddle fallback failed: {exc}", end=" ")
        return _sort_blocks(blocks)

    merged = list(blocks)
    added = 0
    for raw in fallback_raw:
        block = _normalize_detected_block(raw, width, height, "paddle")
        if not block:
            continue
        area = _bbox_area(block["bbox"])
        if area < min_area or area / image_area > max_area_ratio:
            continue
        if _is_duplicate_block(block, merged, duplicate_iou):
            continue
        merged.append(block)
        added += 1

    if added:
        print(f"+{added} paddle", end=" ")
    return _sort_blocks(merged)


def _recover_empty_text_with_paddle(image_path: Path, blocks: list[dict], detection_cfg: dict) -> list[dict]:
    """Fill blocks the CTD detector found but ocr48px could not read.

    ocr48px silently drops stylized/textured English captions (e.g. the yellow
    "I SHOULD BE SURROUNDED BY THE SAGE EMPEROR..." narration boxes), leaving the
    region detected but with empty text. Those used to be filled in by hand; now
    that translation is automatic (Lapa only sees `original`), an empty original
    means the caption is never translated. PaddleOCR handles these outlined
    captions, so we re-OCR just the empty boxes with it.

    Only runs when there ARE empty boxes, so clean pages pay no Paddle cost.
    """
    if not detection_cfg.get("recover_empty_ocr", True):
        return blocks
    empties = [b for b in blocks if not (b.get("text") or "").strip()]
    if not empties:
        return blocks
    non_empty = [b for b in blocks if (b.get("text") or "").strip()]

    try:
        from pipeline.ocr import _get_paddle_ocr
        ocr = _get_paddle_ocr()
        page = Image.open(image_path).convert("RGB")
    except Exception as exc:
        print(f"\n    OCR-recovery unavailable: {exc}", end=" ")
        return blocks

    page_w, page_h = page.size
    min_conf = float(detection_cfg.get("recover_min_confidence", 0.5))

    def _center_in(box, cx, cy):
        return box[0] <= cx <= box[2] and box[1] <= cy <= box[3]

    # Re-OCR each empty box individually on a padded CROP, not the whole page.
    # Whole-page PaddleOCR mis-localizes these captions, and CTD's boxes are often
    # too tight (they clip the text), so we pad generously, read the crop, offset
    # the line boxes back to page coords, and GROW the block to fit — that way the
    # inpaint mask covers the full English and Lapa gets the complete sentence.
    recovered = 0
    for block in empties:
        x1, y1, x2, y2 = block["bbox"]
        pad_x = max(30, int((x2 - x1) * 0.10))
        pad_y = max(60, int((y2 - y1) * 0.6))
        cx1, cy1 = max(0, x1 - pad_x), max(0, y1 - pad_y)
        cx2, cy2 = min(page_w, x2 + pad_x), min(page_h, y2 + pad_y)
        try:
            result = ocr.predict(np.array(page.crop((cx1, cy1, cx2, cy2))))
        except Exception:
            continue
        if not result:
            continue
        res = result[0]

        def _as_list(v):
            # rec_* may be a numpy array, where `v or []` raises "ambiguous truth value".
            try:
                return list(v) if v is not None and len(v) else []
            except TypeError:
                return []

        texts = _as_list(res.get("rec_texts"))
        scores = _as_list(res.get("rec_scores"))
        polys = _as_list(res.get("rec_polys")) or _as_list(res.get("rec_boxes"))

        found: list[tuple[list[int], str, float, float]] = []
        for text, conf, poly in zip(texts, scores, polys):
            if float(conf) < min_conf or not str(text).strip():
                continue
            arr = np.array(poly)
            if arr.ndim == 2:
                xs = [p[0] for p in arr.tolist()]
                ys = [p[1] for p in arr.tolist()]
                bb = [int(min(xs)) + cx1, int(min(ys)) + cy1,
                      int(max(xs)) + cx1, int(max(ys)) + cy1]
            else:
                a = arr.flatten()[:4]
                bb = [int(min(a[0], a[2])) + cx1, int(min(a[1], a[3])) + cy1,
                      int(max(a[0], a[2])) + cx1, int(max(a[1], a[3])) + cy1]
            mcx, mcy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
            # Skip text that belongs to a bubble ocr48px already read.
            if any(_center_in(nb["bbox"], mcx, mcy) for nb in non_empty):
                continue
            found.append((bb, str(text).strip(), mcx, mcy))

        if not found:
            continue
        # Reading order: cluster lines into rows by y (a wrapping caption's words must
        # not interleave), then left-to-right within each row. Plain (y, x) sort scrambles
        # word order when line boxes overlap vertically.
        found.sort(key=lambda it: it[3])
        row_tol = float(np.median([it[0][3] - it[0][1] for it in found])) * 0.7
        rows = [[found[0]]]
        for it in found[1:]:
            if it[3] - rows[-1][-1][3] > row_tol:
                rows.append([it])
            else:
                rows[-1].append(it)
        for row in rows:
            row.sort(key=lambda it: it[2])
        found = [it for row in rows for it in row]
        recovered_text = " ".join(it[1] for it in found)
        if _is_noise_text(recovered_text):           # recovered a credit line / URL — skip
            continue
        boxes = [it[0] for it in found]
        block["text"] = recovered_text
        block["source"] = "ctd-paddle"   # mark: recovered from a stylized caption →
                                          # OCR-error-prone, worth an image check (flag `verify`)
        block["line_bboxes"] = boxes
        block["bbox"] = [min(b[0] for b in boxes), min(b[1] for b in boxes),
                         max(b[2] for b in boxes), max(b[3] for b in boxes)]
        recovered += 1

    if recovered:
        print(f"+{recovered} ocr-recover", end=" ")
    return blocks


async def _detect_page(image_path: Path, detection_cfg: dict | None = None) -> list[dict]:
    """
    Run CTD detection + 48px OCR on one page (via pipeline.detect), union regions
    into bubbles, drop noise. Returns list of blocks: {bbox, line_bboxes, text}.
    """
    detection_cfg = detection_cfg or {}
    image = Image.open(image_path).convert("RGB")
    width, height = image.size

    raw_blocks = await detect_page_blocks(np.array(image), detection_cfg)

    blocks = []
    for raw in raw_blocks:
        src = raw.get("source", "ctd")
        block = _normalize_detected_block(raw, width, height, src)
        if block:
            blocks.append(block)
        elif src == "ctd-line":
            # Recovered free caption line: OCR was empty, but keep the box so the
            # English gets cleaned and the user can add a translation by eye.
            bbox = _clip_bbox(raw["bbox"], width, height)
            if _bbox_area(bbox) > 0:
                blocks.append({"bbox": bbox, "line_bboxes": [bbox],
                               "text": (raw.get("text") or "").strip(), "source": src})

    # Union regions belonging to the same speech bubble -> one bubble per block.
    gap = int(detection_cfg.get("bubble_merge_gap", 36))
    blocks = merge_into_bubbles(blocks, gap=gap)

    # Re-OCR boxes ocr48px left empty (stylized captions) with PaddleOCR so they
    # carry text into translation instead of staying blank.
    blocks = _recover_empty_text_with_paddle(image_path, blocks, detection_cfg)

    # Optional PaddleOCR fill (off by default; CTD already covers full bubbles).
    if detection_cfg.get("paddle_fallback", False):
        blocks = _merge_paddle_fallback(image_path, blocks, (width, height), detection_cfg)
    return _sort_blocks(blocks)


# ---------------------------------------------------------------------------
# Chapter extraction
# ---------------------------------------------------------------------------
# Page sorting
# ---------------------------------------------------------------------------

import re as _re


def _manga_page_number(path: Path) -> int:
    """
    Extract the actual manga page number from the filename.

    Filenames from manga_upscaler look like:
        00010_0010-tales-of-demons-and-gods-1-1.png   ← page 1
        00001_0001-tales-of-demons-and-gods-1-12.png  ← page 12

    The page number is the last integer before the file extension.
    Falls back to alphabetical sort if the pattern doesn't match.
    """
    m = _re.search(r"-(\d+)\.[^.]+$", path.name)
    return int(m.group(1)) if m else 0


def _sorted_manga_pages(source_dir: Path) -> list[Path]:
    """Return manga pages sorted by their in-chapter page number."""
    pages = list(source_dir.glob("*.png")) + list(source_dir.glob("*.jpg"))
    return sorted(pages, key=_manga_page_number)


def _load_existing_pages(out_json: Path) -> dict[str, list[dict]]:
    if not out_json.exists():
        return {}
    try:
        old = json.loads(out_json.read_text())
    except Exception:
        return {}

    existing: dict[str, list[dict]] = {}
    for page in old.get("pages", []):
        key = str(page.get("page_num", ""))
        blocks = []
        for pos, block in enumerate(page.get("blocks", [])):
            translation = block.get("translation", "")
            if not str(translation).strip():
                continue
            blocks.append({
                "id": block.get("id", pos),
                "_pos": pos,
                "bbox": block.get("bbox"),
                "original": block.get("original", ""),
                "translation": translation,
            })
        if blocks:
            existing[key] = blocks
    return existing


def _match_existing_translation(new_block: dict, old_blocks: list[dict], used_old: set[int]) -> str:
    """
    Preserve manual translations by spatial/text similarity instead of raw block id.

    MIT/Paddle can change detection order between runs. Reusing translations by id
    alone can place a Ukrainian line into the wrong speech bubble.
    """
    best: tuple[float, int, dict] | None = None
    for old in old_blocks:
        old_key = int(old.get("_pos", old.get("id", -1)))
        if old_key in used_old:
            continue

        iou = _bbox_iou(new_block.get("bbox"), old.get("bbox"))
        dist = _center_distance_ratio(new_block.get("bbox"), old.get("bbox"))
        text_sim = _text_similarity(new_block.get("text", ""), old.get("original", ""))
        near = max(0.0, 1.0 - dist * 4.0)
        score = iou * 0.62 + near * 0.26 + text_sim * 0.12
        if text_sim >= 0.92 and dist <= 0.22:
            score += 0.18

        if best is None or score > best[0]:
            best = (score, old_key, old)

    if best is None:
        return ""

    score, old_key, old = best
    iou = _bbox_iou(new_block.get("bbox"), old.get("bbox"))
    dist = _center_distance_ratio(new_block.get("bbox"), old.get("bbox"))
    text_sim = _text_similarity(new_block.get("text", ""), old.get("original", ""))

    if score >= 0.38 or iou >= 0.20 or (text_sim >= 0.92 and dist <= 0.22):
        used_old.add(old_key)
        return old.get("translation", "")
    return ""


def _copy_page_if_changed(src: Path, dst: Path) -> None:
    if not dst.exists():
        shutil.copy2(src, dst)
        return
    try:
        src_stat = src.stat()
        dst_stat = dst.stat()
        if src_stat.st_size != dst_stat.st_size or src_stat.st_mtime_ns > dst_stat.st_mtime_ns:
            shutil.copy2(src, dst)
    except OSError:
        shutil.copy2(src, dst)


# ---------------------------------------------------------------------------

def extract_chapter(chapter_num: int, cfg: dict) -> Path:
    novel = cfg["novel"]
    src = Path(novel["source_dir"])
    source_dir = (src if src.is_absolute() else PROJECT_ROOT / src) / chapter_dir_name(chapter_num)
    if not source_dir.exists():
        raise FileNotFoundError(f"Not found: {source_dir}")

    novel_slug = novel["folder"]
    work_dir = PROJECT_ROOT / "temp" / novel_slug / chapter_dir_name(chapter_num)
    pages_dir = work_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)

    out_json = work_dir / "translations.json"

    # Preserve existing manual translations by bbox/text, not by fragile block order.
    existing = _load_existing_pages(out_json)
    detection_cfg = cfg.get("detection", {})

    pages = _sorted_manga_pages(source_dir)
    print(f"Chapter {chapter_num}: {len(pages)} pages  (MIT detector + 48px OCR)")

    result_pages = []
    for idx, src_page in enumerate(pages, 1):
        dst_page = pages_dir / f"{idx:04d}{src_page.suffix}"
        _copy_page_if_changed(src_page, dst_page)

        print(f"  [{idx}/{len(pages)}] {src_page.name} ...", end=" ", flush=True)
        blocks_raw = asyncio.run(_detect_page(dst_page, detection_cfg))
        print(f"{len(blocks_raw)} blocks")

        prev = existing.get(str(idx), [])
        used_prev: set[int] = set()
        blocks_out = []
        for bid, b in enumerate(blocks_raw):
            blocks_out.append({
                "id": bid,
                "original": b["text"],
                "translation": _match_existing_translation(b, prev, used_prev),
                "detector": b.get("source", "mit"),
                "bbox": b["bbox"],
                "line_bboxes": b["line_bboxes"],
            })

        result_pages.append({
            "page_num": idx,
            "source_file": src_page.name,
            "blocks": blocks_out,
        })

    data = {
        "novel": novel["name"],
        "chapter": chapter_num,
        "pages": result_pages,
    }
    jsonfmt.write(out_json, data)
    print(f"\nSaved: {out_json}")
    print("Next: run step2_translate.py to fill 'translation' with Lapa LLM, then step3_render.py")
    return out_json


def main():
    if len(sys.argv) > 1:
        raise SystemExit("step1_extract.py reads chapters from config.json -> run.chapters; remove CLI arguments.")
    cfg = load_config()
    for chapter_num in chapter_numbers(cfg):
        extract_chapter(chapter_num, cfg)


if __name__ == "__main__":
    main()
