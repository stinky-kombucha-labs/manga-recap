"""
Panel-by-panel video fragments (the "smartC" recap style).

Given a RENDERED page and its blocks, produce the ordered list of fragments to
show: a brief whole-page establisher, then panel crops (white-gutter panel
detection); where contiguous artwork defeats panel detection (a merged region
covering >45% of the page), fall back to a SMART 16:9 window per text block —
the window must contain the bubble but is placed to maximise artwork inside and
to avoid slicing art at the frame edges, so characters end up whole in frame
and the bubble sits off-centre.

Static cuts only (no motion) — recap-channel convention, comfortable to read on
a phone. The cover (page 1) is always shown whole for its full narration.
"""

from __future__ import annotations

import cv2
import numpy as np
from PIL import Image

ASPECT = 3840 / 2160


def _sort_boxes_reading_order(boxes: list[list[int]], W: int, H: int,
                              band_ratio: float = 0.2) -> list[list[int]]:
    """Same reading order as text blocks: vertical bands, column clusters."""
    from step1_extract import _sort_blocks
    wrapped = _sort_blocks([{"bbox": b} for b in boxes], (W, H), band_ratio)
    return [w["bbox"] for w in wrapped]


def detect_panels(img: Image.Image) -> list[list[int]]:
    """Segment panels by the white gutters: non-white connected components."""
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

    merged = True
    while merged:                      # fuse boxes overlapping >40%
        merged = False
        out: list[list[int]] = []
        while boxes:
            b = boxes.pop()
            for o in out:
                ix = max(0, min(b[2], o[2]) - max(b[0], o[0]))
                iy = max(0, min(b[3], o[3]) - max(b[1], o[1]))
                if ix * iy > 0.4 * min((b[2]-b[0])*(b[3]-b[1]), (o[2]-o[0])*(o[3]-o[1])):
                    o[0], o[1] = min(o[0], b[0]), min(o[1], b[1])
                    o[2], o[3] = max(o[2], b[2]), max(o[3], b[3])
                    merged = True
                    break
            else:
                out.append(b)
        boxes = out
    return _sort_boxes_reading_order(boxes, W, H)


def _ink_integral(img: Image.Image):
    g = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2GRAY)
    return cv2.integral((g < 235).astype(np.uint8))


def _ink_sum(ii, x1, y1, x2, y2):
    return int(ii[y2, x2] - ii[y1, x2] - ii[y2, x1] + ii[y1, x1])


def _naive_window(bbox, W, H):
    x1, y1, x2, y2 = bbox
    win_h = min(H, max((y2 - y1) * 3.0, H * 0.34))
    win_w = min(W, win_h * ASPECT)
    win_h = min(win_h, win_w / ASPECT)
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    wx1 = int(min(max(0, cx - win_w / 2), W - win_w))
    wy1 = int(min(max(0, cy - win_h / 2), H - win_h))
    return [wx1, wy1, int(wx1 + win_w), int(wy1 + win_h)]


def _smart_window(bbox, W, H, integral):
    """16:9 window containing the block, placed to pull nearby art whole into
    frame: interior ink rewarded, ink under the frame edges penalised (edges
    settle into white gutters instead of slicing faces)."""
    x1, y1, x2, y2 = bbox
    win_h = int(min(H, max((y2 - y1) * 3.0, H * 0.34)))
    win_w = int(min(W, win_h * ASPECT))
    win_h = int(min(win_h, win_w / ASPECT))
    if x2 - x1 > win_w or y2 - y1 > win_h:
        return _naive_window(bbox, W, H)

    lo_x, hi_x = max(0, x2 - win_w), min(x1, W - win_w)
    lo_y, hi_y = max(0, y2 - win_h), min(y1, H - win_h)
    if hi_x < lo_x:
        lo_x = hi_x = max(0, min(x1, W - win_w))
    if hi_y < lo_y:
        lo_y = hi_y = max(0, min(y1, H - win_h))

    strip = max(24, win_h // 18)
    best, best_score = (lo_x, lo_y), None
    for wx in {lo_x + (hi_x - lo_x) * i // 8 for i in range(9)}:
        for wy in {lo_y + (hi_y - lo_y) * i // 8 for i in range(9)}:
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


def page_fragments(page: dict, img: Image.Image, is_cover: bool = False,
                   cfg: dict | None = None) -> list[dict]:
    """Ordered fragments for one page: [{box, text, min_dur}, ...]."""
    cfg = cfg or {}
    W, H = img.size
    if is_cover:
        return [{"box": [0, 0, W, H], "text": _narration(page["blocks"]),
                 "min_dur": float(cfg.get("cover_min_dur", 4.0))}]

    est_dur = float(cfg.get("establisher_dur", 1.8))
    text_dur = float(cfg.get("fragment_min_dur", 2.6))
    art_dur = float(cfg.get("art_fragment_dur", 2.0))

    panels = detect_panels(img)
    blocks = [b for b in page["blocks"] if (b.get("translation") or "").strip()]
    integral = None

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

    frags = [{"box": [0, 0, W, H], "text": "", "min_dur": est_dur}]
    for i, p in enumerate(panels):
        blks = per_panel.get(i, [])
        area_ratio = (p[2] - p[0]) * (p[3] - p[1]) / (W * H)
        if area_ratio > 0.45 and len(blks) >= 2:
            if integral is None:
                integral = _ink_integral(img)
            for b in blks:
                frags.append({"box": _smart_window(b["bbox"], W, H, integral),
                              "text": _narration([b]), "min_dur": text_dur})
        elif area_ratio > 0.9 and not blks:
            continue          # textless full-page "panel" duplicates the establisher
        else:
            pad = 20
            box = [max(0, p[0]-pad), max(0, p[1]-pad), min(W, p[2]+pad), min(H, p[3]+pad)]
            frags.append({"box": box, "text": _narration(blks),
                          "min_dur": text_dur if blks else art_dur})
    return frags
