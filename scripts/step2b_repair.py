#!/usr/bin/env python3
"""
Step 2b — Flag suspicious translations, auto-repair them locally with Lapa, then
write a short review list for Claude. Keeps the Claude pass tiny so it scales to 900+
chapters.

Pipeline position:
    step1_extract → step2_translate → step2b_repair (THIS) → [Claude on review_todo] → step3_render

What it does, per chapter:
  1. Flag every block with a deterministic check (pipeline/flag.py): leftover Latin,
     "explanation" hallucinations, bad length ratio, empty translation, mixed script.
  2. Re-translate the flagged blocks (that have English source) through Lapa with a
     short CORRECTIVE prompt ("fix OCR errors, translate by meaning, no explanations").
     This is local and free — it fixes most issues without any Claude tokens.
  3. Re-flag. Whatever is STILL suspicious is written to review_todo.json next to
     translations.json — that small list is all Claude needs to look at.

Run:
    .venv/bin/python scripts/step2b_repair.py
    .venv/bin/python scripts/step2b_repair.py --no-repair   # only flag, don't call Lapa
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from pipeline.chapters import chapter_numbers
from pipeline import jsonfmt
from pipeline.flag import flag_block
from pipeline.translate import translate_texts

PROJECT_ROOT = Path(__file__).resolve().parents[1]

REPAIR_PROMPT = (
    "Переклади українською. Це текст з OCR манги, можливі помилки розпізнавання — "
    "виправляй за змістом. Дай лише переклад, без пояснень:\n\n{text}"
)


def load_config() -> dict:
    with (PROJECT_ROOT / "config.json").open() as f:
        return json.load(f)


def chapter_dir_name(n: int) -> str:
    return f"chapter-{n:05d}"


def _flagged(pages: list[dict], flag_cfg: dict) -> list[tuple[dict, dict, list[str]]]:
    """Return [(page, block, reasons)] for every flagged block."""
    out = []
    for page in pages:
        for block in page.get("blocks", []):
            reasons = flag_block(block, flag_cfg)
            if reasons:
                out.append((page, block, reasons))
    return out


def collect_chapter(chapter_num: int, cfg: dict):
    """Load one chapter and collect its flagged blocks needing a repair pass.
    Keys are "<page_num>|<block_id>" (caller prefixes the chapter)."""
    novel = cfg["novel"]
    work_dir = PROJECT_ROOT / "temp" / novel["folder"] / chapter_dir_name(chapter_num)
    translations_path = work_dir / "translations.json"
    if not translations_path.exists():
        raise FileNotFoundError(f"Run step1_extract.py + step2_translate.py first: {translations_path}")

    data = json.loads(translations_path.read_text())
    pages = data.get("pages", [])
    flag_cfg = cfg.get("flag", {})

    flagged = _flagged(pages, flag_cfg)
    total_blocks = sum(len(p.get("blocks", [])) for p in pages)
    print(f"Chapter {chapter_num}: {total_blocks} blocks, {len(flagged)} flagged")

    pending = []
    index = {}
    for page, block, reasons in flagged:
        original = (block.get("original") or "").strip()
        if not any(ch.isalpha() for ch in original):
            continue  # nothing to retranslate (empty source caption)
        # `verify`-only blocks are flagged for their SOURCE (fallback-OCR text),
        # not their translation — re-translating buys nothing and, worse, every
        # re-run would overwrite the human review fixes these blocks get. They
        # go straight to review_todo.json.
        if set(reasons) == {"verify"}:
            continue
        key = f"{page['page_num']}|{block['id']}"
        pending.append((key, original))
        index[key] = block
    return data, translations_path, work_dir, pending, index


def finish_chapter(chapter_num: int, cfg: dict, data: dict, translations_path: Path,
                   work_dir: Path, repaired: int) -> Path:
    """Re-flag after the (batched) repair pass, auto-clear untranslatable SFX,
    write the small Claude review list."""
    pages = data.get("pages", [])
    flag_cfg = cfg.get("flag", {})
    if repaired:
        print(f"  Chapter {chapter_num}: Lapa changed {repaired} translation(s).")
        jsonfmt.write(translations_path, data)

    # --- Re-flag, then auto-clear untranslatable Latin SFX ---
    remaining = _flagged(pages, flag_cfg)

    # A block still mostly-Latin after the Lapa repair is SFX/garbage that has no
    # meaningful translation (a real sentence would have been translated). Blank it —
    # better an empty box (original art stays) than Latin gibberish in the video — and
    # drop it from the review list. Skip ones also flagged `verify` (those need eyes).
    auto_emptied = 0
    kept = []
    for page, block, reasons in remaining:
        t = (block.get("translation") or "").strip()
        latin = sum(ch.isascii() and ch.isalpha() for ch in t)
        cyr = len(re.findall(r"[А-Яа-яІіЇїЄєҐґ]", t))
        latin_ratio = latin / (latin + cyr) if (latin + cyr) else 0.0
        if set(reasons) <= {"latin", "mixed"} and latin_ratio > 0.5:
            block["translation"] = ""
            block["keep_empty"] = True   # deliberate blank: step2 must not refill it
            auto_emptied += 1
            continue
        # Still a bare transliteration after the corrective pass ("STAREEE" →
        # "СТАРЕЕЕ") — untranslatable SFX; blank it so the art stays instead of
        # Cyrillic gibberish being rendered and narrated.
        if set(reasons) == {"translit"}:
            block["translation"] = ""
            block["keep_empty"] = True
            auto_emptied += 1
            continue
        kept.append((page, block, reasons))
    if auto_emptied:
        print(f"  Auto-cleared {auto_emptied} untranslatable Latin/SFX block(s).")
        jsonfmt.write(translations_path, data)
    remaining = kept

    todo = [{
        "page_num": page["page_num"],
        "id": block["id"],
        "reasons": reasons,
        "original": block.get("original", ""),
        "translation": block.get("translation", ""),
        "page_image": f"pages/{page['page_num']:04d}.png",
    } for page, block, reasons in remaining]

    review_path = work_dir / "review_todo.json"
    review_path.write_text(json.dumps({
        "novel": data.get("novel"),
        "chapter": chapter_num,
        "translations_file": "translations.json",
        "blocks_to_review": todo,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if todo:
        verify_only = sum(1 for t in todo if t["reasons"] == ["verify"])
        fixes = len(todo) - verify_only
        print(f"  Review list: {len(todo)} block(s) -> {review_path}")
        print(f"    {fixes} to fix, {verify_only} 'verify' (image check of recovered captions, e.g. WORD/WORLD)")
        print("  Run Claude on review_todo.json (see REVIEW.md), then step3_render.py")
    else:
        print("  All clear after repair — no Claude review needed. Run step3_render.py")
    return review_path


def main():
    parser = argparse.ArgumentParser(description="Flag + locally repair translations, emit Claude review list.")
    parser.add_argument("--no-repair", action="store_true", help="only flag; don't run the Lapa repair pass")
    args = parser.parse_args()
    cfg = load_config()

    # Collect flagged blocks across ALL chapters, repair them in ONE worker call
    # (single model load per run), then re-flag and write per chapter.
    loaded = []
    all_pending: list[tuple[str, str]] = []
    all_index: dict[str, dict] = {}
    for chapter_num in chapter_numbers(cfg):
        data, path, work_dir, pending, index = collect_chapter(chapter_num, cfg)
        if not args.no_repair:
            for key, text in pending:
                all_pending.append((f"{chapter_num}|{key}", text))
            for key, block in index.items():
                all_index[f"{chapter_num}|{key}"] = block
        loaded.append((chapter_num, data, path, work_dir))

    results = {}
    if all_pending:
        print(f"Repairing {len(all_pending)} flagged block(s) with the corrective prompt...")
        results = translate_texts(all_pending, cfg.get("translation", {}), prompt_template=REPAIR_PROMPT)

    for chapter_num, data, path, work_dir in loaded:
        prefix = f"{chapter_num}|"
        repaired = 0
        for key, block in all_index.items():
            if not key.startswith(prefix):
                continue
            new = (results.get(key) or "").strip()
            if new and new != (block.get("translation") or "").strip():
                block["translation"] = new
                repaired += 1
        finish_chapter(chapter_num, cfg, data, path, work_dir, repaired)


if __name__ == "__main__":
    main()
