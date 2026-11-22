"""Read the vector content of a finishes drawing (PDF) with pdfium.

Everything is converted to Callidus sheet coordinates in metres: x from the
left edge of the sheet, y = 0 at the bottom edge and negative going up, so
rooms we find land exactly where Callidus draws the same sheet.

Curves are kept as their end and control points (no flattening). Floor
plans are almost all straight lines; the few curves are columns, door
swings and legend circles.
"""

from __future__ import annotations

import ctypes
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as R
from shapely.geometry import Polygon
from shapely.validation import make_valid

Colour = tuple[int, int, int]
PT_PER_INCH = 72
M_PER_INCH = 0.0254


@dataclass
class FillShape:
    order: int  # painting order on the page
    colour: Colour
    geom: object  # shapely geometry, sheet metres


@dataclass
class StrokeShape:
    order: int
    colour: Colour
    width_m: float
    lines: list[list[tuple[float, float]]]  # polylines, sheet metres


@dataclass
class TextItem:
    text: str
    box: tuple[float, float, float, float]  # x0, y0, x1, y1 in sheet metres (y0 < y1)

    @property
    def centre(self) -> tuple[float, float]:
        return ((self.box[0] + self.box[2]) / 2, (self.box[1] + self.box[3]) / 2)


@dataclass
class Drawing:
    path: Path
    page: int
    width_m: float
    height_m: float
    scale: float
    fills: list[FillShape] = field(default_factory=list)
    strokes: list[StrokeShape] = field(default_factory=list)
    texts: list[TextItem] = field(default_factory=list)


def _matrix(obj) -> tuple[float, ...]:
    m = R.FS_MATRIX()
    R.FPDFPageObj_GetMatrix(obj, ctypes.byref(m))
    return (m.a, m.b, m.c, m.d, m.e, m.f)


def _colour(getter, obj) -> Colour:
    r, g, b, a = (ctypes.c_uint() for _ in range(4))
    getter(obj, ctypes.byref(r), ctypes.byref(g), ctypes.byref(b), ctypes.byref(a))
    return (r.value, g.value, b.value)


def _subpaths(obj, tf) -> list[list[tuple[float, float]]]:
    a, b, c, d, e, f = _matrix(obj)
    out, cur = [], []
    for i in range(R.FPDFPath_CountSegments(obj)):
        seg = R.FPDFPath_GetPathSegment(obj, i)
        x, y = ctypes.c_float(), ctypes.c_float()
        R.FPDFPathSegment_GetPoint(seg, ctypes.byref(x), ctypes.byref(y))
        px, py = a * x.value + c * y.value + e, b * x.value + d * y.value + f
        if R.FPDFPathSegment_GetType(seg) == R.FPDF_SEGMENT_MOVETO:
            if len(cur) > 1:
                out.append(cur)
            cur = [tf(px, py)]
        else:
            cur.append(tf(px, py))
        if R.FPDFPathSegment_GetClose(seg):
            if len(cur) > 1:
                out.append(cur + [cur[0]])
            cur = []
    if len(cur) > 1:
        out.append(cur)
    return out


def _fill_geom(subpaths, even_odd: bool):
    """Filled area of a path. Even-odd: alternate subpaths cut holes. Non-zero
    winding: a subpath inside another and drawn the opposite way round is a
    hole (the usual way CAD exports a room with an island)."""
    polys = []
    for sp in subpaths:
        if len(sp) < 3:
            continue
        raw = Polygon(sp)
        ccw = raw.exterior.is_ccw
        g = make_valid(raw)
        if g.area > 0:
            polys.append((g, ccw))
    if not polys:
        return None
    if even_odd:
        geom = polys[0][0]
        for g, _ in polys[1:]:
            geom = geom.symmetric_difference(g)
        return geom
    polys.sort(key=lambda p: -p[0].area)
    placed: list[tuple[object, bool]] = []
    geom = None
    for g, ccw in polys:
        host = next((h for h in placed if h[0].contains(g.representative_point())), None)
        if host is not None and host[1] != ccw:
            geom = geom.difference(g)
        else:
            geom = g if geom is None else geom.union(g)
        placed.append((g, ccw))
    return geom


def _clip(obj, tf):
    """The object's clip region in sheet metres, or None. pdfium gives clip
    paths in page space already. Revit and similar exports often draw a room
    as a large rectangle clipped to the room shape."""
    cp = R.FPDFPageObj_GetClipPath(obj)
    if not cp:
        return None
    n = R.FPDFClipPath_CountPaths(cp)
    if n <= 0:
        return None
    region = None
    for k in range(n):
        subs, cur = [], []
        for i in range(R.FPDFClipPath_CountPathSegments(cp, k)):
            seg = R.FPDFClipPath_GetPathSegment(cp, k, i)
            x, y = ctypes.c_float(), ctypes.c_float()
            R.FPDFPathSegment_GetPoint(seg, ctypes.byref(x), ctypes.byref(y))
            if R.FPDFPathSegment_GetType(seg) == R.FPDF_SEGMENT_MOVETO:
                if len(cur) > 2:
                    subs.append(cur)
                cur = [tf(x.value, y.value)]
            else:
                cur.append(tf(x.value, y.value))
            if R.FPDFPathSegment_GetClose(seg):
                if len(cur) > 2:
                    subs.append(cur)
                cur = []
        if len(cur) > 2:
            subs.append(cur)
        g = _fill_geom(subs, even_odd=False)
        if g is None:
            continue
        region = g if region is None else region.intersection(g)
    return region


