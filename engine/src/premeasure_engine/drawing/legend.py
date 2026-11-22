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

    custom_code: str | None = None  # the drawing's own code (F1-E, M50-161), when it has one

    @property
    def code(self) -> str:
        if self.custom_code:
            return self.custom_code
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


def _read_heading_legend(dwg: Drawing) -> Legend:
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


LEGEND_MARK = re.compile(r"^\s*(LEGEND:?|FLOOR FINISHES( LEGEND)?|.*FINISHES LEGEND|FINISHES KEY)\s*$", re.I)


def _entry_text(dwg: Drawing, b, next_top: float) -> list[TextItem]:
    """Lines of text beside a swatch: they start just right of it and run down
    until the next swatch or a gap. Fragments on the same line are joined."""
    x0, y0, x1, y1 = b
    h = y1 - y0
    first = [t for t in dwg.texts if x1 < t.box[0] < x1 + max(1.5, 1.2 * h) and y0 - 0.25 * h <= t.centre[1] <= y1 + 0.25 * h]
    if not first:
        return []
    lx = min(t.box[0] for t in first)
    lh = sorted(t.box[3] - t.box[1] for t in first)[len(first) // 2]
    starts = sorted(
        (t for t in dwg.texts if abs(t.box[0] - lx) < 3 * lh and y0 - 0.25 * h <= t.box[1] < next_top - 0.2 * lh),
        key=lambda t: t.box[1],
    )
    rows = []
    for t in starts:
        if rows and t.box[1] - rows[-1].box[1] > 2.2 * lh:
            break
        rows.append(t)
    out = []
    for r in rows:
        line = [r]
        cands = sorted(
            (t for t in dwg.texts if t is not r and abs(t.box[1] - r.box[1]) < 0.4 * lh and t.box[0] > r.box[0]),
            key=lambda t: t.box[0],
        )
        for t in cands:
            if t.box[0] - line[-1].box[2] > 4 * lh:
                break
            line.append(t)
        out.extend(line)
    return out


CODE_TOKEN = re.compile(r"^[A-Z]{1,3}\d{1,3}(?:[-.][A-Z0-9]{1,4})?$")


def _row_texts(dwg: Drawing, y0: float, y1: float, x_from: float, x_to: float) -> list[TextItem]:
    return [t for t in dwg.texts if x_from <= t.box[0] <= x_to and y0 - 0.05 <= t.centre[1] <= y1 + 0.05]


def _swatch_candidates(dwg: Drawing):
    """Small boxes with text just to their right: the shape of a legend entry."""
    out = []
    for f in dwg.fills:
        x0, y0, x1, y1 = f.geom.bounds
        w, h = x1 - x0, y1 - y0
        if not (0.25 <= w <= 5.0 and 0.25 <= h <= 5.0 and 0.4 <= w / h <= 4.0):
            continue
        if f.geom.area < 0.6 * w * h * (0.78 if 0.9 < w / h < 1.1 else 1.0) * 0.9:
            continue  # not a solid box or disc
        right = [t for t in dwg.texts if x1 < t.box[0] < x1 + max(1.5, w) and y0 - 0.2 * h < t.centre[1] < y1 + 0.2 * h]
        right = right or [t for t in dwg.texts if x1 < t.box[0] < x1 + max(1.5, w) and abs(t.box[1] - y0) < 0.3 * h]
        if right:
            out.append((f, (x0, y0, x1, y1)))
    return out


def _columns(cands):
    """Group swatches that line up in a column with similar size; keep columns of 3 or more."""
    cols: list[list] = []
    for f, b in sorted(cands, key=lambda c: (c[1][0], c[1][1])):
        w = b[2] - b[0]
        for col in cols:
            cb = col[0][1]
            if abs(cb[0] - b[0]) < 0.25 * w and abs((cb[2] - cb[0]) - w) < 0.25 * w:
                if all(abs(b[1] - o[1][1]) > 0.5 * (b[3] - b[1]) for o in col):
                    col.append((f, b))
                break
        else:
            cols.append([(f, b)])
    return [c for c in cols if len(c) >= 3]


def _read_swatch_legend(dwg: Drawing) -> Legend:
    """A column of swatches, each with its text to the right (and maybe a code to the left)."""
    cols = _columns(_swatch_candidates(dwg))
    if not cols:
        return Legend([], None)
    # Prefer columns near the word LEGEND; otherwise the longest.
    marks = [t for t in dwg.texts if LEGEND_MARK.search(t.text)]

    def near(col):
        x0, y0 = col[0][1][0], min(b[1] for _, b in col)
        return min((abs(m.box[0] - x0) + abs(m.box[1] - y0) for m in marks), default=1e9)

    cols.sort(key=lambda c: (near(c) > 15, -len(c)))
    keep = [c for c in cols if near(c) <= 15] or cols[:1]
    # further swatch columns of the same legend (different swatch shapes) line up with it
    for c in cols:
        if c not in keep and any(abs(c[0][1][0] - k[0][1][0]) < 1.0 for k in keep):
            keep.append(c)
    entries: list[LegendEntry] = []
    boxes = []
    for col in keep:
        col = sorted(col, key=lambda c: c[1][1])
        tops = [b[1] for _, b in col]
        for k, (f, b) in enumerate(col):
            x0, y0, x1, y1 = b
            h = y1 - y0
            nxt = tops[k + 1] if k + 1 < len(col) else y1 + 3 * h
            texts = _entry_text(dwg, b, nxt)
            right_edge = max((t.box[2] for t in texts), default=x1 + 1.0)
            lefts = [t for t in dwg.texts if x0 - 2.5 < t.box[2] <= x0 + 0.05 and y0 - 0.2 * h < t.centre[1] < y1 + 0.2 * h]
            words = re.sub(r"\s+", " ", " ".join(t.text for t in texts)).strip()
            heading = HEADING.search(words)
            code = None
            kind = "skirting" if re.search(r"SKIRT", words, re.I) else "floor"
            if heading:
                kind = "floor" if heading.group(1).lower().startswith("floor") else "skirting"
                number = int(heading.group(2))
                words = HEADING.sub("", words, count=1).strip(" :")
            else:
                number = len(entries) + 1
                left_code = next((t.text.strip() for t in lefts if CODE_TOKEN.match(t.text.strip())), None)
                first = words.split(" ", 1)[0] if words else ""
                if left_code:
                    code = left_code
                elif re.match(r"^[A-Z]{1,3}\d{1,3}(?:-\d{1,4})?$", first):
                    code = first
            hatch = Counter(_strokes_in(dwg, (x0, y0, x1, y1)))
            hcols = [c for c, _ in hatch.most_common() if c != OUTLINE or len(hatch) == 1]
            e = LegendEntry(kind, number, words[:40], words, f.colour, hcols, b)
            if code:
                same = sum(1 for o in entries if o.custom_code and o.custom_code.split(" (")[0] == code)
                e.custom_code = code if same == 0 else f"{code} ({same + 1})"
            entries.append(e)
            boxes.append(box(x0 - 2.5, y0, right_edge, y1))
    area = None
    if boxes:
        x0, y0, x1, y1 = unary_union(boxes).bounds
        right = max((t.box[2] for t in dwg.texts if x0 <= t.box[0] <= x1 and y0 - 0.5 <= t.box[1] <= y1 + 0.5), default=x1)
        area = (x0 - 0.5, y0 - 1.5, right + 0.5, y1 + 1.0)
    return Legend(entries, area)


def read_legend(dwg: Drawing) -> Legend:
    """Read the finishes legend.

    Two layouts are known: headings such as "FLOOR FINISH TYPE 7:" with a
    swatch beside each (Cashel), and a column of swatches with the text, and
    often a code, beside each (Limerick, Shellybanks).
    """
    if sum(1 for t in dwg.texts if HEADING.search(t.text)) >= 3:
        return _read_heading_legend(dwg)
    return _read_swatch_legend(dwg)
