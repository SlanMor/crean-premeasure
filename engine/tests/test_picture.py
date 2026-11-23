"""Drawings that arrive as pictures. A drawn test sheet always (needs Tesseract
for the legend text); the Cashel P2 drawing turned into a picture with fixtures."""

import os
import shutil
from pathlib import Path

import numpy as np
import pytest

from premeasure_engine.drawing import picture as P

ROOT = os.environ.get("PREMEASURE_FIXTURES")
FONT = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
DPI = 100  # a typical scan
SHEET, SCALE = "A3", 100
COLOURS = [(244, 214, 120), (150, 200, 235), (205, 160, 200)]


def needs_ocr():
    try:
        import pytesseract  # noqa: F401
    except ImportError:
        pytest.skip("pytesseract not installed")
    if shutil.which("tesseract") is None or not FONT.exists():
        pytest.skip("Tesseract or the test font not installed")


def _px(m: float) -> int:
    """Real metres to pixels on the test sheet."""
    return round(m * 1000 / SCALE / 25.4 * DPI)


def _sheet(tmp_path, title=True, page_mm=None) -> Path:
    """An A3 finishes plan at 1:100 as a picture-only PDF: two rooms (8 x 6 m and
    5 x 4 m) in their finish colours with 200 mm walls, a third finish in a
    3 x 3 m room, the legend, and a title block."""
    from PIL import Image, ImageDraw, ImageFont

    w, h = round(420 / 25.4 * DPI), round(297 / 25.4 * DPI)
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype(str(FONT), 22)
    wall = _px(0.2)
    x0, y0 = _px(2), _px(2)
    rooms = [(x0, y0, 8, 6, 0), (x0 + _px(8.2), y0, 5, 4, 1), (x0 + _px(8.2), y0 + _px(4.2), 3, 3, 2)]
    for x, y, rw, rh, c in rooms:
        d.rectangle([x - wall, y - wall, x + _px(rw) + wall - 1, y + _px(rh) + wall - 1], fill=(40, 40, 40))
    for x, y, rw, rh, c in rooms:
        d.rectangle([x, y, x + _px(rw) - 1, y + _px(rh) - 1], fill=COLOURS[c])
    lx, ly = round(w * 0.55), _px(2)
    for i, (c, text) in enumerate(zip(COLOURS, ["CARPET TILES", "SAFETY VINYL", "LINOLEUM SHEET"])):
        y = ly + i * 70
        d.rectangle([lx, y, lx + 40, y + 40], fill=c, outline=(0, 0, 0), width=2)
        d.text((lx + 55, y + 8), f"FLOOR FINISH TYPE {i + 1}: {text}", fill="black", font=font)
    if title:
        tx = round(w * 0.84)
        d.text((tx, h - 160), "SCALE 1:100", fill="black", font=font)
        d.text((tx, h - 120), "SHEET A3", fill="black", font=font)
    pdf = tmp_path / "picture.pdf"
    if page_mm:  # a scan saved on a page of another size
        img.save(pdf, "PDF", resolution=w / (page_mm[0] / 25.4))
    else:
        img.save(pdf, "PDF", resolution=DPI)
    return pdf


def test_title_block(tmp_path):
    needs_ocr()
    img = P.load_picture(_sheet(tmp_path))
    assert P.read_title(img) == ("A3", 100.0)


def test_rooms_measured_from_a_picture(tmp_path):
    needs_ocr()
    r = P.read_picture(_sheet(tmp_path))
    assert r.sheet == "A3" and r.scale == 100
    assert r.report["sources"] == {"sheet": "title block", "scale": "title block"}
    by = {}
    for room in r.rooms:
        by.setdefault(room.finish.code, []).append(room.area)
    assert sorted(by) == ["FF1", "FF2", "FF3"]
    for code, true in (("FF1", 48.0), ("FF2", 20.0), ("FF3", 9.0)):
        assert len(by[code]) == 1
        assert abs(by[code][0] / true - 1) < 0.02, (code, by[code][0])
    assert all(room.status == "Needs review" for room in r.rooms)  # a picture is never "Verified"


