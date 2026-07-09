#!/usr/bin/env python3
"""
Combiner — зшиває готові chapter-XXXXX.mp4 у великі збірники по N розділів
(наприклад, 1-50, 51-100, ...) БЕЗ перекодування (ffmpeg concat, -c copy).

Повністю автономний: тільки стандартна бібліотека Python + ffmpeg у PATH.
Нічого не імпортує з пайплайна.

Налаштування читаються з config.json у корені проєкту:
    "combine": { "chapters_per_video": 50 }
а також novel.folder і run.output_dir (де лежать chapter-XXXXX.mp4).

Запуск:
    python3 combiner/combine.py            # зшити всі повні групи
    python3 combiner/combine.py --dry-run  # тільки показати, що буде зроблено
    python3 combiner/combine.py --partial  # зшити й неповний хвіст (останню групу)

Результат: video_output/<novel>/combined/chapters-00001-00050.mp4 і т.д.
Уже зшитий файл пропускається, якщо він новіший за всі свої розділи.
Запис атомарний (.tmp -> rename), обрив у будь-який момент не лишає битих файлів.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_config() -> dict:
    with (PROJECT_ROOT / "config.json").open(encoding="utf-8") as f:
        return json.load(f)


def find_chapters(src_dir: Path) -> dict[int, Path]:
    """{номер розділу: шлях до mp4} для всіх наявних chapter-XXXXX.mp4."""
    chapters: dict[int, Path] = {}
    for f in src_dir.glob("chapter-*.mp4"):
        stem = f.stem  # chapter-00051
        num_part = stem.replace("chapter-", "")
        if num_part.isdigit():
            chapters[int(num_part)] = f
    return chapters


def combine_group(files: list[Path], out_path: Path, dry_run: bool) -> bool:
    """Зшити список mp4 в один (без перекодування). True = зроблено/актуально."""
    # пропуск, якщо збірник уже існує і новіший за всі вхідні файли
    if out_path.exists() and all(out_path.stat().st_mtime_ns > f.stat().st_mtime_ns
                                 for f in files):
        print(f"  SKIP {out_path.name} (актуальний)")
        return True
    if dry_run:
        total_mb = sum(f.stat().st_size for f in files) / 1e6
        print(f"  BUILD {out_path.name}  ({len(files)} розділів, ~{total_mb:.0f} MB)")
        return True

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        concat_list = Path(tmp) / "concat.txt"
        concat_list.write_text(
            "".join(f"file '{f.resolve()}'\n" for f in files), encoding="utf-8")
        partial = out_path.with_suffix(".mp4.tmp")
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-y",
             "-f", "concat", "-safe", "0",
             "-i", str(concat_list),
             "-c", "copy",
             "-movflags", "+faststart",
             "-f", "mp4",
             str(partial)],
            capture_output=True, text=True)
        if r.returncode != 0:
            partial.unlink(missing_ok=True)
            print(f"  FAIL {out_path.name}:\n{r.stderr[-800:]}", file=sys.stderr)
            return False
        partial.replace(out_path)
    print(f"  OK   {out_path.name}  ({out_path.stat().st_size / 1e6:.0f} MB)")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Зшити розділи у збірники по N.")
    parser.add_argument("--dry-run", action="store_true",
                        help="показати план, нічого не робити")
    parser.add_argument("--partial", action="store_true",
                        help="зшивати й неповну останню групу")
    args = parser.parse_args()

    cfg = load_config()
    per = int(cfg.get("combine", {}).get("chapters_per_video", 50))
    src_dir = PROJECT_ROOT / cfg["run"]["output_dir"] / cfg["novel"]["folder"]
    out_dir = src_dir / "combined"

    chapters = find_chapters(src_dir)
    if not chapters:
        raise SystemExit(f"Не знайдено chapter-*.mp4 у {src_dir}")
    last = max(chapters)
    print(f"Знайдено {len(chapters)} розділів (до {last}), група = {per} розділів")

    ok = True
    start = 1
    while start <= last:
        end = start + per - 1
        group_nums = list(range(start, end + 1))
        missing = [n for n in group_nums if n not in chapters]
        present = [chapters[n] for n in group_nums if n in chapters]

        if not present:
            start = end + 1
            continue
        if missing and not (args.partial and end > last):
            # у групі дірки (або хвіст без --partial) — не зшиваємо мовчки
            if end <= last:
                print(f"  SKIP chapters {start}-{end}: відсутні розділи {missing[:8]}"
                      f"{'...' if len(missing) > 8 else ''}")
            else:
                print(f"  SKIP хвіст {start}-{max(n for n in group_nums if n in chapters)}"
                      f" (неповна група; додай --partial, щоб зшити)")
            start = end + 1
            continue

        real_end = max(n for n in group_nums if n in chapters)
        out_path = out_dir / f"chapters-{start:05d}-{real_end:05d}.mp4"
        ok &= combine_group(present, out_path, args.dry_run)
        start = end + 1

    if not ok:
        raise SystemExit(2)
    print("Готово." if not args.dry_run else "План показано (--dry-run).")


if __name__ == "__main__":
    main()
