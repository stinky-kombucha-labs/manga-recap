"""
Chapter selection from config.json.

The pipeline intentionally uses config.json as the single source of truth for
chapter selection. Supported forms: "1", "1-5", "1,3,7-9", "all".
"""

from __future__ import annotations


def chapter_numbers(cfg: dict) -> list[int]:
    run_cfg = cfg.get("run", {})
    novel_cfg = cfg.get("novel", {})
    spec = str(run_cfg.get("chapters", "")).strip().lower()
    total = int(novel_cfg.get("total_chapters") or 0)

    if not spec:
        raise ValueError("config.json: run.chapters is empty")

    if spec == "all":
        if total <= 0:
            raise ValueError("config.json: novel.total_chapters is required for run.chapters='all'")
        return list(range(1, total + 1))

    chapters: list[int] = []
    seen: set[int] = set()
    for raw_part in spec.split(","):
        part = raw_part.strip()
        if not part:
            continue

        if "-" in part:
            left, right = [p.strip() for p in part.split("-", 1)]
            start, end = int(left), int(right)
            if end < start:
                raise ValueError(f"config.json: invalid chapter range '{part}'")
            values = range(start, end + 1)
        else:
            values = (int(part),)

        for chapter in values:
            if chapter < 1:
                raise ValueError(f"config.json: chapter must be >= 1, got {chapter}")
            if total and chapter > total:
                raise ValueError(f"config.json: chapter {chapter} exceeds total_chapters={total}")
            if chapter not in seen:
                seen.add(chapter)
                chapters.append(chapter)

    if not chapters:
        raise ValueError("config.json: run.chapters did not contain any chapters")
    return chapters