def test_sheet_size_asked_for_when_unknown(tmp_path):
    needs_ocr()
    pdf = _sheet(tmp_path, title=False, page_mm=(297, 210))  # an A4 page holding an A3 drawing
    with pytest.raises(P.SheetNeeded):
        P.read_picture(pdf)  # no scale anywhere
    with pytest.raises(P.SheetNeeded):
        P.read_picture(pdf, scale=100)  # the A4 page size is not trusted without printed areas
    r = P.read_picture(pdf, scale=100, sheet="A3")
    assert r.report["sources"] == {"sheet": "entered", "scale": "entered"}
    assert abs(sum(room.area for room in r.rooms) / 77 - 1) < 0.02


def test_pipeline_takes_a_picture(tmp_path):
    needs_ocr()
    from premeasure_engine.premeasure import analyse, render_preview, summary

    a = analyse(_sheet(tmp_path), ["Carpet tiles", "Safety vinyl", "Linoleum sheet"])
    assert a.picture
    s = summary(a)
    assert s["picture"]["sheet"] == "A3"
    assert {f["code"] for f in s["finishes"]} == {"FF1", "FF2", "FF3"}
    png = render_preview(a, tmp_path / "p.png")
    from PIL import Image

    im = Image.open(png)
    assert abs(im.width / im.height - 420 / 297) < 0.01


def test_room_tag_patterns():
    assert P.ROOM_NO.findall("02-PLNT-004 IPS/UPS") == ["02-PLNT-004"]
    assert P._room_no("02-PLNT.OO4") == "02-PLNT.004"
    assert P.AREA.findall("20.8 mm' and 4.7 m2 and 12.25m²") == ["20.8", "4.7", "12.25"]
    assert P.AREA.findall("room 1.04") == []


def test_legend_text_tidied():
    text, others = P._clean_text("FLOOR FINISH TYPE 9: | 2.5MM FORBO ’MARMOLEUM’ TAIGA FLOOR FINISH TYPE 10: FRESCO")
    assert text == "2.5MM FORBO 'MARMOLEUM' TAIGA"
    assert others == ["FF10"]


# --- with fixtures: Cashel P2 as a scan --------------------------------------------


@pytest.fixture(scope="module")
def cashel_picture(tmp_path_factory):
    if not ROOT:
        pytest.skip("PREMEASURE_FIXTURES not set")
    pdf = Path(ROOT) / "cashel" / "finishes-P2.pdf"
    if not pdf.exists():
        pytest.skip("Cashel fixtures missing")
    needs_ocr()
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(pdf))
    img = doc[0].render(scale=106 / 72).to_pil().convert("RGB")
    doc.close()
    out = tmp_path_factory.mktemp("pic") / "cashel-scan.pdf"
    img.save(out, "PDF", resolution=106)
    return pdf, out


def test_cashel_picture_against_the_vector_reading(cashel_picture):
    """Every room the picture reader finds alone (one room to one room), against
    the same room read from the architect's PDF. Cashel draws its fills under
    the heavy wall lines, so a scan of it reads a little short (about 3%);
    Limerick and Shellybanks read within about 1%. Here to catch a regression."""
    from premeasure_engine.drawing import find_rooms, read_drawing, read_legend

    vector_pdf, picture_pdf = cashel_picture
    d = read_drawing(vector_pdf)
    truth, _ = find_rooms(d, read_legend(d))
    r = P.read_picture(picture_pdf, sheet="A1")
    assert r.scale == 100  # from the title block
    assert set(r.report["manual_finishes"]) == {"FF5", "FF10"}  # hatching only: measure by hand
    errs = []
    for t in truth:
        if t.area < 5:
            continue
        best = max(r.rooms, key=lambda p: p.geom.intersection(t.geom).area)
        if best.geom.intersection(t.geom).area / best.geom.union(t.geom).area > 0.9:
            errs.append(best.area / t.area - 1)
    assert len(errs) >= 20
    assert abs(float(np.median(errs))) < 0.04
    assert float(np.mean(np.abs(errs))) < 0.05
