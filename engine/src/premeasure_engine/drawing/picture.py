"""Read a finishes drawing that arrived as a picture (scanned, or saved as an image).

A picture has no linework or text to read, only pixels, so this works from
colour: the legend's swatches give each finish's colour, every pixel on the
plan is matched to the nearest swatch colour, and each patch of one finish
becomes a room. Accuracy comes from four things:

1. The picture is read at its own resolution, never resampled.
2. Room edges are found to a fraction of a pixel: straight runs of each
   outline are fitted with lines and snapped to the building's own angles,
   then pushed out to the middle of the soft edge pixels.
3. The size of a pixel comes from the sheet size and scale (read from the
   title block or given by the estimator), and is checked against the room
   areas printed on the drawing. If they disagree, every room says so.
4. Every room is marked for review: a picture is never "Verified".

Text (codes, room tags, printed areas) is read with Tesseract when it is
installed; without it, finishes are numbered and rooms are untagged.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median

import numpy as np

from .legend import Legend, LegendEntry
from .rooms import MAX_FURNITURE_HOLE, MIN_ROOM, Room

SHEETS_MM = {"A0": (1189, 841), "A1": (841, 594), "A2": (594, 420), "A3": (420, 297), "A4": (297, 210)}
EDGE_SHIFT = 0.25  # pixels to push outlines out, to the middle of the soft edge (set from the tests below)
SMOOTH = 5  # pixels: bumps and notches smaller than this are noise (stipple, soft edges)
MATCH = 14.0
PURE = 7.0  # a pixel showing the swatch colour itself (allowing for JPEG noise)  # colour distance (CIE Lab) within which a pixel belongs to a swatch colour
TAG_CONF = 75  # Tesseract confidence needed to take a word from a room tag
CODE = re.compile(r"\b([A-Z]{1,3}\d{0,2}-?\d{1,3}[A-Z]?)\b")
# room numbers: 02-PLNT-004, G.01, 1-04, L2.15 (OCR reads 0 as O in the digit groups)
ROOM_NO = re.compile(r"\b(\d{1,2}-[A-Z]{2,5}[-.][O0-9]{2,4}|[A-Z]{0,3}\d{1,2}[.\-][O0-9]{2,3}[A-Z]?)\b")
# printed areas always carry a decimal; OCR reads m2 as m2, m², mm', rn2...
AREA = re.compile(r"(\d{1,4}[.,]\d{1,2})\s*(?:m|mm|rn|nn|ni)\s*[2²*’'\"?]?(?![A-Za-z0-9])")


@dataclass
class Picture:
    image: np.ndarray  # RGB, as stored in the PDF (displayed orientation)
    sheet: str | None  # "A0" ...
    scale: float | None  # 1:N
    m_per_px: float | None = None  # real metres per pixel
    notes: list[str] = field(default_factory=list)

    @property
    def width_px(self) -> int:
        return self.image.shape[1]

    @property
    def height_px(self) -> int:
        return self.image.shape[0]


# --- the picture ------------------------------------------------------------------


def load_picture(pdf: str | Path, page: int = 0):
    """The page as an image at the picture's own resolution (PIL, RGB)."""
    import pypdfium2 as pdfium
    import pypdfium2.raw as R
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = None
    doc = pdfium.PdfDocument(str(pdf))
    try:
        pg = doc[page]
        biggest = None
        for obj in pg.get_objects():
            if obj.type == R.FPDF_PAGEOBJ_IMAGE:
                w, h = obj.get_px_size()
                if biggest is None or w * h > biggest[0] * biggest[1]:
                    biggest = (w, h)
        pw, ph = pg.get_size()  # as displayed
        if biggest is None:
            raise ValueError("no picture on this page")
        long_px = max(biggest)
        scale = long_px / max(pw, ph)  # render at the picture's own pixel size
        return pg.render(scale=scale).to_pil().convert("RGB")
    finally:
        doc.close()


def _ocr(img, psm: int = 6, upscale: int = 3, min_conf: int | None = None) -> str:
    """Text in the image. With ``min_conf``, only words Tesseract is at least
    that sure of (0-100), so smudges and hatching do not turn into words."""
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return ""
    if upscale > 1:
        img = img.resize((img.width * upscale, img.height * upscale), Image.LANCZOS)
    try:
        if min_conf is None:
            return " ".join(pytesseract.image_to_string(img, config=f"--psm {psm}").split())
        d = pytesseract.image_to_data(img, config=f"--psm {psm}", output_type=pytesseract.Output.DICT)
        return " ".join(w for w, c in zip(d["text"], d["conf"]) if w.strip() and float(c) >= min_conf)
    except Exception:
        return ""


def read_title(img) -> tuple[str | None, float | None]:
    """Sheet size and scale from the title block (the right-hand strip)."""
    from PIL import Image

    w, h = img.size
    strip = img.crop((int(w * 0.8), 0, w, h))
    text = _ocr(strip.convert("L"), psm=11, upscale=2).upper()
    sheet = None
    m = re.search(r"\bA([0-4OIL])\b", text)  # OCR reads A0 as AO and A1 as AI or AL
    if m:
        sheet = "A" + {"O": "0", "I": "1", "L": "1"}.get(m.group(1), m.group(1))
    scales = [int(n) for n in re.findall(r"(?<![\d.])1\s*[:/]\s*(\d{2,4})(?!\d)", text)]
    scales = [s for s in scales if s in (20, 25, 50, 75, 100, 125, 150, 200, 250, 500)]
    _ = Image
    return sheet, (float(min(scales, key=lambda s: (-scales.count(s), s))) if scales else None)


