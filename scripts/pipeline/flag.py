"""Deterministic problem detection for translated blocks (no LLM).

Most of Lapa's translations are fine; only a small fraction have issues (OCR-induced
mistranslations, "explanation" hallucinations, leftover English, dropped/added text).
This module flags ONLY the suspicious blocks so the expensive review (local Lapa
repair, then Claude) touches a handful of blocks per chapter instead of all ~70 — the
key to staying affordable across 900+ chapters without lowering quality.

`flag_block` returns a list of reason strings (empty list = block looks fine).
Patterns ported from the sister Lapa project's problem detector.
"""

from __future__ import annotations

import re

# Lapa "explained" the text instead of translating it (the failure mode on short /
# garbled OCR like "WA", "ATP"): "це абревіатура…", "перекладається як…", "це речення…".
_EXPLANATION_PATTERNS = [
    r"^переклад\s*:",
    r"^ось переклад",
    r"українськ[аоуіїею]+\s+мов[аоуіїею]+",
    r"англійськ[аоуіїею]+\s+мов[аоуіїею]+",
    r"перекла[дст]\w*\s.*(українськ|англійськ|мов[аоую])",
    r"перекладається\s+як",
    r"мовою\s+це\s+буде",
    r"це\s+абревіатур",
    r"розшифров",
    r"набір\s+(випадкових|літер)",
    r"не\s+має\s+(конкретного\s+)?значенн",
    r"у\s+цьому\s+реченні",
    r"це\s+речення\s+(англійськ|українськ|перекл)",
]

# Junk that should never be a translation.
_HALLUCINATION_MARKERS = [r"перекладач", r"без пояснень", r"→", r"^імена\s*:"]

# A single word mixing Latin and Cyrillic letters (e.g. "Сloud", "Айн6") — OCR/merge damage.
_MIXED_SCRIPT_WORD_RE = re.compile(
    r"[A-Za-zА-Яа-яІіЇїЄєҐґ'’]*[A-Za-z][A-Za-zА-Яа-яІіЇїЄєҐґ'’]*"
    r"[А-Яа-яІіЇїЄєҐґ][A-Za-zА-Яа-яІіЇїЄєҐґ'’]*|"
    r"[A-Za-zА-Яа-яІіЇїЄєҐґ'’]*[А-Яа-яІіЇїЄєҐґ][A-Za-zА-Яа-яІіЇїЄєҐґ'’]*"
    r"[A-Za-z][A-Za-zА-Яа-яІіЇїЄєҐґ'’]*"
)

_DEFAULTS = {
    "latin_threshold": 0.30,   # >30% Latin letters in the translation = leftover English
    "len_ratio_min": 0.40,     # translation much shorter than original = dropped text
    "len_ratio_max": 2.60,     # much longer = invented text / explanation
    "len_check_min_chars": 12,  # only length-check originals with enough letters
    "empty_min_letters": 10,    # only flag a missing translation if the source is real text
                                # (short SFX/interjections like "WA", "AH" stay empty)
    "verify_recovered": True,   # flag PaddleOCR-recovered captions for an image check (their
                                # OCR is error-prone, e.g. WORD↔WORLD — only the page reveals it)
}


def _has_letters(s: str) -> bool:
    return any(ch.isalpha() for ch in s or "")


def _mixed_script(text: str) -> bool:
    for m in _MIXED_SCRIPT_WORD_RE.finditer(text):
        w = m.group(0)
        if re.search(r"[A-Za-z]", w) and re.search(r"[А-Яа-яІіЇїЄєҐґ]", w):
            return True
    return False


def flag_block(block: dict, cfg: dict | None = None) -> list[str]:
    """Return reasons this block's translation is suspicious (empty list if fine)."""
    c = {**_DEFAULTS, **(cfg or {})}
    original = (block.get("original") or "").strip()
    translation = (block.get("translation") or "").strip()
    reasons: list[str] = []

    # Missing translation: only worth flagging if the source is real text. Short SFX /
    # interjections ("WA", "AH", garbled OCR) are meant to stay empty (rendered as art).
    if not translation:
        letters = sum(1 for ch in original if ch.isalpha())
        return ["empty"] if letters >= c["empty_min_letters"] else reasons

    if (any(re.search(p, translation, re.IGNORECASE) for p in _EXPLANATION_PATTERNS)
            or any(re.search(p, translation, re.IGNORECASE) for p in _HALLUCINATION_MARKERS)):
        reasons.append("explanation")

    if _mixed_script(translation):
        reasons.append("mixed")

    latin = len(re.findall(r"[A-Za-z]", translation))
    cyr = len(re.findall(r"[А-Яа-яІіЇїЄєҐґ]", translation))
    if latin + cyr and latin / (latin + cyr) > c["latin_threshold"]:
        reasons.append("latin")

    if _has_letters(original) and len(re.sub(r"\s", "", original)) >= c["len_check_min_chars"]:
        ratio = len(translation) / max(1, len(original))
        if ratio < c["len_ratio_min"] or ratio > c["len_ratio_max"]:
            reasons.append("length")

    # Recovered captions read by PaddleOCR are the most OCR-error-prone (the source text
    # itself may be wrong, which no text-only check can catch). Mark them so the optional
    # image-aware review (Claude opening the page) confirms the meaning.
    if c.get("verify_recovered", True) and "paddle" in (block.get("detector") or ""):
        reasons.append("verify")

    return reasons
