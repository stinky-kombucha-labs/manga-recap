"""
Render module — LaMa inpaint + Anime Ace stroke text (v1 approach).

Generalised from scripts/10_generate_pro_variants.py for any page + OCR blocks.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import torch
from PIL import Image, ImageDraw, ImageFont

_MIT_ROOT = Path(__file__).resolve().parents[2] / "manga-image-translator"
sys.path.insert(0, str(_MIT_ROOT))

from manga_translator.config import Config
from manga_translator.manga_translator import MangaTranslator
from manga_translator.utils import Quadrilateral, TextBlock

ANIME_ACE = _MIT_ROOT / "fonts" / "anime_ace_3.ttf"
NOTO_BOLD = Path("/usr/share/fonts/google-noto/NotoSans-Bold.ttf")
USE_GPU = bool(torch.cuda.is_available() or torch.backends.mps.is_available())

_PROBE = ImageDraw.Draw(Image.new("RGB", (8, 8)))


# ---------------------------------------------------------------------------
# Font helpers
# ---------------------------------------------------------------------------

def _load_font(size: int) -> ImageFont.FreeTypeFont:
    if ANIME_ACE.exists():
        return ImageFont.truetype(str(ANIME_ACE), size)
    return ImageFont.truetype(str(NOTO_BOLD), size)


def _cap_ratio() -> float:
    """Rendered uppercase cap height as a fraction of the nominal font size for
    the active font — used to translate a target glyph height (px) into a font
    size so the Ukrainian caps match the measured English caps."""
    try:
        f = _load_font(100)
        bb = _PROBE.textbbox((0, 0), "ХІМШABZ", font=f)
        r = (bb[3] - bb[1]) / 100.0
        return r if r > 0.1 else 0.72
    except Exception:
        return 0.72


_CAP_RATIO = _cap_ratio()


def _font_for_glyph_height(px: float) -> int:
    return max(8, int(round(px / _CAP_RATIO)))


# Anime Ace lacks several glyphs the Ukrainian text needs (І Ї Є Ґ, em dash, …).
# It renders every missing glyph as the same "№"-looking .notdef box. Rather than
# hardcode a list, detect missing glyphs by comparing each char's bitmap to the
# font's .notdef bitmap, and draw those from a Cyrillic-complete fallback (Noto).
_fallback_cache: dict[int, ImageFont.FreeTypeFont] = {}
_notdef_cache: dict[int, bytes] = {}
_missing_cache: dict[tuple[str, int], bool] = {}


def _load_fallback(size: int) -> ImageFont.FreeTypeFont:
    if size not in _fallback_cache:
        path = NOTO_BOLD if NOTO_BOLD.exists() else ANIME_ACE
        _fallback_cache[size] = ImageFont.truetype(str(path), size)
    return _fallback_cache[size]


def _notdef_bytes(primary: ImageFont.FreeTypeFont) -> bytes:
    size = int(primary.size)
    if size not in _notdef_cache:
        try:
            _notdef_cache[size] = primary.getmask("").tobytes()   # private-use => guaranteed missing
        except Exception:
            _notdef_cache[size] = b""
    return _notdef_cache[size]


def _is_missing(ch: str, primary: ImageFont.FreeTypeFont) -> bool:
    if not ch.strip():
        return False
    key = (ch, int(primary.size))
    if key not in _missing_cache:
        try:
            _missing_cache[key] = primary.getmask(ch).tobytes() == _notdef_bytes(primary)
        except Exception:
            _missing_cache[key] = True
    return _missing_cache[key]


def _font_for_char(ch: str, primary: ImageFont.FreeTypeFont,
                   fallback: ImageFont.FreeTypeFont) -> ImageFont.FreeTypeFont:
    return fallback if _is_missing(ch, primary) else primary


def _ukr_to_anime_ace(text: str) -> str:
    # Native Cyrillic; only Ї/Є/Ґ are drawn from the fallback font at paint time.
    return text.upper()


def _measured_lh(font: ImageFont.FreeTypeFont, sw: int) -> int:
    bb = _PROBE.textbbox((0, 0), "Агя|", font=font, stroke_width=sw)
    return max(1, bb[3] - bb[1])


def _line_w(line: str, font: ImageFont.FreeTypeFont, sw: int,
            fb: ImageFont.FreeTypeFont | None = None) -> int:
    """Rendered width of one line (sum of per-char advances, fallback-aware)
    INCLUDING the stroke on both sides."""
    if fb is None:
        bb = _PROBE.textbbox((0, 0), line, font=font, stroke_width=sw)
        return bb[2] - bb[0]
    w = 0.0
    for ch in line:
        w += _font_for_char(ch, font, fb).getlength(ch)
    return int(w) + 2 * sw


def _stroke_for(fs: int, cfg: dict) -> int:
    """Stroke width for a given font size.

    If cfg['stroke_ratio'] is set the stroke scales with the font (keeps the
    outline visually proportional at every size); otherwise a fixed
    cfg['stroke_width'] is used.
    """
    ratio = cfg.get("stroke_ratio")
    if ratio:
        return max(1, int(round(fs * float(ratio))))
    return max(0, int(cfg.get("stroke_width", 7)))


def _wrap(text: str, font: ImageFont.FreeTypeFont, max_w: int, sw: int,
          fb: ImageFont.FreeTypeFont | None = None) -> list[str]:
    """Greedy word wrap. Width is measured WITH the stroke so wrapped lines
    reflect what is actually painted, not the thinner glyph-only width."""
    max_w = max(1, int(max_w))
    words = text.split()
    lines, line = [], []
    for w in words:
        cand = " ".join(line + [w])
        if _line_w(cand, font, sw, fb) <= max_w or not line:
            line.append(w)
        else:
            lines.append(" ".join(line))
            line = [w]
    if line:
        lines.append(" ".join(line))
    return lines


def _fit_text(text: str, bw: int, bh: int, cfg: dict,
              max_fs: int = 61, min_fs: int = 21, spacing: float = 1.18):
    """Pick the largest font size at which the wrapped text fits the box on
    BOTH axes (width incl. stroke, and total height). Steps down by 1 px for
    a tight fit. Returns (font, lines, line_height, normalised_text, stroke_w).

    When even min_fs cannot fit (very long text in a tiny bubble) the smallest
    size is returned as a best effort — it will still be centred inside the box.
    """
    norm = _ukr_to_anime_ace(text)
    pad = 16
    avail_w = max(1, bw - pad)
    avail_h = max(1, bh - pad)
    for fs in range(int(max_fs), int(min_fs) - 1, -1):
        sw = _stroke_for(fs, cfg)
        font = _load_font(fs)
        fb = _load_fallback(fs)
        lines = _wrap(norm, font, avail_w, sw, fb)
        lh = int(_measured_lh(font, sw) * spacing)
        widest = max((_line_w(ln, font, sw, fb) for ln in lines), default=0)
        if lh * len(lines) <= avail_h and widest <= avail_w:
            return font, fb, lines, lh, norm, sw
    sw = _stroke_for(min_fs, cfg)
    font = _load_font(int(min_fs))
    fb = _load_fallback(int(min_fs))
    lines = _wrap(norm, font, avail_w, sw, fb)
    lh = int(_measured_lh(font, sw) * spacing)
    return font, fb, lines, lh, norm, sw


def _draw_text_mixed(draw: ImageDraw.ImageDraw, s: str, x: float, y: int,
                     font, fb, sw: int) -> float:
    """Draw a string char-by-char, picking the fallback font for Ї/Є/Ґ so they
    render correctly while the rest keeps the Anime Ace look. Returns advance."""
    cx = float(x)
    for ch in s:
        f = _font_for_char(ch, font, fb)
        draw.text((round(cx), y), ch, font=f, fill=(12, 12, 12),
                  stroke_width=sw, stroke_fill=(255, 255, 255))
        cx += f.getlength(ch)
    return cx - x


def _word_w(word: str, font, fb, sw: int) -> float:
    return sum(_font_for_char(ch, font, fb).getlength(ch) for ch in word)


def _draw_justify(draw: ImageDraw.ImageDraw, line: str, font, fb, x1: int, y: int,
                  target_w: int, sw: int) -> None:
    words = line.split()
    if len(words) <= 1:
        lw = _word_w(line, font, fb, sw)
        _draw_text_mixed(draw, line, x1 + max(0, (target_w - lw) / 2), y, font, fb, sw)
        return
    wws = [_word_w(w, font, fb, sw) for w in words]
    gap = (target_w - sum(wws)) / (len(words) - 1)
    x = float(x1)
    for w, ww in zip(words, wws):
        _draw_text_mixed(draw, w, x, y, font, fb, sw)
        x += ww + gap


def _draw_underlay(image: Image.Image, x1: int, y1: int, w: int, h: int, cfg: dict) -> None:
    """Paint a translucent rounded rectangle behind the text so the overlay
    stays legible on busy artwork. Alpha-composited onto the page."""
    alpha = float(cfg.get("underlay_alpha", 0.55))
    if alpha <= 0 or w <= 0 or h <= 0:
        return
    color = tuple(cfg.get("underlay_color", (255, 255, 255)))
    radius = int(cfg.get("underlay_radius", 18))
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rounded_rectangle([x1, y1, x1 + w, y1 + h], radius=radius,
                         fill=(*color, int(round(alpha * 255))))
    image.alpha_composite(overlay) if image.mode == "RGBA" else \
        image.paste(Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB"), (0, 0))


def render_text_on_image(image: Image.Image, translation: str, bbox: list[int],
                          cfg: dict) -> None:
    """Render Ukrainian text with Word-justify and white stroke into bbox.

    Font size is chosen so the wrapped text fits the box on both axes; an
    optional translucent underlay can be drawn first (cfg['underlay'])."""
    x1, y1, x2, y2 = [int(v) for v in bbox]
    bw, bh = x2 - x1, y2 - y1
    if bw <= 0 or bh <= 0 or not translation.strip():
        return
    font, fb, lines, lh, norm, sw = _fit_text(
        translation, bw, bh, cfg,
        max_fs=cfg.get("font_max", 61),
        min_fs=cfg.get("font_min", 21),
        spacing=cfg.get("line_spacing", 1.18),
    )
    total_h = lh * len(lines)
    avail_w = bw - 16
    widest = max((_line_w(ln, font, sw, fb) for ln in lines), default=0)

    if cfg.get("underlay"):
        pad_u = int(cfg.get("underlay_pad", 14))
        uw = min(bw, widest + 2 * pad_u)
        uh = min(bh, total_h + 2 * pad_u)
        ux = x1 + max(0, (bw - uw) // 2)
        uy = y1 + max(0, (bh - uh) // 2)
        _draw_underlay(image, ux, uy, uw, uh, cfg)

    draw = ImageDraw.Draw(image)
    y = y1 + max(8, (bh - total_h) // 2)

    # Word-justify spreads short lines into ugly gaps in narrow bubbles; default
    # to centered lines unless justify is explicitly requested.
    justify = bool(cfg.get("justify", False))
    for i, line in enumerate(lines):
        is_last = (i == len(lines) - 1)
        if not justify or is_last or len(line.split()) <= 1:
            lw = _line_w(line, font, sw, fb)
            _draw_text_mixed(draw, line, x1 + 8 + max(0, (avail_w - lw) // 2), y,
                             font, fb, sw)
        else:
            _draw_justify(draw, line, font, fb, x1 + 8, y, avail_w, sw)
        y += lh


# ---------------------------------------------------------------------------
# LaMa mask
# ---------------------------------------------------------------------------

def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _odd(v):
    v = max(3, int(v))
    return v if v % 2 else v + 1


def _expanded_render_bbox(bbox: list[int], image_size: tuple[int, int], cfg: dict) -> list[int]:
    if not cfg.get("expand_render_bbox", True):
        return [int(v) for v in bbox]
    width, height = image_size
    x1, y1, x2, y2 = [int(v) for v in bbox]
    bw, bh = max(1, x2 - x1), max(1, y2 - y1)
    max_pad = int(cfg.get("render_padding_max", 80))
    pad_x = min(max_pad, max(8, int(bw * float(cfg.get("render_padding_x_ratio", 0.18)))))
    pad_y = min(max_pad, max(6, int(bh * float(cfg.get("render_padding_y_ratio", 0.42)))))
    return [
        _clamp(x1 - pad_x, 0, max(0, width - 1)),
        _clamp(y1 - pad_y, 0, max(0, height - 1)),
        _clamp(x2 + pad_x, 1, width),
        _clamp(y2 + pad_y, 1, height),
    ]


def _letter_mask_from_patch(local_gray: np.ndarray) -> np.ndarray:
    h, w = local_gray.shape[:2]
    if h <= 0 or w <= 0:
        return np.zeros_like(local_gray, dtype=np.uint8)

    candidates = [
        (local_gray < 205).astype(np.uint8) * 255,
        (local_gray > 235).astype(np.uint8) * 255,
    ]
    if float(local_gray.std()) >= 4.0:
        _, otsu = cv2.threshold(local_gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        candidates.extend([otsu, cv2.bitwise_not(otsu)])

    box_area = max(1, h * w)
    min_area = max(3, int(box_area * 0.0004))
    max_area = max(min_area + 1, int(box_area * 0.60))
    filtered = np.zeros((h, w), dtype=np.uint8)

    for cand in candidates:
        num, labels, stats, _ = cv2.connectedComponentsWithStats(cand, connectivity=8)
        for lbl in range(1, num):
            area = int(stats[lbl, cv2.CC_STAT_AREA])
            cw = int(stats[lbl, cv2.CC_STAT_WIDTH])
            ch = int(stats[lbl, cv2.CC_STAT_HEIGHT])
            if area < min_area or area > max_area:
                continue
            if cw >= int(w * 0.98) and ch >= int(h * 0.80):
                continue
            filtered[labels == lbl] = 255

    return filtered


# ---------------------------------------------------------------------------
# English text measurement + full-extent detection
# ---------------------------------------------------------------------------

def english_glyph_height(gray: np.ndarray, block: dict) -> float:
    """Median pixel height of the English lettering in a block (its OCR line
    boxes). Used to size the Ukrainian so it is never bigger than the English."""
    H, W = gray.shape[:2]
    heights = []
    for lb in (block.get("line_bboxes") or [block["bbox"]]):
        x1, y1, x2, y2 = [int(v) for v in lb]
        x1, y1, x2, y2 = max(0, x1), max(0, y1), min(W, x2), min(H, y2)
        if x2 <= x1 or y2 <= y1:
            continue
        patch = gray[y1:y2, x1:x2]
        ph = patch.shape[0]
        for inv in (cv2.THRESH_BINARY_INV, cv2.THRESH_BINARY):
            _, th = cv2.threshold(patch, 0, 255, inv + cv2.THRESH_OTSU)
            n, _, stats, _ = cv2.connectedComponentsWithStats(th, connectivity=8)
            for i in range(1, n):
                h = int(stats[i, cv2.CC_STAT_HEIGHT])
                w = int(stats[i, cv2.CC_STAT_WIDTH])
                if h < ph * 0.25 or h > ph * 0.95 or w > (x2 - x1) * 0.6:
                    continue
                heights.append(h)
    if heights:
        return float(np.median(heights))
    return (block["bbox"][3] - block["bbox"][1]) * 0.4


def detect_text_extent(gray: np.ndarray, block: dict, eng_h: float,
                       cfg: dict) -> list[list[int]]:
    """Find continuation text lines around a block that the OCR missed.

    Opens a search window around the block bbox, segments letter-sized
    components (so large artwork is ignored), groups them into rows, and keeps
    only rows that look like real text (several aligned letters spanning a
    meaningful width). Returns the extra line boxes (page coords); the caller
    adds them to the clean mask and the render bbox so the WHOLE original
    English caption is replaced, not just the part OCR recorded.
    """
    if not cfg.get("detect_extent", True) or eng_h <= 0:
        return []
    H, W = gray.shape[:2]
    bx1, by1, bx2, by2 = [int(v) for v in block["bbox"]]
    bw, bh = max(1, bx2 - bx1), max(1, by2 - by1)
    mx = float(cfg.get("detect_margin_x", 0.10))
    up = float(cfg.get("detect_margin_up", 0.30))
    dn = float(cfg.get("detect_margin_down", 1.20))
    wx1 = max(0, bx1 - int(bw * mx)); wx2 = min(W, bx2 + int(bw * mx))
    wy1 = max(0, by1 - int(bh * up)); wy2 = min(H, by2 + int(bh * dn))
    patch = gray[wy1:wy2, wx1:wx2]
    if patch.size == 0:
        return []
    mask = _letter_mask_from_patch(patch)
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    letters = []
    for i in range(1, n):
        x = int(stats[i, cv2.CC_STAT_LEFT]); y = int(stats[i, cv2.CC_STAT_TOP])
        w = int(stats[i, cv2.CC_STAT_WIDTH]); h = int(stats[i, cv2.CC_STAT_HEIGHT])
        if h < eng_h * 0.45 or h > eng_h * 1.9 or w > eng_h * 6:
            continue
        letters.append([wx1 + x, wy1 + y, wx1 + x + w, wy1 + y + h])
    if not letters:
        return []

    letters.sort(key=lambda b: (b[1] + b[3]) / 2)
    rows, cur = [], [letters[0]]
    cy = (letters[0][1] + letters[0][3]) / 2
    for b in letters[1:]:
        c = (b[1] + b[3]) / 2
        if abs(c - cy) <= eng_h * 0.7:
            cur.append(b)
        else:
            rows.append(cur); cur = [b]
        cy = c
    rows.append(cur)

    pad = int(eng_h * 0.25)
    out = []
    for r in rows:
        x1 = min(b[0] for b in r); y1 = min(b[1] for b in r)
        x2 = max(b[2] for b in r); y2 = max(b[3] for b in r)
        if len(r) >= 4 and (x2 - x1) >= eng_h * 4:        # genuine text line
            out.append([x1, max(0, y1 - pad), x2, min(H, y2 + pad)])
    return out


def build_lama_mask(image_rgb: np.ndarray, line_bboxes: list[list[int]],
                    dilation: int = 30, kernel_size: int = 5) -> np.ndarray:
    h, w = image_rgb.shape[:2]
    gray = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2GRAY)
    mask = np.zeros((h, w), dtype=np.uint8)

    for box in line_bboxes:
        x1, y1, x2, y2 = [_clamp(int(v), 0, dim - 1)
                           for v, dim in zip(box, [w, h, w, h])]
        x2 = _clamp(x2, 1, w)
        y2 = _clamp(y2, 1, h)
        if x2 <= x1 or y2 <= y1:
            continue
        local_gray = gray[y1:y2, x1:x2]
        filtered = _letter_mask_from_patch(local_gray)
        if cv2.countNonZero(filtered) < max(4, int((y2 - y1) * (x2 - x1) * 0.006)):
            # If letter segmentation fails, still remove the narrow OCR line box.
            filtered[:, :] = 255
        mask[y1:y2, x1:x2] = cv2.bitwise_or(mask[y1:y2, x1:x2], filtered)

    if kernel_size > 1:
        kk = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (_odd(kernel_size),) * 2)
        mask = cv2.dilate(mask, kk, iterations=1)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (_odd(dilation * 2 + 1),) * 2)
    mask = cv2.dilate(mask, k, iterations=1)
    mask[mask > 0] = 255
    return mask


# ---------------------------------------------------------------------------
# MIT-based LaMa inpainter
# ---------------------------------------------------------------------------

def _box_to_quad(box):
    x1, y1, x2, y2 = [int(v) for v in box]
    return np.array([[x1, y1], [x2, y1], [x2, y2], [x1, y2]], dtype=np.float32)


class _PageInpainter(MangaTranslator):
    """MangaTranslator subclass: uses OCR block data, skips MIT OCR/detect/translate/render."""

    def __init__(self, blocks: list[dict], cfg: dict):
        super().__init__({
            "use_gpu": USE_GPU,
            "kernel_size": cfg.get("kernel_size", 5),
            "font_path": str(ANIME_ACE),
            "model_dir": str(_MIT_ROOT / "models"),
            "input": [],
            "ignore_errors": False,
            "models_ttl": 1,
        })
        self._blocks = blocks
        self._cfg = cfg

    async def _run_detection(self, config: Config, ctx: Any):
        textlines = [
            Quadrilateral(_box_to_quad(lb), "ocr", 1.0, 0, 0, 0, 255, 255, 255)
            for block in self._blocks
            for lb in block.get("line_bboxes", [block["bbox"]])
        ]
        mask = build_lama_mask(
            ctx.img_rgb,
            [lb for block in self._blocks for lb in block.get("line_bboxes", [block["bbox"]])],
            dilation=self._cfg.get("mask_dilation", 30),
            kernel_size=self._cfg.get("kernel_size", 5),
        )
        return textlines, mask, None

    async def _run_ocr(self, config: Config, ctx: Any):
        return ctx.textlines

    async def _run_textline_merge(self, config: Config, ctx: Any):
        regions = []
        for block in self._blocks:
            line_boxes = block.get("line_bboxes") or [block["bbox"]]
            lines_q = [_box_to_quad(lb) for lb in line_boxes]
            region = TextBlock(
                lines=lines_q,
                texts=["ocr"],
                font_size=60,
                translation=block.get("translation", block.get("text", "")),
                fg_color=(0, 0, 0),
                bg_color=(255, 255, 255),
                direction="horizontal",
                alignment="center",
                target_lang="UKR",
                source_lang="ENG",
            )
            region.text_raw = "ocr"
            region._direction = "horizontal"
            region._alignment = "center"
            regions.append(region)
        return regions

    async def _run_text_translation(self, config: Config, ctx: Any):
        return ctx.text_regions

    async def _run_text_rendering(self, config: Config, ctx: Any):
        return ctx.img_inpainted


def _mit_config(cfg: dict) -> Config:
    return Config(**{
        "detector": {"detector": "none", "detection_size": cfg.get("inpainting_size", 2560)},
        "ocr": {"ocr": "48px", "min_text_length": 1, "ignore_bubble": 0},
        "inpainter": {
            "inpainter": "lama_large",
            "inpainting_size": cfg.get("inpainting_size", 2560),
            "inpainting_precision": "bf16" if USE_GPU else "fp32",
        },
        "translator": {"translator": "none", "target_lang": "UKR",
                       "enable_post_translation_check": False},
        "render": {"renderer": "none", "alignment": "center", "direction": "horizontal",
                   "uppercase": True, "no_hyphenation": True, "font_size_minimum": 18},
        "mask_dilation_offset": cfg.get("mask_dilation", 30),
        "kernel_size": cfg.get("kernel_size", 5),
    })


async def _inpaint_async(image: Image.Image, blocks: list[dict], cfg: dict) -> Image.Image:
    if not blocks:
        return image
    inpainter = _PageInpainter(blocks, cfg)
    ctx = await inpainter.translate(image, _mit_config(cfg), skip_context_save=True)
    result = ctx.result
    return result.convert("RGB") if result else image


def _blur_background(image: Image.Image, blocks: list[dict], cfg: dict) -> Image.Image:
    """Heavily blur the original-text regions instead of neural inpainting.

    Each block's expanded render bbox (the exact area the Ukrainian overlay will
    occupy) is blurred so the English underneath becomes an unreadable smudge and
    the overlay always lands on a clean, soft background. The blur mask is feathered
    so the edges of each patch fade into the artwork rather than showing a hard seam.
    """
    arr = np.array(image)
    h, w = arr.shape[:2]

    # Adaptive blur strength: at 4K a fixed sigma barely softens large lettering,
    # so scale it by the typical text-line height. blur_strength is the fraction of
    # a line's height used as sigma (~0.6 makes glyphs unreadable). blur_sigma is a
    # floor for tiny text. Override either in config / variant.
    line_heights = [
        (lb[3] - lb[1])
        for block in blocks
        for lb in (block.get("line_bboxes") or [block["bbox"]])
        if (lb[3] - lb[1]) > 0
    ]
    median_lh = float(np.median(line_heights)) if line_heights else 24.0
    strength = float(cfg.get("blur_strength", 0.6))
    sigma = max(float(cfg.get("blur_sigma", 18)), median_lh * strength)
    blurred = cv2.GaussianBlur(arr, (0, 0), sigmaX=sigma, sigmaY=sigma)

    mask = np.zeros((h, w), dtype=np.uint8)
    for block in blocks:
        # Blur both the (tight) OCR line boxes and the expanded overlay bbox so
        # the smudged area fully covers wherever text gets painted.
        boxes = list(block.get("line_bboxes") or []) + [block["bbox"]]
        for box in boxes:
            x1, y1, x2, y2 = _expanded_render_bbox(box, (w, h), cfg)
            cv2.rectangle(mask, (x1, y1), (x2, y2), 255, thickness=-1)

    feather = int(cfg.get("blur_feather", 9))
    if feather > 0:
        mask = cv2.GaussianBlur(mask, (0, 0), sigmaX=feather, sigmaY=feather)
    m = (mask.astype(np.float32) / 255.0)[..., None]
    out = (arr.astype(np.float32) * (1.0 - m) + blurred.astype(np.float32) * m)
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))


def _clean_background(image: Image.Image, blocks: list[dict], cfg: dict) -> Image.Image:
    """Remove/obscure the original English text. Dispatches on cfg['bg_mode']:
      - 'lama'      : neural inpainting (default, cleanest, slowest)
      - 'blur'      : gaussian-blur the text regions
      - 'blur_box'  : same as 'blur' (translucent underlay is added at draw time)
    """
    mode = str(cfg.get("bg_mode", "lama")).lower()
    if mode in ("blur", "blur_box"):
        return _blur_background(image, blocks, cfg)
    return asyncio.run(_inpaint_async(image, blocks, cfg))


def _plan_blocks(image: Image.Image, blocks: list[dict], cfg: dict) -> list[dict]:
    """For each block compute: the English glyph height, any missed continuation
    text lines (so the WHOLE caption is replaced), the full render extent, and a
    font_max capped to the English glyph height (Ukrainian never bigger than the
    English it replaces). Returns the blocks augmented with these fields and with
    `line_bboxes` extended so background cleaning covers the detected text too.
    """
    glyph_match = str(cfg.get("fit_mode", "glyph_match")).lower() == "glyph_match"
    ratio = float(cfg.get("glyph_match_ratio", 1.0))
    gray = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2GRAY)
    planned = []
    for block in blocks:
        nb = dict(block)
        base_lines = list(block.get("line_bboxes") or [block["bbox"]])
        eng_h = english_glyph_height(gray, block)
        extra = detect_text_extent(gray, block, eng_h, cfg)
        all_lines = base_lines + extra
        nb["line_bboxes"] = all_lines
        # Render extent = union of every text line we will erase.
        xs1 = [b[0] for b in all_lines] + [block["bbox"][0]]
        ys1 = [b[1] for b in all_lines] + [block["bbox"][1]]
        xs2 = [b[2] for b in all_lines] + [block["bbox"][2]]
        ys2 = [b[3] for b in all_lines] + [block["bbox"][3]]
        nb["_extent"] = [min(xs1), min(ys1), max(xs2), max(ys2)]
        nb["_eng_h"] = eng_h
        if glyph_match and eng_h > 0:
            fs = _font_for_glyph_height(eng_h * ratio)
            nb["_font_max"] = max(int(cfg.get("font_min", 14)), fs)
        else:
            nb["_font_max"] = int(cfg.get("font_max", 61))
        planned.append(nb)
    return planned


def render_page(page_path: Path, blocks: list[dict], out_path: Path, cfg: dict) -> None:
    """
    Full render pipeline for one manga page:
    1. Detect the full English text extent per block (catch OCR-missed lines)
    2. Clean those areas (LaMa inpaint or blur, per cfg['bg_mode'])
    3. Render Ukrainian sized to the English glyph height, across the full extent

    blocks: list from ocr.py + translate.py (with "bbox", "line_bboxes", "translation")
    """
    image = Image.open(page_path).convert("RGB")

    if blocks:
        planned = _plan_blocks(image, blocks, cfg)
        image = _clean_background(image, planned, cfg)
        for block in planned:
            t = block.get("translation", "").strip()
            if not t:
                continue
            extent = block.get("_extent", block["bbox"])
            bbox = (extent if str(cfg.get("fit_mode", "glyph_match")).lower() == "glyph_match"
                    else _expanded_render_bbox(block["bbox"], image.size, cfg))
            block_cfg = {**cfg, "font_max": block.get("_font_max", cfg.get("font_max", 61))}
            block_cfg["font_min"] = min(int(cfg.get("font_min", 14)), block_cfg["font_max"])
            render_text_on_image(image, t, bbox, block_cfg)

    image.save(out_path)