def sheet_m_per_px(sheet: str, scale: float, width_px: int, height_px: int) -> tuple[float, str | None]:
    """Real metres per pixel, and a warning if the picture is not the sheet's shape."""
    a, b = SHEETS_MM[sheet]
    long_px, short_px = max(width_px, height_px), min(width_px, height_px)
    per_px_long = a / long_px
    per_px_short = b / short_px
    note = None
    if abs(per_px_long / per_px_short - 1) > 0.01:
        note = (f"The picture is not the shape of an {sheet} sheet ({abs(per_px_long / per_px_short - 1):.1%} off): "
                f"it may be cropped. Check the scale against a known dimension.")
    return (per_px_long + per_px_short) / 2 * scale / 1000, note


# --- legend -----------------------------------------------------------------------


def _runs(mask_1d: np.ndarray, min_len: int):
    out, start = [], None
    for i, v in enumerate(np.append(mask_1d, False)):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if i - start >= min_len:
                out.append((start, i - 1))
            start = None
    return out


def find_swatches(rgb: np.ndarray, px_per_mm: float):
    """Legend swatches: small closed shapes (boxes, circles) 3 to 25 mm across,
    filled with a colour, stacked in a column with at least two others.
    Returns [(x0, y0, x1, y1, mask)] for each swatch interior, top to bottom."""
    import cv2

    dark = (rgb.max(axis=2) < 130).astype(np.uint8)  # ink: dark in every channel (a strong blue is not ink)
    contours, hier = cv2.findContours(dark, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    if hier is None:
        return []
    hier = np.asarray(hier).reshape(-1, 4)
    lo, hi = 3 * px_per_mm, 25 * px_per_mm
    found = []
    for k, c in enumerate(contours):
        if hier[k][3] == -1:
            continue  # not the inside of a closed outline
        x, y, w, h = cv2.boundingRect(c)
        if not (lo <= w <= hi and lo <= h <= hi and 0.4 <= w / h <= 2.5):
            continue
        area = cv2.contourArea(c)
        hull = cv2.contourArea(cv2.convexHull(c))
        if area < 0.6 * w * h or hull == 0 or area / hull < 0.9:
            continue
        mask = np.zeros((h, w), np.uint8)
        cv2.drawContours(mask, [c], -1, 1, -1, offset=(-x, -y))
        mask = cv2.erode(mask, np.ones((3, 3), np.uint8), iterations=2)
        inner = rgb[y : y + h, x : x + w][mask.astype(bool)].astype(int)
        if len(inner) < 10:
            continue
        light = inner[inner.max(1) >= 130]
        if len(light) < 0.5 * len(inner):
            continue
        colour = np.median(light, 0)
        if colour.min() > 246:
            continue  # white inside: a grid bubble, a white or hatch-only swatch
        found.append((x, y, x + w - 1, y + h - 1, mask))
    # swatches with no outline, or one too faint to close (filled circles, soft boxes):
    # compact patches of one colour
    solid = ((rgb.min(axis=2) < 240) & (rgb.max(axis=2) >= 130)).astype(np.uint8)
    solid = cv2.morphologyEx(solid, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))  # cut thin table lines away
    n, comp, stats, _ = cv2.connectedComponentsWithStats(solid, connectivity=4)
    for j in range(1, n):
        x, y, w, h, a = stats[j]
        if not (lo <= w <= hi and lo <= h <= hi and 0.4 <= w / h <= 2.5) or a < 0.7 * w * h:
            continue
        if any(f[0] - 3 <= x <= f[2] and f[1] - 3 <= y <= f[3] for f in found):
            continue  # already found by its outline
        mask = (comp[y : y + h, x : x + w] == j).astype(np.uint8)
        mask = cv2.erode(mask, np.ones((3, 3), np.uint8), iterations=2)  # off the soft rim
        px = rgb[y : y + h, x : x + w][mask.astype(bool)].astype(int)
        if len(px) < 10:
            continue
        near = np.abs(px - np.median(px, 0)).max(1) <= 30
        if near.mean() < 0.75:
            continue  # not one colour (a code written inside the swatch takes up to a fifth)
        found.append((x, y, x + w - 1, y + h - 1, mask))
    # keep shapes stacked in a column with at least two others of about the same size
    keep = []
    for f in found:
        cx, size = (f[0] + f[2]) / 2, f[2] - f[0]
        mates = [g for g in found if abs((g[0] + g[2]) / 2 - cx) < 0.35 * size and abs((g[2] - g[0]) - size) < 0.35 * size]
        if len(mates) >= 3:
            keep.append(f)
    keep.sort(key=lambda b: (round(b[0] / (10 * px_per_mm)), b[1]))
    return keep


HEADING = re.compile(r"\s((?:FLOOR\s+|WALL\s+)?\S*(?:FINISH|IRTING|OVING)\w*)\s+TYPES?\b\s*(\d+)?", re.I)