def _text(obj, textpage) -> str:
    n = R.FPDFTextObj_GetText(obj, textpage, None, 0)
    buf = ctypes.create_string_buffer(n * 2)
    R.FPDFTextObj_GetText(obj, textpage, ctypes.cast(buf, ctypes.POINTER(ctypes.c_ushort)), n)
    return buf.raw.decode("utf-16-le", errors="replace").replace("\x00", "").strip()


# scales architects use for plans; anything else ("1:3" in a note) is ignored
PLAN_SCALES = {20, 25, 50, 75, 100, 125, 150, 200, 250, 500}
_SCALE = re.compile(r"(?<![\d.,])1\s*(?::|/)\s*(\d{2,4})(?![\d.,])")


class ScaleNotFound(ValueError):
    """No drawing scale printed on the sheet."""


class PictureOnly(ValueError):
    """The PDF holds a picture of the drawing (scanned or flattened), not its linework."""


def _picture_only(pg) -> bool:
    paths = images = 0
    for obj in pg.get_objects():  # every depth, including nested groups
        if obj.type == R.FPDF_PAGEOBJ_PATH:
            paths += 1
            if paths > 50:
                return False
        elif obj.type == R.FPDF_PAGEOBJ_IMAGE:
            images += 1
    return images > 0


def read_scale(texts: list[TextItem], page_text: str = "") -> float | None:
    """The sheet's drawing scale: the most common '1:N' (also '1 : N', '1/N',
    'SCALE 1:100 @ A1') among plan scales. Text next to the word SCALE counts
    double. A tie goes to the larger drawing (smaller N): key plans are drawn
    smaller. ``page_text`` is the page's whole text, which also holds text
    that sits inside nested drawing groups."""
    found: Counter = Counter()
    for t in texts:
        for m in _SCALE.finditer(t.text):
            n = int(m.group(1))
            if n in PLAN_SCALES:
                found[n] += 2 if "SCALE" in t.text.upper() else 1
    if not found and page_text:
        for line in page_text.splitlines():
            for m in _SCALE.finditer(line):
                n = int(m.group(1))
                if n in PLAN_SCALES:
                    found[n] += 2 if "SCALE" in line.upper() else 1
    if not found:
        return None
    best = max(found.values())
    return float(min(n for n, c in found.items() if c == best))


def read_drawing(path: str | Path, page: int = 0, scale: float | None = None) -> Drawing:
    """Read fills, strokes and text from one page of a vector PDF."""
    path = Path(path)
    pdf = pdfium.PdfDocument(str(path))
    try:
        pg = pdf[page]
        if _picture_only(pg):
            raise PictureOnly(
                "This PDF is a picture of the drawing (scanned or saved as an image), not the drawing itself, "
                "so there are no finish areas or room tags to read. Ask the architect for the PDF exported "
                "from their drawing software."
            )
        x0, y0, x1, y1 = pg.get_mediabox()
        rot = pg.get_rotation() % 360
        uw, uh = x1 - x0, y1 - y0  # unrotated page size, points
        tp = pg.get_textpage()
        # Texts first, in points, so the scale can be read before converting.
        raw_texts = []
        for obj in pg.get_objects(max_depth=1):
            if obj.type == R.FPDF_PAGEOBJ_TEXT:
                t = _text(obj.raw, tp.raw)
                if t:
                    raw_texts.append((t, obj.get_bounds()))
        if scale is None:
            pts = [TextItem(t, b) for t, b in raw_texts]
            scale = read_scale(pts, tp.get_text_range())
            if scale is None:
                raise ScaleNotFound(
                    "No drawing scale (such as 1:100) is printed on this sheet. Enter the scale and read it again."
                )
        k = M_PER_INCH / PT_PER_INCH * scale

        def tf(px: float, py: float) -> tuple[float, float]:
            # page as displayed: /Rotate turns it clockwise
            ux, uy = px - x0, py - y0
            if rot == 90:
                ux, uy = uy, uw - ux
            elif rot == 180:
                ux, uy = uw - ux, uh - uy
            elif rot == 270:
                ux, uy = uh - uy, ux
            return (ux * k, -uy * k)

        dw, dh = (uh, uw) if rot in (90, 270) else (uw, uh)
        dwg = Drawing(path, page, dw * k, dh * k, scale)
        for t, (l, b, r, top) in raw_texts:
            (ax, ay), (bx, by) = tf(l, b), tf(r, top)
            dwg.texts.append(TextItem(t, (min(ax, bx), min(ay, by), max(ax, bx), max(ay, by))))
        for order, obj in enumerate(pg.get_objects(max_depth=1)):
            if obj.type != R.FPDF_PAGEOBJ_PATH:
                continue
            fm, st = ctypes.c_int(), ctypes.c_int()
            R.FPDFPath_GetDrawMode(obj.raw, ctypes.byref(fm), ctypes.byref(st))
            subs = _subpaths(obj.raw, tf)
            if not subs:
                continue
            if fm.value:
                g = _fill_geom(subs, even_odd=fm.value == R.FPDF_FILLMODE_ALTERNATE)
                if g is not None and not g.is_empty:
                    clip = _clip(obj.raw, tf)
                    if clip is not None and not clip.contains(g):
                        g = g.intersection(clip)
                if g is not None and not g.is_empty and g.area > 0:
                    dwg.fills.append(FillShape(order, _colour(R.FPDFPageObj_GetFillColor, obj.raw), g))
            if st.value:
                w = ctypes.c_float()
                R.FPDFPageObj_GetStrokeWidth(obj.raw, ctypes.byref(w))
                dwg.strokes.append(
                    StrokeShape(order, _colour(R.FPDFPageObj_GetStrokeColor, obj.raw), w.value * k, subs)
                )
        return dwg
    finally:
        pdf.close()
