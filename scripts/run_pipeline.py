#!/usr/bin/env python3
"""
Run the WHOLE pipeline for every chapter in config.json -> run.chapters with one
command — detection, translation, repair, render, TTS, encode, and the
leftover-English QA loop:

    step1_extract -> step2_translate -> step2b_repair
        -> step3_render -> step3b_verify --fix
        -> (if junk leaks were queued) step3_render -> step3b_verify

Set run.chapters (e.g. "4-103") and run:
    .venv/bin/python scripts/run_pipeline.py
    .venv/bin/python scripts/run_pipeline.py --skip-tts

Fully automatic. The only OPTIONAL manual step is the translation quality
review: each chapter's review_todo.json lists the few blocks the deterministic
flagger still doubts after the local repair pass (typically OCR-mangled
captions). The batch does NOT stop for it — run Claude on those files per
REVIEW.md afterwards, then re-run step3_render.py (only edited pages re-render
and re-voice thanks to the caches).

A failing step aborts the batch (so a broken model path can't silently produce
959 untranslated chapters).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from pipeline.chapters import chapter_numbers

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"
SCRIPTS = Path(__file__).resolve().parent


def run_step(script: str, *args: str, ok_codes: tuple[int, ...] = (0,)) -> int:
    cmd = [str(PYTHON), str(SCRIPTS / script), *args]
    print(f"\n{'=' * 70}\n=== {script} {' '.join(args)}\n{'=' * 70}", flush=True)
    code = subprocess.run(cmd).returncode
    if code not in ok_codes:
        raise SystemExit(f"\n{script} failed (exit {code}) — batch aborted.")
    return code


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the full per-chapter pipeline end to end.")
    parser.add_argument("--skip-tts", action="store_true", help="render silent videos (no narration)")
    args = parser.parse_args()
    render_args = ["--skip-tts"] if args.skip_tts else []

    cfg = json.loads((PROJECT_ROOT / "config.json").read_text())
    chapters = chapter_numbers(cfg)
    print(f"Pipeline for {len(chapters)} chapter(s): {chapters[0]}..{chapters[-1]}")

    run_step("step1_extract.py")
    run_step("step2_translate.py")
    run_step("step2b_repair.py")
    run_step("step3_render.py", *render_args)

    # QA loop: junk leaks (exit 3) get queued as noise blocks -> re-render just
    # those pages -> verify again. Real-text leaks (exit 2) need eyes — report.
    code = run_step("step3b_verify.py", "--fix", ok_codes=(0, 2, 3))
    if code == 3:
        run_step("step3_render.py", *render_args)
        code = run_step("step3b_verify.py", ok_codes=(0, 2))

    # --- Batch summary ---
    novel_dir = PROJECT_ROOT / "temp" / cfg["novel"]["folder"]
    out_dir = PROJECT_ROOT / cfg["run"]["output_dir"] / cfg["novel"]["folder"]
    review_total = 0
    print(f"\n{'=' * 70}\n=== Batch summary\n{'=' * 70}")
    for ch in chapters:
        ch_dir = novel_dir / f"chapter-{ch:05d}"
        todo = []
        review_path = ch_dir / "review_todo.json"
        if review_path.exists():
            todo = json.loads(review_path.read_text()).get("blocks_to_review", [])
        leaks = 0
        qa_path = ch_dir / "render_qa.json"
        if qa_path.exists():
            leaks = json.loads(qa_path.read_text()).get("leaks", 0)
        mp4 = out_dir / f"chapter-{ch:05d}.mp4"
        size = f"{mp4.stat().st_size / 1024 / 1024:.0f} MB" if mp4.exists() else "MISSING"
        review_total += len(todo)
        flags = []
        if todo:
            flags.append(f"{len(todo)} block(s) to review")
        if leaks:
            flags.append(f"{leaks} ENGLISH LEAK(S)")
        print(f"  chapter {ch:>4}: {size:>8}  {'; '.join(flags) if flags else 'clean'}")

    if review_total:
        print(f"\nOptional quality pass: {review_total} flagged block(s) across the batch —")
        print("run Claude on each chapter's review_todo.json (see REVIEW.md), then re-run step3_render.py.")
    if code == 2:
        print("\nSome pages still show English — check render_qa.json files above.")
        raise SystemExit(2)
    print("\nDone.")


if __name__ == "__main__":
    main()