def _clean_text(text: str) -> tuple[str, list[str]]:
    """Legend text as the drawing says it: no heading (it becomes the code), and
    none of the stray marks OCR makes of table lines. Text that runs on into the
    next entry's heading is cut there; that entry's code is returned too, as it
    has no swatch of its own the picture reader could find (often hatching)."""
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    text = re.sub(r"[|_~]+", " ", text)
    text = re.sub(r"^\W*(?:FLOOR\s+FINISH|SKIRTING|COVING|WALL\s+FINISH)\s+TYPE\s+\d+\s*[:.]?\s*", "", text.strip(),
                  flags=re.I)
    text = " " + " ".join(text.split())
    others = []
    for m in HEADING.finditer(text):
        if m.group(2):
            others.append(("SK" if re.search(r"IRTING|OVING", m.group(1), re.I) else "FF") + m.group(2))
    cut = HEADING.search(text)
    return (text[: cut.start()] if cut else text).strip(), others


def read_legend_picture(img, px_per_mm: float) -> tuple[Legend, list[dict]]:
    """Finishes from the legend swatches: colour, code and text. Skirting entries
    are kept apart (their thin strips are not rooms)."""
    rgb = np.asarray(img)
    boxes = find_swatches(rgb, px_per_mm)
    entries, info = [], []
    for i, (x0, y0, x1, y1, mask) in enumerate(boxes):
        inner = rgb[y0 : y1 + 1, x0 : x1 + 1][mask.astype(bool)].astype(int)
        light = inner[inner.max(1) >= 130]
        colour = tuple(int(c) for c in np.median(light, 0))
        stipple = float((inner.max(1) < 130).mean())
        # the entry's text: to the right of the swatch, down to the next swatch below it
        below = [b[1] for b in boxes if b[1] > y1 and abs(b[0] - x0) < (x1 - x0)]
        bottom = min(below) - 2 if below else y1 + int(12 * px_per_mm)
        top = max(0, y0 - int(2 * px_per_mm))
        text = _ocr(img.crop((x1 + 2, top, min(img.width, x1 + int(95 * px_per_mm)), bottom)).convert("L"), psm=6)
        inside = _ocr(img.crop((x0, y0, x1 + 1, y1 + 1)).convert("L"), psm=7, upscale=4)
        left = _ocr(img.crop((max(0, x0 - int(22 * px_per_mm)), y0 - 2, x0 - 2, y1 + 3)).convert("L"), psm=7)
        if not _reads_like_words(text):
            continue  # a pattern on the plan that happens to look like a swatch
        up = text.upper()
        first_floor, first_skirt = up.find("FLOOR"), min([i for i in (up.find("SKIRT"), up.find("COVING")) if i >= 0],
                                                         default=-1)
        kind = "skirting" if first_skirt >= 0 and (first_floor < 0 or first_skirt < first_floor) else "floor"
        heading = re.search(r"(FLOOR FINISH|SKIRTING)\s+TYPE\s*(\d+)", up)
        code = (
            (("FF" if heading.group(1) == "FLOOR FINISH" else "SK") + heading.group(2) if heading else None)
            or _find_code(left, last=True) or _find_code(inside) or _find_code(text.split("|")[0][:12])
        )
        coded = code is not None
        code = code or ("S" if kind == "skirting" else "P") + str(i + 1)
        info.append({"box": (x0, y0, x1, y1), "colour": colour, "stipple": stipple, "code": code, "kind": kind,
                     "text": text, "coded": coded})
    # the legend is the columns of swatches that carry codes; other columns are patterns on the plan
    columns: dict[int, list[dict]] = {}
    for f in info:
        columns.setdefault(round(f["box"][0] / (5 * px_per_mm)), []).append(f)
    if any(sum(f["coded"] for f in col) >= 3 for col in columns.values()):
        info = [f for col in columns.values() if sum(f["coded"] for f in col) >= 0.5 * len(col) for f in col]
    info.sort(key=lambda f: (round(f["box"][0] / (10 * px_per_mm)), f["box"][1]))
    boxes = [f["box"] + (None,) for f in info]
    unread: list[str] = []
    for f in info:
        text, others = _clean_text(f["text"])
        unread += others
        entries.append(LegendEntry(f["kind"], len(entries) + 1, "", text[:300], f["colour"], [],
                                   custom_code=f["code"]))
    seen: dict[str, int] = {}
    for e in entries:  # one code per finish: a repeat is a misread, number it apart
        if e.custom_code in seen:
            seen[e.custom_code] += 1
            e.custom_code = f"{e.custom_code} ({seen[e.custom_code]})"
        else:
            seen[e.custom_code] = 1
    area = None
    if boxes:
        area = (min(b[0] for b in boxes) - int(3 * px_per_mm), min(b[1] for b in boxes) - int(10 * px_per_mm),
                img.width, max(b[3] for b in boxes) + int(15 * px_per_mm))
    legend = Legend(entries, area)
    codes = {e.code for e in entries}
    # finishes named on the legend with no swatch found: hatching or black linework only
    legend.unread = sorted(set(c for c in unread if c not in codes), key=lambda c: (c[:2], int(c[2:])))
    return legend, info


def _reads_like_words(text: str) -> bool:
    """Legend text, not OCR noise from a pattern on the plan."""
    words = re.findall(r"\b[A-Z][A-Za-z]{3,}\b|\b[a-z]{4,}\b", text)
    tokens = text.split()
    return len(words) >= 1 and len(words) >= 0.35 * max(1, len(tokens))


