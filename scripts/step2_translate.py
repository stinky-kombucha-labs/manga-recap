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
    `translation`) are left alone. Noise blocks (credits/URLs/watermarks) are
    only inpainted, never translated. Without --overwrite, blocks that already
    have a translation are skipped so manual edits survive re-runs.
    """
    if block.get("noise"):
        return False
    if block.get("keep_empty") and not overwrite:
        return False   # step2b deliberately blanked this SFX — don't refill it
    original = (block.get("original") or "").strip()
    if not original or not _has_letters(original):
        return False
    if not overwrite and (block.get("translation") or "").strip():
        return False
    return True


def _normalized(text: str) -> str:
    return "".join(ch.lower() for ch in text or "" if ch.isalnum())


def _glossary_lookup(original: str, glossary: dict[str, str]) -> str | None:
    """Fixed translation for recurring stylized text (series title on every cover).

    OCR reads those unreliably ("TALES OF..." → "W ES OF..."), so match fuzzily on
    normalized text instead of exactly.
    """
    from difflib import SequenceMatcher
    norm = _normalized(original)
    if len(norm) < 4:
        return None
    for key, value in (glossary or {}).items():
        if SequenceMatcher(None, norm, _normalized(key)).ratio() >= 0.72:
            return value
    return None


def collect_chapter(chapter_num: int, cfg: dict, overwrite: bool = False):
    """Load one chapter, apply the glossary in place, and collect blocks that
    need LLM translation. Keys are "<page_num>|<block_id>" (the caller prefixes
    the chapter). Returns (data, translations_path, pending, index)."""
    novel = cfg["novel"]
    work_dir = PROJECT_ROOT / "temp" / novel["folder"] / chapter_dir_name(chapter_num)
    translations_path = work_dir / "translations.json"
    if not translations_path.exists():
        raise FileNotFoundError(f"Run step1_extract.py first: {translations_path}")

    data = json.loads(translations_path.read_text())
    pages = data.get("pages", [])

    glossary = cfg.get("translation", {}).get("glossary", {})
    pending: list[tuple[str, str]] = []
    index: dict[str, dict] = {}
    glossary_hits = 0
    for page in pages:
        pnum = page.get("page_num")
        for block in page.get("blocks", []):
            if block.get("noise"):
                continue
            # Glossary is canonical: it overrides whatever is in the field (the
            # whole point is pinning recurring stylized text the OCR garbles).
            if (block.get("original") or "").strip():
                fixed = _glossary_lookup(block["original"], glossary)
                if fixed is not None:
                    if block.get("translation") != fixed:
                        block["translation"] = fixed
                        glossary_hits += 1
                    continue
            if not _needs_translation(block, overwrite):
                continue
            key = f"{pnum}|{block.get('id')}"
            pending.append((key, block["original"].strip()))
            index[key] = block

    total_blocks = sum(len(p.get("blocks", [])) for p in pages)
    print(f"Chapter {chapter_num}: {total_blocks} blocks, {len(pending)} to translate"
          f"{' (overwrite)' if overwrite else ''}")
    if glossary_hits:
        print(f"  {glossary_hits} block(s) filled from translation.glossary")
    return data, translations_path, pending, index


def main():
    parser = argparse.ArgumentParser(description="Translate translations.json with the local LLM.")
    parser.add_argument("--overwrite", action="store_true",
                        help="re-translate blocks that already have a translation")
    args = parser.parse_args()

    cfg = load_config()
    overwrite = args.overwrite or bool(cfg.get("translation", {}).get("overwrite", False))

    # Collect across ALL chapters first, then translate in ONE worker call — the
    # GGUF model loads once per run instead of once per chapter (matters at
    # 100-chapter batches: each load is ~half a minute).
    loaded = []
    all_pending: list[tuple[str, str]] = []
    all_index: dict[str, dict] = {}
    for chapter_num in chapter_numbers(cfg):
        data, path, pending, index = collect_chapter(chapter_num, cfg, overwrite=overwrite)
        for key, text in pending:
            all_pending.append((f"{chapter_num}|{key}", text))
        for key, block in index.items():
            all_index[f"{chapter_num}|{key}"] = block
        loaded.append((chapter_num, data, path, len(pending)))

    results = translate_texts(all_pending, cfg.get("translation", {})) if all_pending else {}

    for chapter_num, data, path, n_pending in loaded:
        prefix = f"{chapter_num}|"
        filled = 0
        for key, block in all_index.items():
            if not key.startswith(prefix):
                continue
            translation = (results.get(key) or "").strip()
            if translation:
                block["translation"] = translation
                filled += 1
        # Always rewrite so the file gets the readable format (and glossary hits).
        jsonfmt.write(path, data)
        if n_pending:
            print(f"Chapter {chapter_num}: filled {filled}/{n_pending} -> {path}")
    print("Review/edit the 'translation' fields, then run step2b_repair.py / step3_render.py")


if __name__ == "__main__":
    main()
