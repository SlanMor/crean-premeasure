"""Read the finishes legend: each finish type's swatch fill, hatch and text.

Architects' finishes legends put a swatch (usually a circle) beside a heading
such as "FLOOR FINISH TYPE 7:" with the product description below it. The
swatch shows the colour fill and hatch pattern used for that finish on the
plan. Some finishes have a hatch but no fill (carpet and matting on the
Cashel drawing), so both are recorded.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from shapely.geometry import box
from shapely.ops import unary_union

from .pdf_reader import Colour, Drawing, TextItem

HEADING = re.compile(r"(FLOOR\s+FINISH|SKIRTING)\s+TYPE\s+(\d+)", re.I)
OUTLINE = (0, 0, 0)


@dataclass
class LegendEntry:
    kind: str  # "floor" or "skirting"
    number: int
    heading: str
    description: str
    fill: Colour | None
    hatch: list[Colour] = field(default_factory=list)  # hatch colours, most used first
    swatch: tuple[float, float, float, float] | None = None

    @property
    def code(self) -> str:
        return f"{'FF' if self.kind == 'floor' else 'SK'}{self.number}"

    @property
    def label(self) -> str:
        return f"{self.code} - {self.description}" if self.description else self.code


@dataclass
class Legend:
    entries: list[LegendEntry]
    area: tuple[float, float, float, float] | None  # sheet metres, to keep off the plan

    def floors(self) -> list[LegendEntry]:
        return [e for e in self.entries if e.kind == "floor"]

    def skirtings(self) -> list[LegendEntry]:
        return [e for e in self.entries if e.kind == "skirting"]


def _description(head: TextItem, texts: list[TextItem], heads: list[TextItem]) -> str:
    """Text under a heading, in its column, down to the next heading."""
    hx0, hy0, _, hy1 = head.box
    lh = hy1 - hy0
    below_heads = [h.box[1] for h in heads if h is not head and abs(h.box[0] - hx0) < 2 * lh and h.box[1] > hy0]
    limit = min(below_heads) - 0.3 * lh if below_heads else hy0 + 8 * lh
    # the next column to the right, if it has headings level with this entry
    right = min(
        (h.box[0] for h in heads if h.box[0] > hx0 + 5 * lh and hy0 - 8 * lh < h.box[1] < limit),
        default=float("inf"),
    )
    rows = [t for t in texts if t is not head and abs(t.box[0] - hx0) < 1.5 * lh and hy0 < t.box[1] < limit]
    row_ys = [t.box[1] for t in rows]
    frags = [
        t for t in texts
        if t is not head and t not in rows and hx0 < t.box[0] < right - lh
        and any(abs(t.box[1] - y) < 0.6 * lh for y in row_ys)
    ]
    parts = sorted(rows + frags, key=lambda t: (round(t.box[1] / (0.6 * lh)), t.box[0]))
    rest = HEADING.sub("", head.text).strip(" :")
    return re.sub(r"\s+", " ", " ".join([rest] + [t.text for t in parts])).strip()


def _stroke_index(dwg: Drawing):
    """First point and colour of every stroke, as arrays, built once per drawing."""
    import numpy as np

    if getattr(dwg, "_stroke_index", None) is None:
        pts, cols = [], []
        for i, s in enumerate(dwg.strokes):
            x, y = s.lines[0][0]
            pts.append((x, y))
            cols.append(i)
        dwg._stroke_index = (np.array(pts), np.array(cols))
    return dwg._stroke_index


def _strokes_in(dwg: Drawing, bx) -> list:
    pts, idx = _stroke_index(dwg)
    x0, y0, x1, y1 = bx
    m = (pts[:, 0] > x0) & (pts[:, 0] < x1) & (pts[:, 1] > y0) & (pts[:, 1] < y1)
    return [dwg.strokes[i].colour for i in idx[m]]


def read_legend(dwg: Drawing) -> Legend:
    heads = [t for t in dwg.texts if HEADING.search(t.text)]
    entries: list[LegendEntry] = []
    boxes = []
    for h in heads:
        m = HEADING.search(h.text)
        kind = "floor" if m.group(1).lower().startswith("floor") else "skirting"
        hx0, hy0, hx1, hy1 = h.box
        size = hy1 - hy0
        # Swatch: a roughly square fill or stroke group just left of the heading,
        # its top level with the heading's top (y grows downwards visually as y
        # becomes less negative, so "top" is the smaller y).
        best = None
        for f in dwg.fills:
            x0, y0, x1, y1 = f.geom.bounds
            w, hgt = x1 - x0, y1 - y0
            if not (2 * size < w < 8 * size and 0.6 < w / max(hgt, 1e-9) < 1.6):
                continue
            if not (hx0 - 6 * size < x1 <= hx0 + 0.2 * size and abs(y0 - hy0) < 2.5 * size):
                continue
            d = abs(y0 - hy0) + (hx0 - x1)
            if best is None or d < best[0]:
                best = (d, f)
        if best:
            sw_box = best[1].geom.bounds
            fill = best[1].colour
        else:
            # No fill: look for a hatch block in the same place.
            sw_box = (hx0 - 5.5 * size, hy0 - 0.5 * size, hx0 - 0.5 * size, hy0 + 4.5 * size)
            fill = None
        hatch = Counter(_strokes_in(dwg, sw_box))
        cols = [c for c, _ in hatch.most_common() if c != OUTLINE or len(hatch) == 1]
        if fill is None and not cols:
            sw_box = None
        entries.append(
            LegendEntry(kind, int(m.group(2)), h.text, _description(h, dwg.texts, heads), fill, cols, sw_box)
        )
        boxes.append(box(*h.box))
        if sw_box:
            boxes.append(box(*sw_box))
    # Legend area: headings, swatches and description lines, padded.
    area = None
    if boxes:
        u = unary_union(boxes)
        x0, y0, x1, y1 = u.bounds
        desc_right = max(
            (t.box[2] for t in dwg.texts if x0 - 1 < t.box[0] < x1 + 1 and y0 - 1 < t.box[1] < y1 + 3), default=x1
        )
        pad = 1.0
        area = (x0 - pad, y0 - pad, max(x1, desc_right) + pad, y1 + 3 + pad)
    entries.sort(key=lambda e: (e.kind, e.number))
    return Legend(entries, area)
