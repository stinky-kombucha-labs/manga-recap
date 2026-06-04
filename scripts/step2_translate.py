#!/usr/bin/env python3
"""
Step 2 — Translate translations.json from English to Ukrainian with local Lapa LLM.

Sits between extraction and rendering:

    step1_extract.py   detect bubbles + OCR English   -> translations.json
    step2_translate.py  fill the "translation" fields  <- THIS SCRIPT
    step3_render.py    LaMa inpaint + type UA + TTS    -> 4K MP4

Reads each chapter's translations.json, finds every block that still has an
English `original` but an empty `translation`, sends them to the Lapa GGUF model
(running in its own venv, see config.json -> translation), and writes the
Ukrainian back into the same file. Geometry and any translation you already
edited by hand are preserved.

Re-runnable: by default it only translates empty fields, so you can run it,
hand-fix a few lines, and run step3. Use --overwrite to re-translate everything,
--force to ignore manual edits too.

Run:
    .venv/bin/python scripts/step2_translate.py
    .venv/bin/python scripts/step2_translate.py --overwrite
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from pipeline.chapters import chapter_numbers
from pipeline import jsonfmt
from pipeline.translate import translate_texts

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_config() -> dict:
    with (PROJECT_ROOT / "config.json").open() as f:
        return json.load(f)


def chapter_dir_name(n: int) -> str:
    return f"chapter-{n:05d}"


def _has_letters(text: str) -> bool:
    return any(ch.isalpha() for ch in text or "")


def _needs_translation(block: dict, overwrite: bool) -> bool:
    """A block is translatable if it has real English text in `original`.

    Empty-`original` blocks (e.g. free captions a human typed straight into
    `translation`) are left alone. Without --overwrite, blocks that already have
    a translation are skipped so manual edits survive re-runs.
    """
    original = (block.get("original") or "").strip()
    if not original or not _has_letters(original):
        return False
    if not overwrite and (block.get("translation") or "").strip():
        return False
    return True


def translate_chapter(chapter_num: int, cfg: dict, overwrite: bool = False) -> Path:
    novel = cfg["novel"]
    work_dir = PROJECT_ROOT / "temp" / novel["folder"] / chapter_dir_name(chapter_num)
    translations_path = work_dir / "translations.json"
    if not translations_path.exists():
        raise FileNotFoundError(f"Run step1_extract.py first: {translations_path}")

    data = json.loads(translations_path.read_text())
    pages = data.get("pages", [])

    # Collect every block needing translation. Key = "<page_num>|<block_id>" so
    # results map back unambiguously even across pages.
    pending: list[tuple[str, str]] = []
    index: dict[str, dict] = {}
    for page in pages:
        pnum = page.get("page_num")
        for block in page.get("blocks", []):
            if _needs_translation(block, overwrite):
                key = f"{pnum}|{block.get('id')}"
                pending.append((key, block["original"].strip()))
                index[key] = block

    total_blocks = sum(len(p.get("blocks", [])) for p in pages)
    print(f"Chapter {chapter_num}: {total_blocks} blocks, {len(pending)} to translate"
          f"{' (overwrite)' if overwrite else ''}")
    if not pending:
        print("  Nothing to translate. (Use --overwrite to re-translate.)")
        # Still rewrite once so the file gets the readable format.
        jsonfmt.write(translations_path, data)
        return translations_path

    results = translate_texts(pending, cfg.get("translation", {}))

    filled = 0
    for key, block in index.items():
        translation = (results.get(key) or "").strip()
        if translation:
            block["translation"] = translation
            filled += 1

    jsonfmt.write(translations_path, data)
    print(f"  Filled {filled}/{len(pending)} translations -> {translations_path}")
    print("  Review/edit the 'translation' fields, then run step3_render.py")
    return translations_path


def main():
    parser = argparse.ArgumentParser(description="Translate translations.json with local Lapa LLM.")
    parser.add_argument("--overwrite", action="store_true",
                        help="re-translate blocks that already have a translation")
    args = parser.parse_args()

    cfg = load_config()
    overwrite = args.overwrite or bool(cfg.get("translation", {}).get("overwrite", False))
    for chapter_num in chapter_numbers(cfg):
        translate_chapter(chapter_num, cfg, overwrite=overwrite)


if __name__ == "__main__":
    main()
