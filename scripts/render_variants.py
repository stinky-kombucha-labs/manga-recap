#!/usr/bin/env python3
"""
Render comparison variants — same chapter, several background/text styles.

For every chapter in config.json's run.chapters, this renders all pages once per
variant into a dedicated folder so you can eyeball them side by side and pick the
best look. No TTS / no video — pure image rendering.

    .venv/bin/python scripts/render_variants.py
    .venv/bin/python scripts/render_variants.py --chapters 1
    .venv/bin/python scripts/render_variants.py --variants B_blur,C_blur_underlay
    .venv/bin/python scripts/render_variants.py --pages 1,2,5      # subset of pages

Output (per chapter):
    temp/<novel>/chapter-XXXXX/rendered_variant_<name>/0001.png ...

Each variant is config.json's "render" block with the overrides below merged on top.
The text auto-fit (width+height incl. stroke) is identical across variants — only the
background treatment and stroke/underlay styling differ, which is what you compare.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from pipeline.chapters import chapter_numbers
from pipeline.render import render_page

PROJECT_ROOT = Path(__file__).resolve().parents[1]


# --- Variant definitions ----------------------------------------------------
# Each entry merges over config.json["render"]. Keys here override the base.
# All variants use the new glyph-match fit (Ukrainian sized to the English glyph
# height) + full-extent detection (OCR-missed continuation lines are erased too),
# which are render.py defaults. Variants differ in background + text styling.
_GLYPH = {           # shared glyph-match defaults
    "fit_mode": "glyph_match",
    "detect_extent": True,
    "glyph_match_ratio": 1.0,
    "stroke_ratio": 0.10,
    "font_min": 12,
}
VARIANTS: dict[str, dict] = {
    # A: neural inpaint (cleanest erase), glyph-matched text.
    "A_lama": {**_GLYPH, "bg_mode": "lama"},
    # B: blur the original text instead of LaMa. Always hides English, faster.
    "B_blur": {**_GLYPH, "bg_mode": "blur", "blur_strength": 0.6, "blur_feather": 10},
    # C: blur + translucent white underlay behind the text — most subtitle-like.
    "C_blur_underlay": {
        **_GLYPH, "bg_mode": "blur_box", "blur_strength": 0.55, "blur_feather": 8,
        "underlay": True, "underlay_alpha": 0.6, "underlay_color": [255, 255, 255],
        "underlay_radius": 22, "underlay_pad": 16,
    },
    # D: LaMa but Ukrainian 10% smaller than English (safest "not bigger").
    "D_lama_90pct": {**_GLYPH, "bg_mode": "lama", "glyph_match_ratio": 0.90},
}


def load_config() -> dict:
    with (PROJECT_ROOT / "config.json").open() as f:
        return json.load(f)


def chapter_dir_name(n: int) -> str:
    return f"chapter-{n:05d}"


def _resolve_src(pages_dir: Path, idx: int) -> Path:
    src = pages_dir / f"{idx:04d}.png"
    if src.exists():
        return src
    candidates = list(pages_dir.glob(f"{idx:04d}.*"))
    return candidates[0] if candidates else src


def render_variant(chapter_num: int, variant_name: str, overrides: dict,
                   base_render_cfg: dict, novel_slug: str,
                   page_filter: set[int] | None) -> Path:
    work_dir = PROJECT_ROOT / "temp" / novel_slug / chapter_dir_name(chapter_num)
    pages_dir = work_dir / "pages"
    out_dir = work_dir / f"rendered_variant_{variant_name}"
    out_dir.mkdir(parents=True, exist_ok=True)

    translations_path = work_dir / "translations.json"
    if not translations_path.exists():
        raise FileNotFoundError(f"Run step1_extract.py first: {translations_path}")

    render_cfg = {**base_render_cfg, **overrides}
    data = json.loads(translations_path.read_text())
    pages = data["pages"]

    print(f"\n=== Chapter {chapter_num} | variant {variant_name} "
          f"(bg_mode={render_cfg.get('bg_mode')}) -> {out_dir.name}")
    for page in pages:
        idx = page["page_num"]
        if page_filter and idx not in page_filter:
            continue
        blocks = [b for b in page["blocks"] if b.get("translation", "").strip()]
        src_page = _resolve_src(pages_dir, idx)
        rendered = out_dir / f"{idx:04d}.png"
        print(f"  [{idx}/{len(pages)}] {page.get('source_file', '')}")
        render_page(src_page, blocks, rendered, render_cfg)
    return out_dir


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--variants", help="comma list of variant names (default: all)")
    parser.add_argument("--chapters", help="override config run.chapters, e.g. '1' or '1-3'")
    parser.add_argument("--pages", help="comma list of page numbers to render (default: all)")
    args = parser.parse_args()

    cfg = load_config()
    if args.chapters:
        cfg.setdefault("run", {})["chapters"] = args.chapters
    base_render_cfg = cfg.get("render", {})
    novel_slug = cfg["novel"]["folder"]

    names = (args.variants.split(",") if args.variants else list(VARIANTS))
    names = [n.strip() for n in names if n.strip()]
    unknown = [n for n in names if n not in VARIANTS]
    if unknown:
        sys.exit(f"Unknown variant(s): {unknown}. Available: {list(VARIANTS)}")

    page_filter = None
    if args.pages:
        page_filter = {int(p) for p in args.pages.split(",") if p.strip()}

    produced = []
    for chapter_num in chapter_numbers(cfg):
        for name in names:
            out = render_variant(chapter_num, name, VARIANTS[name],
                                  base_render_cfg, novel_slug, page_filter)
            produced.append(out)

    print("\nDone. Compare these folders:")
    for p in produced:
        print(f"  {p}")


if __name__ == "__main__":
    main()