def _find_code(text: str, last: bool = False) -> str | None:
    """A finish code as legends write them: FL-401, F1-A, M50-150. Tesseract
    sometimes drops the hyphen (FL401); that form is accepted and mended."""
    t = text.upper().replace("—", "-").replace("–", "-").replace(" -", "-").replace("- ", "-")
    for pattern, fmt in (
        (r"\b([A-Z]{1,3}\d{0,2}-[A-Z0-9]{1,4})\b", lambda m: m.group(1)),
        (r"\b([A-Z]{2})(\d{3}[A-Z]?)\b", lambda m: f"{m.group(1)}-{m.group(2)}"),
        (r"\b([A-Z]\d{1,2})\b", lambda m: m.group(1)),  # F5, F6: only written beside the swatch
    ):
        if pattern.startswith(r"\b([A-Z]\d") and not last:
            continue
        hits = [fmt(m) for m in re.finditer(pattern, t)]
        if hits:
            return hits[-1] if last else hits[0]
    return None


# --- rooms ------------------------------------------------------------------------


def _lab(rgb: np.ndarray) -> np.ndarray:
    import cv2

    return cv2.cvtColor(rgb.astype(np.uint8), cv2.COLOR_RGB2LAB).astype(np.float32) * np.array(
        [100 / 255, 1, 1], np.float32) - np.array([0, 128, 128], np.float32)


def label_pixels(rgb: np.ndarray, colours: list[tuple[int, int, int]]) -> np.ndarray:
    """Index of the nearest swatch colour for each pixel, or -1 when none is close.
    White and black count as colours too, so pale finishes are not taken for paper."""
    palette = list(colours) + [(255, 255, 255), (0, 0, 0)]
    lab = _lab(rgb)
    pal = _lab(np.array([palette], np.uint8))[0]
    best = np.full(rgb.shape[:2], -1, np.int16)
    best_d = np.full(rgb.shape[:2], np.inf, np.float32)
    for i, c in enumerate(pal):
        d = np.sqrt(((lab - c) ** 2).sum(-1))
        closer = d < best_d
        best[closer] = i
        best_d[closer] = d[closer]
    best[best_d > MATCH] = -1
    best[best >= len(colours)] = -1  # white and black are not finishes
    return best


def _straighten(contour: np.ndarray, angle: float, tol_deg: float = 4.0) -> list[tuple[float, float]]:
    """Fit lines to the straight runs of an outline and meet them at corners,
    snapping runs within ``tol_deg`` of the building's angle (or square to it)."""
    import cv2

    approx = np.asarray(cv2.approxPolyDP(contour, 2.5, True)).reshape(-1, 2).astype(float)
    if len(approx) < 3:
        return []
    pts = np.asarray(contour).reshape(-1, 2).astype(float)
    # assign outline points to segments of the simplified outline
    n = len(approx)
    idx = [int(np.argmin(((pts - p) ** 2).sum(1))) for p in approx]
    lines = []
    for k in range(n):
        a, b = idx[k], idx[(k + 1) % n]
        seg = pts[a : b + 1] if a <= b else np.vstack([pts[a:], pts[: b + 1]])
        if len(seg) < 2:
            seg = np.array([approx[k], approx[(k + 1) % n]])
        p0, p1 = approx[k], approx[(k + 1) % n]
        theta = math.atan2(p1[1] - p0[1], p1[0] - p0[0])
        # snap the direction to the building grid if close
        rel = (theta - angle) % (math.pi / 2)
        if min(rel, math.pi / 2 - rel) < math.radians(tol_deg):
            snapped = angle + round((theta - angle) / (math.pi / 2)) * (math.pi / 2)
            d = np.array([math.cos(snapped), math.sin(snapped)])
            nrm = np.array([-d[1], d[0]])
            c = float(np.median(seg @ nrm))  # offset of the line: robust to stray pixels
            lines.append((nrm, c))
        else:
            mean = seg.mean(0)
            _, _, vt = np.linalg.svd(seg - mean) if len(seg) > 2 else (None, None, np.array([[p1 - p0]]))
            d = vt[0] / (np.linalg.norm(vt[0]) or 1)
            nrm = np.array([-d[1], d[0]])
            lines.append((nrm, float(mean @ nrm)))
    out = []
    for k in range(n):
        (n1, c1), (n2, c2) = lines[k - 1], lines[k]
        m = np.array([n1, n2])
        corner = approx[k]
        if abs(np.linalg.det(m)) > 1e-3:
            fitted = np.linalg.solve(m, [c1, c2])
            if np.hypot(*(fitted - corner)) <= 4:  # nearly parallel lines meet far away: keep the found corner
                corner = fitted
        out.append(tuple(corner))
    return out


def _square_up(mask: np.ndarray, angle: float, k: int) -> np.ndarray:
    """Remove bumps and notches smaller than k pixels, with a square turned to
    the building's angle, so straight walls stay straight and corners square."""
    import cv2

    if k < 2:
        return mask
    h, w = mask.shape
    deg = math.degrees(angle)
    if abs(deg) > 0.2:
        rot = cv2.getRotationMatrix2D((w / 2, h / 2), deg, 1.0)
        m = cv2.warpAffine(mask, rot, (w, h), flags=cv2.INTER_NEAREST)
    else:
        m = mask
    sq = np.ones((k, k), np.uint8)
    m = cv2.morphologyEx(cv2.morphologyEx(m, cv2.MORPH_OPEN, sq), cv2.MORPH_CLOSE, sq)
    if abs(deg) > 0.2:
        back = cv2.getRotationMatrix2D((w / 2, h / 2), -deg, 1.0)
        m = cv2.warpAffine(m, back, (w, h), flags=cv2.INTER_NEAREST)
    return m


