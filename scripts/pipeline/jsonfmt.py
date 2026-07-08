"""Human-friendly serialization for translations.json.

The file is meant to be hand-edited between step 2 (translate) and step 3
(render): a human reads the English `original`, checks/edits the Ukrainian
`translation`, and leaves the geometry alone. So we:

  * order each block as  id -> original -> translation -> detector -> bbox -> line_bboxes
    (the two fields you actually edit sit right at the top), and
  * collapse the numeric coordinate arrays onto a single line each, instead of
    one number per line, so the bubbles stay compact and the text is easy to scan.

Standard JSON either way — `json.loads` reads it back unchanged.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

# Order blocks so the editable text is first and the geometry is tucked at the end.
_BLOCK_KEY_ORDER = ("id", "original", "translation", "detector", "bbox", "line_bboxes")

# Matches an innermost array of only numbers/commas/whitespace, e.g. the
# "[\n 1337,\n 630,\n ...]" that indent=2 produces for a bbox. No nested
# brackets, so line_bboxes' outer array keeps one inner box per line.
_NUM_ARRAY_RE = re.compile(r"\[[\d\s,.\-]+\]")


def _collapse_numeric_array(match: re.Match) -> str:
    compact = re.sub(r"\s+", "", match.group(0))      # [1337,630,2331,1219]
    return compact.replace(",", ", ")                  # [1337, 630, 2331, 1219]


def _ordered_block(block: dict) -> dict:
    ordered = {k: block[k] for k in _BLOCK_KEY_ORDER if k in block}
    for k, v in block.items():                         # keep any extra keys, last
        if k not in ordered:
            ordered[k] = v
    return ordered


def normalize(data: dict) -> dict:
    """Return a copy of the translations dict with blocks in the readable order."""
    out = dict(data)
    pages = []
    for page in data.get("pages", []):
        page = dict(page)
        page["blocks"] = [_ordered_block(b) for b in page.get("blocks", [])]
        pages.append(page)
    out["pages"] = pages
    return out


def dumps(data: dict) -> str:
    text = json.dumps(normalize(data), ensure_ascii=False, indent=2)
    return _NUM_ARRAY_RE.sub(_collapse_numeric_array, text)


def write(path: Path, data: dict) -> None:
    """Atomic write: a Ctrl+C / power cut mid-write must never leave a
    truncated translations.json (it is the source of truth for the chapter)."""
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(dumps(data) + "\n", encoding="utf-8")
    os.replace(tmp, path)
