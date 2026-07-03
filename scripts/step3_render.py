#!/usr/bin/env python3
"""
Step 3 — Read translations.json, render pages (LaMa + stroke text), TTS, encode 4K YouTube MP4.

Run after step2_translate.py has filled (and you have reviewed) the Ukrainian
translations:
    .venv/bin/python scripts/step3_render.py
    .venv/bin/python scripts/step3_render.py --skip-tts
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import wave
import struct
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from pipeline.chapters import chapter_numbers
from pipeline.render import render_page
from pipeline.tts import generate_chapter_audio

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 4K landscape: 3840×2160 (16:9). Manga pages (portrait ≈2912×4120) are scaled to fit
# height 2160, then pillarboxed with black bars left/right to reach 3840 width.
TARGET_W = 3840
TARGET_H = 2160


def load_config() -> dict:
    with (PROJECT_ROOT / "config.json").open() as f:
        return json.load(f)


def chapter_dir_name(n: int) -> str:
    return f"chapter-{n:05d}"


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text()) if path.exists() else default
    except Exception:
        return default


def _write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))


def _page_render_signature(page: dict, src_page: Path, render_cfg: dict) -> str:
    try:
        stat = src_page.stat()
        src_meta = {
            "path": src_page.name,
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }
    except OSError:
        src_meta = {"path": src_page.name, "missing": True}

    blocks = []
    for block in page.get("blocks", []):
        blocks.append({
            "id": block.get("id"),
            "bbox": block.get("bbox"),
            "line_bboxes": block.get("line_bboxes"),
            "original": block.get("original", ""),
            "translation": block.get("translation", ""),
            "detector": block.get("detector", ""),
            "noise": bool(block.get("noise")),
        })

    payload = {
        "source_file": page.get("source_file", ""),
        "source": src_meta,
        "blocks": blocks,
        "render": render_cfg,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _wav_duration(p: Path) -> float:
    try:
        with wave.open(str(p), "rb") as f:
            return f.getnframes() / f.getframerate()
    except Exception:
        return 0.0


def _silence_wav(p: Path, duration: float = 1.0, rate: int = 22050) -> None:
    n = int(duration * rate)
    with wave.open(str(p), "w") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(struct.pack(f"<{n}h", *([0] * n)))


def render_chapter(chapter_num: int, cfg: dict, skip_tts: bool = False, force_render: bool = False) -> Path:
    novel = cfg["novel"]
    render_cfg = cfg.get("render", {})
    tts_cfg = cfg.get("tts", {})
    video_cfg = cfg.get("video", {})

    novel_slug = novel["folder"]
    work_dir = PROJECT_ROOT / "temp" / novel_slug / chapter_dir_name(chapter_num)
    pages_dir = work_dir / "pages"
    rendered_dir = work_dir / "rendered"
    audio_dir = work_dir / "audio"
    out_dir = PROJECT_ROOT / cfg["run"]["output_dir"] / novel_slug

    for d in (rendered_dir, audio_dir, out_dir):
        d.mkdir(parents=True, exist_ok=True)

    translations_path = work_dir / "translations.json"
    if not translations_path.exists():
        raise FileNotFoundError(f"Run step1_extract.py first: {translations_path}")

    data = json.loads(translations_path.read_text())
    pages = data["pages"]
    render_cache_path = rendered_dir / ".render_cache.json"
    render_cache = _load_json(render_cache_path, {})

    page_duration_min = video_cfg.get("page_duration_min", 5)
    tts_python = tts_cfg.get("tts_python", "/home/user/PycharmProjects/tts/.venv/bin/python3")
    tts_enabled = tts_cfg.get("enabled", True) and not skip_tts

    print(f"Chapter {chapter_num}: {len(pages)} pages")

    # --- Render all pages ---
    page_entries = []
    for page in pages:
        idx = page["page_num"]
        blocks = page["blocks"]

        src_page = pages_dir / f"{idx:04d}.png"
        if not src_page.exists():
            candidates = list(pages_dir.glob(f"{idx:04d}.*"))
            src_page = candidates[0] if candidates else pages_dir / f"{idx:04d}.png"

        rendered = rendered_dir / f"{idx:04d}.png"
        print(f"  [{idx}/{len(pages)}] {page['source_file']}")

        # Blocks with a translation get cleaned + typeset; noise blocks (scanlation
        # credits/URLs/watermarks) get cleaned only — the box is inpainted and left
        # empty, so the junk disappears from the video.
        render_blocks = [b for b in blocks
                         if b.get("translation", "").strip() or b.get("noise")]
        signature = _page_render_signature(page, src_page, render_cfg)
        cache_key = str(idx)
        cache_hit = rendered.exists() and render_cache.get(cache_key) == signature
        if force_render or not cache_hit:
            print(f"    Rendering...")
            render_page(src_page, render_blocks, rendered, render_cfg)
            render_cache[cache_key] = signature
            _write_json(render_cache_path, render_cache)
        else:
            print(f"    Render cached.")

        page_entries.append({
            "image": rendered,
            "audio": audio_dir / f"{idx:04d}.wav",
            "idx": idx,
        })

    # --- Batch TTS (one subprocess for all pages — model loads once) ---
    if tts_enabled:
        # Cache narration by text hash so audio is regenerated when a translation
        # changes (file-exists alone left stale narration after edits / re-translate).
        audio_cache_path = audio_dir / ".audio_cache.json"
        audio_cache = _load_json(audio_cache_path, {})

        page_texts = {}
        for page in pages:
            idx = page["page_num"]
            audio_path = audio_dir / f"{idx:04d}.wav"
            narration = " ".join(
                b["translation"].strip() for b in page["blocks"]
                if b.get("translation", "").strip()
            )
            cache_key = str(idx)
            sig = hashlib.sha256(narration.encode("utf-8")).hexdigest() if narration else ""

            if not narration:
                # Translation cleared — drop any stale audio so it won't be reused.
                if audio_path.exists():
                    audio_path.unlink()
                audio_cache.pop(cache_key, None)
                continue
            if audio_path.exists() and audio_cache.get(cache_key) == sig:
                continue
            page_texts[idx] = narration
            audio_cache[cache_key] = sig

        _write_json(audio_cache_path, audio_cache)

        if page_texts:
            # Free GPU memory held by LaMa before loading TTS model
            import torch, gc
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            print(f"\n  TTS: {len(page_texts)} pages via Oleksa (batch)...")
            generate_chapter_audio(page_texts, audio_dir, tts_python)

    # Attach actual audio paths
    for entry in page_entries:
        audio_path = entry.pop("audio")
        entry["audio"] = audio_path if (tts_enabled and audio_path.exists()) else None

    # Encode 4K YouTube MP4. Skip when the MP4 is newer than every rendered page
    # and audio file — a second pipeline pass (e.g. after step3b --fix touched a
    # few chapters) must not re-encode untouched chapters. (Config changes to
    # crf/page_duration alone won't trigger a re-encode; use --force-render.)
    out_path = out_dir / f"{chapter_dir_name(chapter_num)}.mp4"
    inputs = [e["image"] for e in page_entries] + [e["audio"] for e in page_entries if e["audio"]]
    if (not force_render and out_path.exists()
            and all(p.exists() for p in inputs)
            and all(out_path.stat().st_mtime_ns > p.stat().st_mtime_ns for p in inputs)):
        print(f"\n  Encode cached: {out_path}")
        return out_path
    print(f"\n  Encoding 4K MP4...")
    _encode_4k(page_entries, out_path, page_duration_min, video_cfg.get("crf", 18))
    print(f"  -> {out_path}")
    print(f"  Size: {out_path.stat().st_size / 1024 / 1024:.1f} MB")
    return out_path


def _encode_4k(page_entries: list[dict], out_path: Path, page_min: float, crf: int) -> None:
    # Scale page to fit TARGET_H (2160), keep aspect ratio, pad width to TARGET_W (3840)
    vf = (
        f"scale=-2:{TARGET_H},"
        f"pad={TARGET_W}:{TARGET_H}:(ow-iw)/2:(oh-ih)/2:black"
    )

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        segs = []

        for i, entry in enumerate(page_entries):
            img = entry["image"]
            audio = entry.get("audio")

            if audio and audio.exists():
                dur = max(page_min, _wav_duration(audio))
            else:
                dur = page_min
                audio = tmp_dir / f"silence_{i:04d}.wav"
                _silence_wav(audio, dur)

            seg = tmp_dir / f"seg_{i:04d}.mp4"
            cmd = [
                "ffmpeg", "-hide_banner", "-y",
                "-loop", "1", "-framerate", "1",
                "-i", str(img),
                "-i", str(audio),
                "-map", "0:v:0", "-map", "1:a:0",
                "-t", f"{dur:.3f}",
                "-vf", vf,
                "-c:v", "libx264", "-preset", "fast", "-crf", str(crf),
                "-profile:v", "high", "-level", "5.2",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "384k",
                "-shortest",
                str(seg),
            ]
            subprocess.run(cmd, check=True, capture_output=True)
            segs.append(seg)

        concat = tmp_dir / "concat.txt"
        concat.write_text("".join(f"file '{s}'\n" for s in segs))
        subprocess.run([
            "ffmpeg", "-hide_banner", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(concat),
            "-c", "copy",
            str(out_path),
        ], check=True, capture_output=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-tts", action="store_true")
    parser.add_argument("--force-render", action="store_true")
    args = parser.parse_args()
    cfg = load_config()
    for chapter_num in chapter_numbers(cfg):
        render_chapter(chapter_num, cfg, skip_tts=args.skip_tts, force_render=args.force_render)


if __name__ == "__main__":
    main()