def building_angle(mask: np.ndarray) -> float:
    """The main wall direction (radians), from the long straight edges."""
    import cv2

    edges = cv2.Canny(mask.astype(np.uint8) * 255, 50, 150)
    lines = cv2.HoughLinesP(edges, 1, math.pi / 720, 80, minLineLength=60, maxLineGap=3)
    if lines is None:
        return 0.0
    hist: dict[int, float] = {}
    for x0, y0, x1, y1 in np.asarray(lines).reshape(-1, 4):
        a = math.degrees(math.atan2(y1 - y0, x1 - x0)) % 90
        key = int(round(a * 4)) % 360
        hist[key] = hist.get(key, 0) + math.hypot(x1 - x0, y1 - y0)
    best = max(hist, key=hist.get) / 4
    if best > 45:
        best -= 90  # turn the short way: -45 to 45 degrees
    return math.radians(best)


def _unmatched_colour(rgb, labels, chroma, not_white, legend: Legend, m: float) -> dict:
    """Coloured areas on the plan that match no legend colour, as {colour: m2}:
    a finish whose swatch was not found, or a plan drawn in lighter tints than its
    legend. Not measured, so the estimator is told."""
    import cv2

    left = ((labels < 0) & not_white & (chroma > 8)).astype(np.uint8)
    if legend.area:
        x0, y0, x1, y1 = (int(v) for v in legend.area)
        left[max(0, y0) : y1, max(0, x0) : x1] = 0
    left = cv2.morphologyEx(left, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))  # not lines or text
    n, comp, stats, _ = cv2.connectedComponentsWithStats(left, connectivity=4)
    out: dict[tuple[int, int, int], float] = {}
    for j in range(1, n):
        a = float(stats[j, cv2.CC_STAT_AREA]) * m * m
        if a < MIN_ROOM:
            continue
        x, y, w, h = stats[j, :4]
        px = rgb[y : y + h, x : x + w][comp[y : y + h, x : x + w] == j]
        colour = tuple(min(255, int(round(v / 8) * 8)) for v in np.median(px, 0))
        key = next((k for k in out if max(abs(p - q) for p, q in zip(k, colour)) <= 12), colour)
        out[key] = round(out.get(key, 0.0) + a, 2)
    return out


