#!/usr/bin/env python3
"""
EXPERIMENT: panel-by-panel recap video variants (phone-friendly reading).

Instead of one static full page per shot, show FRAGMENTS of the rendered page
large in the frame, in reading order, each for the duration of its narration —
the recap-channel style. No motion/zoom effects: static cuts only.

Variants (both 3840x2160, NVENC, same audio chain as step3):
  A "panels" — detect PANELS on the rendered page (white-gutter segmentation),
               show each panel; its narration = the text blocks inside it.
               Page 1 (cover) is always shown whole.
  B "blocks" — no panel detection: for each text block show a 16:9 context
               window centred on the block (bubble large in frame).

Usage:
    .venv/bin/python scripts/panel_experiments.py            # both variants, ch from config
    .venv/bin/python scripts/panel_experiments.py --variant A
    .venv/bin/python scripts/panel_experiments.py --preview  # only panel-debug images

Output: video_output/<novel>/chapter-XXXXX-panelsA.mp4 / -blocksB.mp4
        temp/<novel>/<chapter>/panel_debug/*.png (detected panel rectangles)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).parent))
from pipeline.chapters import chapter_numbers
from pipeline.tts import generate_chapter_audio
from step1_extract import _sort_blocks

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TARGET_W, TARGET_H = 3840, 2160
ASPECT = TARGET_W / TARGET_H


def load_config() -> dict:
    return json.loads((PROJECT_ROOT / "config.json").read_text())


# ---------------------------------------------------------------------------
# Panel detection (variant A)
# ---------------------------------------------------------------------------

def detect_panels(img: Image.Image) -> list[list[int]]:
    """Segment panels by the white gutters: non-white connected components,
    closed morphologically so art inside a panel fuses into one region."""
    g = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2GRAY)
    H, W = g.shape
    mask = (g < 235).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    boxes = []
    for i in range(1, n):
        x, y, w, h = (int(stats[i, cv2.CC_STAT_LEFT]), int(stats[i, cv2.CC_STAT_TOP]),
                      int(stats[i, cv2.CC_STAT_WIDTH]), int(stats[i, cv2.CC_STAT_HEIGHT]))
        if w * h < W * H * 0.02 or w < W * 0.12 or h < H * 0.05:
            continue
        boxes.append([x, y, x + w, y + h])
    if not boxes:
        return [[0, 0, W, H]]

    # Merge boxes that overlap substantially (art bleeding across gutters).
    merged = True
    while merged:
        merged = False
        out = []
        while boxes:
            b = boxes.pop()
            for o in out:
                ix = max(0, min(b[2], o[2]) - max(b[0], o[0]))
                iy = max(0, min(b[3], o[3]) - max(b[1], o[1]))
                inter = ix * iy
                if inter > 0.4 * min((b[2]-b[0])*(b[3]-b[1]), (o[2]-o[0])*(o[3]-o[1])):
                    o[0], o[1] = min(o[0], b[0]), min(o[1], b[1])
                    o[2], o[3] = max(o[2], b[2]), max(o[3], b[3])
                    merged = True
                    break
            else:
                out.append(b)
        boxes = out
    boxes = [dict(bbox=b) for b in boxes]
    boxes = _sort_blocks(boxes, (W, H), 0.2)      # reading order, same rules as text
    return [b["bbox"] for b in boxes]


# ---------------------------------------------------------------------------
# Fragment builders
# ---------------------------------------------------------------------------

def _narration(blocks: list[dict]) -> str:
    parts = []
    for b in blocks:
        t = (b.get("translation") or "").strip()
        if not t:
            continue
        if t[-1] not in ".!?…:;,—-»›\"'":
            t += "."
        parts.append(t)
    return " ".join(parts)


def _pad_box(box, W, H, pad):
    return [max(0, box[0]-pad), max(0, box[1]-pad), min(W, box[2]+pad), min(H, box[3]+pad)]


def _ink_integral(img: Image.Image):
    """Integral image of the "ink" mask (non-white pixels) for fast window sums."""
    g = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2GRAY)
    ink = (g < 235).astype(np.uint8)
    return cv2.integral(ink)


def _ink_sum(integral, x1, y1, x2, y2):
    return int(integral[y2, x2] - integral[y1, x2] - integral[y2, x1] + integral[y1, x1])


def _smart_window(bbox, W, H, integral):
    """16:9 window that must CONTAIN the text block but is placed to maximise
    artwork inside and to avoid slicing art at the frame edges: interior ink is
    rewarded, ink under the window's edge strips is penalised — so edges settle
    into white gutters and the nearby character is pulled whole into frame,
    with the bubble off-centre instead of dead-centre."""
    x1, y1, x2, y2 = bbox
    bh = y2 - y1
    win_h = int(min(H, max(bh * 3.0, H * 0.34)))
    win_w = int(min(W, win_h * ASPECT))
    win_h = int(min(win_h, win_w / ASPECT))
    if x2 - x1 > win_w or y2 - y1 > win_h:      # block bigger than window: fall back
        return _block_window(bbox, W, H)

    lo_x, hi_x = max(0, x2 - win_w), min(x1, W - win_w)
    lo_y, hi_y = max(0, y2 - win_h), min(y1, H - win_h)
    if hi_x < lo_x: lo_x = hi_x = max(0, min(x1, W - win_w))
    if hi_y < lo_y: lo_y = hi_y = max(0, min(y1, H - win_h))

    strip = max(24, win_h // 18)
    best, best_score = None, None
    xs = {lo_x + (hi_x - lo_x) * i // 8 for i in range(9)}
    ys = {lo_y + (hi_y - lo_y) * i // 8 for i in range(9)}
    for wx in xs:
        for wy in ys:
            wx2, wy2 = wx + win_w, wy + win_h
            interior = _ink_sum(integral, wx, wy, wx2, wy2)
            edges = (_ink_sum(integral, wx, wy, wx2, wy + strip)
                     + _ink_sum(integral, wx, wy2 - strip, wx2, wy2)
                     + _ink_sum(integral, wx, wy, wx + strip, wy2)
                     + _ink_sum(integral, wx2 - strip, wy, wx2, wy2))
            score = interior - 3.5 * edges
            if best_score is None or score > best_score:
                best_score, best = score, (wx, wy)
    wx, wy = best
    return [wx, wy, wx + win_w, wy + win_h]


def _block_window(bbox, W, H):
    """16:9 context window centred on a text block (bubble large in frame)."""
    x1, y1, x2, y2 = bbox
    bh = y2 - y1
    win_h = min(H, max(bh * 3.0, H * 0.30))
    win_w = min(W, win_h * ASPECT)
    win_h = min(win_h, win_w / ASPECT)
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    wx1 = int(min(max(0, cx - win_w / 2), W - win_w))
    wy1 = int(min(max(0, cy - win_h / 2), H - win_h))
    return [wx1, wy1, int(wx1 + win_w), int(wy1 + win_h)]


def fragments_variant_a(page: dict, img: Image.Image, smart: bool = False) -> list[dict]:
    """Whole page briefly, then panel crops with the narration of their blocks."""
    W, H = img.size
    integral = _ink_integral(img) if smart else None
    win = (lambda b: _smart_window(b, W, H, integral)) if smart else \
          (lambda b: _block_window(b, W, H))
    panels = detect_panels(img)
    blocks = [b for b in page["blocks"] if (b.get("translation") or "").strip()]

    def owner(b):
        cx, cy = (b["bbox"][0]+b["bbox"][2])/2, (b["bbox"][1]+b["bbox"][3])/2
        for i, p in enumerate(panels):
            if p[0] <= cx <= p[2] and p[1] <= cy <= p[3]:
                return i
        return min(range(len(panels)),
                   key=lambda i: abs((panels[i][0]+panels[i][2])/2 - cx)
                               + abs((panels[i][1]+panels[i][3])/2 - cy))

    per_panel: dict[int, list[dict]] = {}
    for b in blocks:
        per_panel.setdefault(owner(b), []).append(b)

    frags = [{"box": [0, 0, W, H], "text": "", "min_dur": 1.8}]   # page establisher
    for i, p in enumerate(panels):
        blks = per_panel.get(i, [])
        area_ratio = (p[2] - p[0]) * (p[3] - p[1]) / (W * H)
        if area_ratio > 0.45 and len(blks) >= 2:
            # merged mega-panel (contiguous art defeated gutter detection) —
            # fall back to per-block context windows inside it
            for b in blks:
                frags.append({"box": win(b["bbox"]),
                              "text": _narration([b]), "min_dur": 2.6})
        elif area_ratio > 0.9 and not blks:
            continue          # textless full-page "panel" duplicates the establisher
        else:
            frags.append({"box": _pad_box(p, W, H, 20),
                          "text": _narration(blks),
                          "min_dur": 2.6 if blks else 2.0})
    return frags


def fragments_variant_b(page: dict, img: Image.Image, smart: bool = False) -> list[dict]:
    """A 16:9 context window on every text block (smart: art-seeking placement)."""
    W, H = img.size
    integral = _ink_integral(img) if smart else None
    win = (lambda b: _smart_window(b, W, H, integral)) if smart else \
          (lambda b: _block_window(b, W, H))
    frags = [{"box": [0, 0, W, H], "text": "", "min_dur": 1.8}]
    for b in page["blocks"]:
        t = (b.get("translation") or "").strip()
        if not t:
            continue
        frags.append({"box": win(b["bbox"]),
                      "text": _narration([b]), "min_dur": 2.6})
    return frags


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def _wav_duration(p: Path) -> float:
    try:
        with wave.open(str(p), "rb") as f:
            return f.getnframes() / f.getframerate()
    except Exception:
        return 0.0


def build_variant(chapter: int, cfg: dict, variant: str, preview_only: bool = False) -> None:
    novel = cfg["novel"]["folder"]
    work = PROJECT_ROOT / "temp" / novel / f"chapter-{chapter:05d}"
    data = json.loads((work / "translations.json").read_text())
    rendered = work / "rendered"
    debug_dir = work / f"panel_debug_{variant}"
    debug_dir.mkdir(exist_ok=True)

    # --- collect fragments across all pages -------------------------------
    all_frags: list[dict] = []
    for page in data["pages"]:
        idx = page["page_num"]
        img_path = rendered / f"{idx:04d}.png"
        if not img_path.exists():
            continue
        img = Image.open(img_path).convert("RGB")
        if idx == 1:      # cover: whole page for its narration
            frags = [{"box": [0, 0, *img.size], "text": _narration(page["blocks"]),
                      "min_dur": 4.0}]
        elif variant == "A":
            frags = fragments_variant_a(page, img)
        elif variant == "C":
            frags = fragments_variant_a(page, img, smart=True)
        elif variant == "D":
            frags = fragments_variant_b(page, img, smart=True)
        else:
            frags = fragments_variant_b(page, img)
        for f in frags:
            f["page"] = idx
            f["img_path"] = img_path
        all_frags.extend(frags)

        if variant in ("A", "C", "D"):            # debug overlay of fragments
            dbg = img.copy()
            d = ImageDraw.Draw(dbg)
            for f in frags[1:]:
                d.rectangle(f["box"], outline=(255, 0, 0), width=10)
            w, h = dbg.size
            dbg.resize((w // 4, h // 4)).save(debug_dir / f"{idx:04d}.png")

    print(f"[{variant}] chapter {chapter}: {len(all_frags)} fragments")
    if preview_only:
        return

    # --- TTS per fragment (batch, cached by existing files) ----------------
    audio_dir = work / f"panel_audio_{variant}"
    audio_dir.mkdir(exist_ok=True)
    texts = {}
    for i, f in enumerate(all_frags, 1):
        f["key"] = i
        if f["text"].strip() and not (audio_dir / f"{i:04d}.wav").exists():
            texts[i] = f["text"]
    if texts:
        print(f"[{variant}] TTS: {len(texts)} fragments...")
        generate_chapter_audio(texts, audio_dir, cfg["tts"]["tts_python"])

    # --- encode -------------------------------------------------------------
    names = {"A": "panelsA", "B": "blocksB", "C": "smartC", "D": "smartD"}
    out = (PROJECT_ROOT / cfg["run"]["output_dir"] / novel /
           f"chapter-{chapter:05d}-{names[variant]}.mp4")
    # force_original_aspect_ratio handles BOTH tall and wide fragments (a wide
    # thin panel scaled by height alone exceeded 3840 and broke pad)
    vf = (f"scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=decrease:flags=lanczos,"
          f"pad={TARGET_W}:{TARGET_H}:(ow-iw)/2:(oh-ih)/2:black")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        segs = []
        for f in all_frags:
            crop_p = tmp_dir / f"crop_{f['key']:04d}.png"
            img = Image.open(f["img_path"]).convert("RGB")
            img.crop(f["box"]).save(crop_p)
            wav = audio_dir / f"{f['key']:04d}.wav"
            dur = max(f["min_dur"], _wav_duration(wav) + 0.4) if wav.exists() else f["min_dur"]
            seg = tmp_dir / f"seg_{f['key']:04d}.mp4"

            def _cmd(codec_args):
                c = ["ffmpeg", "-hide_banner", "-y", "-loop", "1", "-framerate", "1",
                     "-i", str(crop_p)]
                if wav.exists():
                    c += ["-i", str(wav), "-map", "0:v:0", "-map", "1:a:0",
                          "-af", "loudnorm=I=-14:TP=-1.5:LRA=11,apad", "-ar", "48000",
                          "-c:a", "aac", "-b:a", "384k"]
                else:
                    c += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
                          "-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-b:a", "384k"]
                return c + ["-t", f"{dur:.3f}", "-vf", vf, *codec_args,
                            "-pix_fmt", "yuv420p", str(seg)]

            nvenc = ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr",
                     "-cq", "18", "-b:v", "0", "-profile:v", "high"]
            x264 = ["-c:v", "libx264", "-preset", "fast", "-tune", "stillimage",
                    "-crf", "18", "-profile:v", "high"]
            if subprocess.run(_cmd(nvenc), capture_output=True).returncode != 0:
                subprocess.run(_cmd(x264), check=True, capture_output=True)
            segs.append(seg)
        concat = tmp_dir / "concat.txt"
        concat.write_text("".join(f"file '{s}'\n" for s in segs))
        partial = out.with_suffix(".mp4.tmp")
        r = subprocess.run(["ffmpeg", "-hide_banner", "-y", "-f", "concat", "-safe", "0",
                            "-i", str(concat), "-c", "copy", "-movflags", "+faststart",
                            "-f", "mp4", str(partial)], capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stderr[-1500:])
            raise SystemExit(f"concat failed (exit {r.returncode})")
        partial.replace(out)
    print(f"[{variant}] -> {out}  ({out.stat().st_size/1e6:.1f} MB)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", choices=["A", "B", "C", "D"], default=None)
    ap.add_argument("--preview", action="store_true", help="only write panel_debug images")
    ap.add_argument("--chapters", help="override config run.chapters (e.g. \"1\")")
    args = ap.parse_args()
    cfg = load_config()
    if args.chapters:
        cfg["run"]["chapters"] = args.chapters
    for ch in chapter_numbers(cfg):
        for v in ([args.variant] if args.variant else ["C", "D"]):
            build_variant(ch, cfg, v, preview_only=args.preview)


if __name__ == "__main__":
    main()
