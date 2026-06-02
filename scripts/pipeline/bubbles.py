"""
Bubble merging — union detected text regions that belong to the same speech
bubble into a single block, so one bubble = one translation.

CTD/textline-merge can split a bubble into several regions; rendering each piece
separately caused duplicate / overlapping Ukrainian text. Unioning regions whose
bboxes are close fixes that.
"""

from __future__ import annotations


def _near(a: list[int], b: list[int], gap: int) -> bool:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    return (ax1 - gap < bx2 and bx1 - gap < ax2 and
            ay1 - gap < by2 and by1 - gap < ay2)


def merge_into_bubbles(blocks: list[dict], gap: int = 36) -> list[dict]:
    """Union blocks whose bboxes overlap when expanded by `gap` px.

    Each input block is a dict with at least ``bbox`` and ``line_bboxes`` and a
    text field (``text`` or ``original``). Returns merged blocks with the union
    bbox, concatenated line boxes, and text joined top-to-bottom / left-to-right.
    """
    if not blocks:
        return []

    parent = list(range(len(blocks)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(blocks)):
        for j in range(i + 1, len(blocks)):
            if _near(blocks[i]["bbox"], blocks[j]["bbox"], gap):
                parent[find(i)] = find(j)

    groups: dict[int, list[dict]] = {}
    for i, b in enumerate(blocks):
        groups.setdefault(find(i), []).append(b)

    merged = []
    for g in groups.values():
        g = sorted(g, key=lambda b: (b["bbox"][1], b["bbox"][0]))
        xs1 = [b["bbox"][0] for b in g]
        ys1 = [b["bbox"][1] for b in g]
        xs2 = [b["bbox"][2] for b in g]
        ys2 = [b["bbox"][3] for b in g]
        line_bboxes = [lb for b in g for lb in (b.get("line_bboxes") or [b["bbox"]])]
        text = " ".join((b.get("text") or b.get("original") or "").strip()
                        for b in g).strip()
        merged.append({
            "bbox": [min(xs1), min(ys1), max(xs2), max(ys2)],
            "line_bboxes": line_bboxes,
            "text": text,
            "source": g[0].get("source", "mit"),
        })
    # stable top-to-bottom, left-to-right order
    merged.sort(key=lambda b: (b["bbox"][1], b["bbox"][0]))
    return merged
