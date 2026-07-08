#!/usr/bin/env python3
"""
Helper stages for run_night.sh. All subcommands operate on the chapters from
config.json -> run.chapters.

    night_tools.py backup       copy every translations.json to backups/
    night_tools.py tm-apply     fill review_todo blocks from the translation
                                memory (recurring phrases decided once by a past
                                review get applied by script, shrinking the AI's
                                work); --dry-run to only count
    night_tools.py tm-harvest   learn new decisions into the translation memory
                                by diffing review_todo snapshots vs the current
                                translations (run AFTER a review)
    night_tools.py summary      per-chapter table: MP4 size, blocks left to
                                review, leftover-English leaks

Translation memory lives in .claude/skills/review-batch/tm.json (git-tracked,
grows with every reviewed batch). Only two kinds of decisions are stored:
  - a replacement translation for a recurring original;
  - "blank" (untranslatable SFX -> translation="" + keep_empty).
Noise decisions are NOT stored: whether something is scanlation junk depends on
its position on the page, not only on its text.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from pipeline.chapters import chapter_numbers
from pipeline import jsonfmt

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TM_PATH = PROJECT_ROOT / ".claude" / "skills" / "review-batch" / "tm.json"
MIN_NORM_LEN = 4          # don't memorize ultra-short originals ("HA", "OK")
MAX_APPLY_LEN = 220       # don't auto-apply page-long texts, let the AI see them


def load_config() -> dict:
    with (PROJECT_ROOT / "config.json").open() as f:
        return json.load(f)


def chapter_dirs(cfg: dict):
    novel = cfg["novel"]["folder"]
    for ch in chapter_numbers(cfg):
        d = PROJECT_ROOT / "temp" / novel / f"chapter-{ch:05d}"
        if (d / "translations.json").exists():
            yield ch, d


def norm(text: str) -> str:
    return "".join(ch.lower() for ch in (text or "") if ch.isalnum())


def load_tm() -> dict:
    if TM_PATH.exists():
        return json.loads(TM_PATH.read_text(encoding="utf-8"))
    return {}


def save_tm(tm: dict) -> None:
    tmp = TM_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(tm, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                   encoding="utf-8")
    tmp.replace(TM_PATH)


# ---------------------------------------------------------------------------

def cmd_backup(cfg: dict) -> None:
    stamp = _dt.date.today().strftime("%Y%m%d")
    out = PROJECT_ROOT / "backups"
    out.mkdir(exist_ok=True)
    n = 0
    for ch, d in chapter_dirs(cfg):
        shutil.copy2(d / "translations.json", out / f"translations-ch{ch:05d}-{stamp}.json")
        n += 1
    print(f"[backup] {n} translations.json -> backups/ ({stamp})")


def cmd_tm_apply(cfg: dict, dry_run: bool = False) -> None:
    tm = load_tm()
    if not tm:
        print("[tm-apply] translation memory is empty — nothing to apply")
        return
    applied = blanked = left = 0
    for ch, d in chapter_dirs(cfg):
        todo_path = d / "review_todo.json"
        if not todo_path.exists():
            continue
        todo = json.loads(todo_path.read_text(encoding="utf-8"))
        blocks_left = []
        data = None
        for t in todo.get("blocks_to_review", []):
            key = norm(t.get("original", ""))
            hit = tm.get(key)
            if (not hit or len(key) < MIN_NORM_LEN
                    or len(hit.get("t", "")) > MAX_APPLY_LEN):
                blocks_left.append(t)
                continue
            if data is None:
                data = json.loads((d / "translations.json").read_text(encoding="utf-8"))
            block = next((b for p in data["pages"] if p["page_num"] == t["page_num"]
                          for b in p["blocks"] if b["id"] == t["id"]), None)
            if block is None or block.get("noise"):
                blocks_left.append(t)
                continue
            if dry_run:
                applied += 1
                blocks_left.append(t)
                continue
            if hit.get("blank"):
                block["translation"] = ""
                block["keep_empty"] = True
                blanked += 1
            else:
                block["translation"] = hit["t"]
                applied += 1
        left += len(blocks_left)
        if not dry_run and data is not None:
            jsonfmt.write(d / "translations.json", data)
            todo["blocks_to_review"] = blocks_left
            todo_path.write_text(json.dumps(todo, ensure_ascii=False, indent=2) + "\n",
                                 encoding="utf-8")
    mode = " (dry-run)" if dry_run else ""
    print(f"[tm-apply]{mode} translated from memory: {applied}, blanked SFX: {blanked}, "
          f"left for AI review: {left}")


def cmd_tm_harvest(cfg: dict) -> None:
    tm = load_tm()
    added = updated = 0
    for ch, d in chapter_dirs(cfg):
        todo_path = d / "review_todo.json"
        if not todo_path.exists():
            continue
        todo = json.loads(todo_path.read_text(encoding="utf-8"))
        data = json.loads((d / "translations.json").read_text(encoding="utf-8"))
        idx = {(p["page_num"], b["id"]): b for p in data["pages"] for b in p["blocks"]}
        for t in todo.get("blocks_to_review", []):
            key = norm(t.get("original", ""))
            if len(key) < MIN_NORM_LEN:
                continue
            block = idx.get((t["page_num"], t["id"]))
            if block is None or block.get("noise"):
                continue          # noise is position-dependent — never memorize
            cur = (block.get("translation") or "").strip()
            old = (t.get("translation") or "").strip()
            if cur == old:
                continue          # reviewer changed nothing — nothing to learn
            entry = {"blank": True} if (not cur and block.get("keep_empty")) else \
                    ({"t": cur} if cur else None)
            if entry is None:
                continue
            entry["src"] = (t.get("original") or "")[:80]
            if key in tm:
                if tm[key] != entry:
                    tm[key] = entry   # newest review wins
                    updated += 1
            else:
                tm[key] = entry
                added += 1
    save_tm(tm)
    print(f"[tm-harvest] memory: +{added} new, {updated} updated, total {len(load_tm())} entries")


def cmd_summary(cfg: dict) -> int:
    out_dir = PROJECT_ROOT / cfg["run"]["output_dir"] / cfg["novel"]["folder"]
    total_todo = total_leaks = 0
    print(f"{'chapter':>8} | {'mp4':>8} | {'to review':>9} | leaks")
    for ch, d in chapter_dirs(cfg):
        todo, leaks = 0, "?"
        tp, qp = d / "review_todo.json", d / "render_qa.json"
        if tp.exists():
            todo = len(json.loads(tp.read_text()).get("blocks_to_review", []))
        if qp.exists():
            leaks = json.loads(qp.read_text()).get("leaks", 0)
        mp4 = out_dir / f"chapter-{ch:05d}.mp4"
        size = f"{mp4.stat().st_size/1e6:.0f} MB" if mp4.exists() else "MISSING"
        total_todo += todo
        total_leaks += leaks if isinstance(leaks, int) else 0
        print(f"{ch:>8} | {size:>8} | {todo:>9} | {leaks}")
    print(f"\nTOTAL: {total_todo} block(s) still flagged, {total_leaks} leftover-English leak(s)")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["backup", "tm-apply", "tm-harvest", "summary"])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    cfg = load_config()
    if args.command == "backup":
        cmd_backup(cfg)
    elif args.command == "tm-apply":
        cmd_tm_apply(cfg, dry_run=args.dry_run)
    elif args.command == "tm-harvest":
        cmd_tm_harvest(cfg)
    elif args.command == "summary":
        cmd_summary(cfg)


if __name__ == "__main__":
    main()