def find_rooms_picture(pic: Picture, legend: Legend, min_opening: float = MAX_FURNITURE_HOLE):
    """Rooms (in sheet metres, as the vector reader gives them) and a report."""
    import cv2
    from shapely.geometry import Polygon
    from shapely.validation import make_valid

    rgb = pic.image
    h, w = rgb.shape[:2]
    floors = [e for e in legend.floors() if e.fill]
    strips = [e for e in legend.skirtings() if e.fill]
    # A median filter takes out stipple, hatching dots and text so each pixel shows
    # its finish's base colour. It also blurs edges, so each finish is first shrunk
    # by the filter's reach (which also drops slivers of mixed colour along edges)...
    r = max(1, min(4, int(round(0.05 / pic.m_per_px))))
    smooth = cv2.medianBlur(rgb, 2 * r + 1)
    first = label_pixels(smooth, [e.fill for e in floors] + [e.fill for e in strips])
    if legend.area:
        x0, y0, x1, y1 = (int(v) for v in legend.area)
        first[max(0, y0) : y1, max(0, x0) : x1] = -1  # the legend is not plan
    labels = np.full(first.shape, -1, np.int16)
    disc = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
    raw_lab = _lab(rgb)
    win = max(5, int(round(0.4 / pic.m_per_px)) | 1)  # 40 cm neighbourhood
    for i, e in enumerate(floors):
        seed = cv2.erode((first == i).astype(np.uint8), disc).astype(bool)
        if not seed.any():
            continue
        # A real finish area shows the swatch colour itself in most of its pixels.
        # Textures that only average out to it (gravel, wall hatching, shading)
        # do not, so they never start a room.
        target = _lab(np.array([[e.fill]], np.uint8))[0, 0]
        pure = (np.sqrt(((raw_lab - target) ** 2).sum(-1)) <= PURE).astype(np.float32)
        local = cv2.boxFilter(pure, -1, (win, win))
        core = (seed & (local >= 0.4)).astype(np.uint8)
        # near an edge the neighbourhood is half wall, so give the core back its edge
        core = cv2.dilate(core, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (win, win))).astype(bool) & seed
        labels[core & (labels < 0)] = i
    # ...then grown back over the real pixels, out to the wall face. Growth crosses
    # skirting strips (their legend colour) freely, up to 20 cm, and anything else
    # only within 2 pixels of a strip or of the finish itself: soft edges and the
    # wall line, but not the inside of a wall, whatever colour walls are drawn.
    m = pic.m_per_px
    ink = rgb.max(axis=2) < 130  # text, grid, stipple and door lines drawn over a fill
    not_white = rgb.min(axis=2) < 240
    strip_px = np.zeros(first.shape, bool)
    for e in strips:
        t = _lab(np.array([[e.fill]], np.uint8))[0, 0]
        strip_px |= np.sqrt(((raw_lab - t) ** 2).sum(-1)) <= MATCH
    # Skirting bands are drawn in colour (often darker than the legend swatch once
    # lines run through them); walls are white, grey or black. So any coloured pixel
    # can be crossed, and grey or black ones only right next to colour or the finish.
    chroma = np.hypot(raw_lab[..., 1], raw_lab[..., 2])
    # Pale tints (L above 75, little colour) are wall fills on some drawings, not bands.
    # The floor's own colour is not a band: the wall line beside a floor is only
    # crossed where a skirting band runs between them.
    floor_like = np.zeros(first.shape, bool)
    for e in floors:
        t = _lab(np.array([[e.fill]], np.uint8))[0, 0]
        floor_like |= np.sqrt(((raw_lab - t) ** 2).sum(-1)) <= MATCH
    # Near-black pixels with a tint are JPEG ringing along a black wall, not a band.
    lum = raw_lab[..., 0]
    band = strip_px | (not_white & ~floor_like & (lum >= 35) & ((chroma > 15) | ((chroma > 6) & (lum < 75))))
    seeded = labels >= 0
    near_seed = cv2.distanceTransform((~seeded).astype(np.uint8), cv2.DIST_L2, 3) <= 2 + r
    near_band = cv2.distanceTransform((~band).astype(np.uint8), cv2.DIST_L2, 3) <= 3
    # beyond a band only the line drawn along it (dark ink) is part of the room
    # A wall drawn solid (poché) is not crossed at all: only thin lines are, which
    # are drawn half over the floor. Solid means at least 15 cm across (heavier
    # than any line, as thick as a drawn wall).
    wk = max(3, int(round(0.15 / m)))
    solid = cv2.morphologyEx((ink & ~band).astype(np.uint8), cv2.MORPH_OPEN, np.ones((wk, wk), np.uint8)).astype(bool)
    # the line along a band is crossed even when heavy; a one-pixel "band" is only
    # the soft edge of a fill, so it does not count for that
    wide = cv2.morphologyEx(band.astype(np.uint8), cv2.MORPH_OPEN, np.ones((2, 2), np.uint8)).astype(np.uint8)
    beside_band = cv2.distanceTransform(1 - wide, cv2.DIST_L2, 3) <= 3
    free = (labels < 0) & not_white & (band | (beside_band & ink) | (~solid & (near_seed | (near_band & ink))))
    if legend.area:
        free[max(0, y0) : y1, max(0, x0) : x1] = False
    steps = r + max(1, int(round(0.20 / m)))
    cross = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))
    for _ in range(steps):
        grown = labels.copy()
        for i in range(len(floors)):
            ring = cv2.dilate((labels == i).astype(np.uint8), cross).astype(bool) & free & (grown < 0)
            grown[ring] = i
        free &= grown < 0
        labels = grown
    any_floor = labels >= 0
    angle = building_angle(any_floor)
    unknown = _unmatched_colour(rgb, labels, chroma, not_white, legend, m)
    min_px = MIN_ROOM / (m * m)
    hole_px = min_opening / (m * m)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    rooms, report = [], {"unknown_fills": unknown, "manual_finishes": [], "picture": True}
    for i, e in enumerate(floors):
        mask = (labels == i).astype(np.uint8)
        if mask.sum() < min_px:
            continue
        # lines and text drawn over the finish belong to it; white wall infill never does
        closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        mask = mask | (closed.astype(bool) & ink).astype(np.uint8)
        mask = _square_up(mask, angle, int(SMOOTH))
        n, comp, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=4)
        target = _lab(np.array([[e.fill]], np.uint8))[0, 0]
        for j in range(1, n):
            if stats[j, cv2.CC_STAT_AREA] < min_px:
                continue
            part = (comp == j).astype(np.uint8)
            # most of a real finish area shows the swatch colour itself; grey walls,
            # hatching and shading only average out to it
            inside = part.astype(bool)
            pure = float((np.sqrt(((raw_lab[inside] - target) ** 2).sum(-1)) <= MATCH).mean())
            if pure < 0.35:
                report.setdefault("rejected_m2", 0.0)
                report["rejected_m2"] += float(inside.sum()) * m * m
                continue
            contours, hier = cv2.findContours(part, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
            if hier is None:
                continue
            hier = np.asarray(hier).reshape(-1, 4)
            for k, c in enumerate(contours):
                if hier[k][3] != -1:
                    continue  # holes are handled with their outline
                outer = _straighten(c, angle)
                if len(outer) < 3:
                    continue
                holes = []
                child = hier[k][2]
                while child != -1:
                    hc = contours[child]
                    if cv2.contourArea(hc) >= hole_px:
                        hp = _straighten(hc, angle)
                        if len(hp) >= 3:
                            holes.append(hp)
                    child = hier[child][0]
                poly = make_valid(Polygon(outer, holes))
                if poly.geom_type != "Polygon":
                    parts = [g for g in getattr(poly, "geoms", []) if g.geom_type == "Polygon"]
                    if not parts:
                        continue
                    poly = max(parts, key=lambda g: g.area)
                poly = poly.buffer(EDGE_SHIFT, join_style=2)  # to the middle of the soft edge
                geom = Polygon(
                    [(x * m, (y - h) * m) for x, y in poly.exterior.coords],
                    [[(x * m, (y - h) * m) for x, y in r.coords] for r in poly.interiors],
                )
                if geom.area < MIN_ROOM:
                    continue
                r = Room(geom, e, "picture", e.fill)
                r.vertices = [(x, y) for x, y in geom.exterior.coords[:-1]]
                r.edge_skirting = [None] * len(r.vertices)
                r.flag("read from a picture of the drawing: check the outline")
                if geom.interiors:
                    r.flag(f"{len(geom.interiors)} opening(s) or other finish inside the room: the Callidus room is the "
                           "outer outline, so cut them out in Callidus")
                rooms.append(r)
    return rooms, report


# --- tags and the scale check ---------------------------------------------------


def read_room_tags(pic: Picture, rooms: list[Room]) -> None:
    """Room numbers, names and printed areas from the text inside each room."""
    from PIL import Image

    m, h = pic.m_per_px, pic.height_px
    gray = Image.fromarray(pic.image).convert("L")
    for r in rooms:
        x0, y0, x1, y1 = r.geom.bounds
        box = (max(0, int(x0 / m)), max(0, int(y0 / m + h)), min(pic.width_px, int(x1 / m) + 1), min(h, int(y1 / m + h) + 1))
        if box[2] - box[0] < 10 or box[3] - box[1] < 10 or (box[2] - box[0]) * (box[3] - box[1]) > 4_000_000:
            continue
        # grey, not black and white: small text on a coloured fill survives best as it is
        text = _ocr(gray.crop(box), psm=11, upscale=4, min_conf=TAG_CONF)
        if not text:
            continue
        areas = [float(a.replace(",", ".")) for a in AREA.findall(text)]
        rest = AREA.sub(" ", text)
        nos = sorted({_room_no(n) for n in ROOM_NO.findall(rest.upper())})
        if nos:
            r.codes = nos[:4]
            # a name only when one tag was read: with several, the words cannot be told apart
            words = [w for w in re.findall(r"[A-Za-z][A-Za-z&/]*", ROOM_NO.sub(" ", rest))
                     if len(w) >= 3 or w.upper() in ("WC", "PS", "DB")]
            r.names = [" ".join(words)[:60]] if len(nos) == 1 and words else []
        if areas:
            r.printed_area = max(areas)  # type: ignore[attr-defined]


def _room_no(text: str) -> str:
    m = re.match(r"(.*[.\-])([O0-9]+[A-Z]?)$", text)
    return m.group(1) + m.group(2).replace("O", "0") if m else text


def check_scale(rooms: list[Room]) -> tuple[float | None, int]:
    """How far the measured areas are from the areas printed on the drawing:
    printed / measured over rooms of 5 m2 and more (printed areas are rounded,
    so small rooms say little). Rooms that disagree with the rest (two rooms
    run together, a misread tag) are left out; at least three rooms, and at
    least half of those with a printed area, must agree. Returns (ratio, rooms
    used), or (None, n) when they do not."""
    ratios = [r.printed_area / r.area for r in rooms
              if getattr(r, "printed_area", None) and r.printed_area >= 5 and r.area > 0]
    if len(ratios) < 3:
        return None, len(ratios)
    mid = median(ratios)
    agree = [x for x in ratios if abs(x / mid - 1) < ROOM_CHECK]
    if len(agree) < 3 or len(agree) * 2 < len(ratios):
        return None, len(agree)
    return median(agree), len(agree)


# --- the whole reading ---------------------------------------------------------------

AREA_CHECK = 0.03  # printed and measured areas this far apart (median over the sheet) mean the scale is wrong
ROOM_CHECK = 0.05  # and this far apart for one room mean its outline needs a look


class SheetNeeded(ValueError):
    """A picture of a drawing can only be measured once its sheet size and scale are known."""


class NoLegend(ValueError):
    """No finish legend (coloured swatches with descriptions) found on the picture."""


@dataclass
class PictureReading:
    rooms: list[Room]
    legend: Legend
    report: dict
    sheet: str
    scale: float
    width_m: float
    height_m: float
    image_size: tuple[int, int]


def page_sheet(pdf: str | Path, page: int = 0) -> str | None:
    """The ISO sheet the PDF page is the size of, if any. A scan is often saved
    on a page of another size, so this is only a starting point."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(pdf))
    try:
        w, h = doc[page].get_size()
    finally:
        doc.close()
    long_mm, short_mm = max(w, h) / 72 * 25.4, min(w, h) / 72 * 25.4
    for name, (a, b) in SHEETS_MM.items():
        if abs(long_mm / a - 1) < 0.015 and abs(short_mm / b - 1) < 0.015:
            return name
    return None


def _measure(img, sheet: str, scale: float, min_opening: float):
    m, shape_note = sheet_m_per_px(sheet, scale, img.width, img.height)
    pic = Picture(np.asarray(img), sheet, scale, m)
    legend, _ = read_legend_picture(img, max(img.size) / SHEETS_MM[sheet][0])
    if not legend.floors():
        raise NoLegend(
            "This PDF is a picture of the drawing, and no finish legend (coloured swatches with their "
            "descriptions) could be found on it, so the finishes cannot be told apart. Ask the architect for "
            "the PDF exported from their drawing software."
        )
    rooms, report = find_rooms_picture(pic, legend, min_opening)
    read_room_tags(pic, rooms)
    return m, shape_note, legend, rooms, report


def _unclear_codes(legend: Legend) -> list[str]:
    """Codes that do not follow the pattern most of the legend's codes follow
    (F1-A, F1-B ... and then P22): most likely misread."""
    from collections import Counter

    codes = [e.code for e in legend.entries]
    shape = {c: re.sub(r"[A-Z]", "A", re.sub(r"\d+", "9", re.sub(r"(?<=\d)[A-Z]$", "", c.split(" (")[0])))
             for c in codes}  # FL-601A has the shape of FL-601
    if len(codes) < 4:
        return [c for c in codes if " (" in c]
    common, n = Counter(shape.values()).most_common(1)[0]
    if n < 0.6 * len(codes):
        return [c for c in codes if " (" in c]
    return [c for c in codes if shape[c] != common or " (" in c]


def _sheet_matching(ratio: float, m: float, scale: float, img) -> str | None:
    """The sheet size at which the printed areas agree with the measured ones."""
    want = m * math.sqrt(ratio)
    for name in SHEETS_MM:
        m_s, _ = sheet_m_per_px(name, scale, img.width, img.height)
        if abs(m_s / want - 1) < AREA_CHECK / 2:
            return name
    return None


def read_picture(pdf: str | Path, page: int = 0, sheet: str | None = None, scale: float | None = None,
                 min_opening: float = MAX_FURNITURE_HOLE) -> PictureReading:
    """Rooms and finishes from a picture of a finishes drawing.

    The size of a pixel comes from the sheet size (A0..A4) and the scale (1:N).
    Each is taken from what the estimator entered, else from the title block.
    A sheet size with no other source (the PDF page size, or none) is only used
    when the room areas printed on the drawing confirm it; otherwise the
    estimator is asked. Nothing is measured from a guessed size."""
    img = load_picture(pdf, page)
    read_sheet, read_scale = read_title(img) if not (sheet and scale) else (None, None)
    source = {"sheet": "entered" if sheet else ("title block" if read_sheet else None),
              "scale": "entered" if scale else ("title block" if read_scale else None)}
    sheet = sheet or read_sheet
    scale = scale or read_scale
    if not scale:
        raise SheetNeeded(
            "This PDF is a picture of the drawing (scanned or saved as an image), so it is measured from its "
            "colours. The scale could not be read from the title block. Enter the sheet size and scale and "
            "read it again."
        )
    trial = sheet if sheet in SHEETS_MM else (page_sheet(pdf, page) or "A1")
    try:
        m, shape_note, legend, rooms, report = _measure(img, trial, scale, min_opening)
    except NoLegend:
        if source["sheet"] is not None:
            raise
        raise SheetNeeded(  # the legend is looked for by the size of its swatches, so a wrong sheet hides it
            "This PDF is a picture of the drawing (scanned or saved as an image), so it is measured from its "
            "colours. The sheet size (A0, A1...) is not on the title block. Enter the sheet size and read it again."
        ) from None
    ratio, used = check_scale(rooms)
    if source["sheet"] is None:
        # no sheet size from the estimator or the title block: only the printed areas can give it
        found = _sheet_matching(ratio, m, scale, img) if ratio is not None else None
        if found is None:
            raise SheetNeeded(
                "This PDF is a picture of the drawing (scanned or saved as an image), so it is measured from its "
                "colours. The sheet size (A0, A1...) is not on the title block and could not be confirmed from "
                "the room areas printed on the drawing. Enter the sheet size and read it again."
            )
        source["sheet"] = "room areas printed on the drawing"
        if found != trial:
            m, shape_note, legend, rooms, report = _measure(img, found, scale, min_opening)
            ratio, used = check_scale(rooms)
        sheet = found
    else:
        sheet = trial

    report["sheet"] = sheet
    report["sources"] = source
    report["manual_finishes"] = list(getattr(legend, "unread", []))
    unclear = _unclear_codes(legend)
    report["unclear_codes"] = unclear
    for r in rooms:
        if r.finish is not None and r.finish.code in unclear:
            r.flag(f"the finish code {r.finish.code} is unclear on the picture: check it on the drawing")
    if shape_note:
        for r in rooms:
            r.flag(shape_note)
        report["shape_note"] = shape_note
    report["printed_areas"] = {"rooms": used, "ratio": None if ratio is None else round(ratio, 4)}
    if ratio is not None and abs(ratio - 1) > AREA_CHECK:
        for r in rooms:
            r.flag(f"the room areas printed on the drawing are {ratio - 1:+.0%} from those measured ({used} rooms): "
                   f"check the sheet size ({sheet}) and scale (1:{scale:g})")
    for r in rooms:
        printed = getattr(r, "printed_area", None)
        if printed and r.area >= 3 and abs(printed / r.area - 1) > ROOM_CHECK:
            r.flag(f"the drawing prints {printed:g} m2 for this room, measured {r.area:.1f} m2: "
                   "check the outline (rooms sharing a finish can run together)")
    return PictureReading(rooms, legend, report, sheet, float(scale), img.width * m, img.height * m, img.size)
