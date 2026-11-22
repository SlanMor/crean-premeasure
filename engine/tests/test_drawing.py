"""Drawing extraction. Synthetic checks always; the Cashel P2 drawing with fixtures."""

import os
from pathlib import Path

import pytest
from shapely.geometry import box

from premeasure_engine.drawing.legend import Legend, LegendEntry
from premeasure_engine.drawing.products import suggest_materials
from premeasure_engine.drawing.rooms import Room, _tidy, skirting_type_of

ROOT = os.environ.get("PREMEASURE_FIXTURES")


def _legend():
    return Legend(
        [
            LegendEntry("skirting", 1, "", "COVING", (127, 127, 255)),
            LegendEntry("skirting", 2, "", "SOFTWOOD", (191, 159, 127)),
        ],
        None,
    )


def test_skirting_colour_families():
    L = _legend()
    assert skirting_type_of((142, 142, 255), L).code == "SK1"  # blue strip blended with the floor
    assert skirting_type_of((136, 125, 105), L).code == "SK2"  # brown strip blended with the floor
    assert skirting_type_of((244, 239, 239), L) is None  # near-white floor edge
    assert skirting_type_of((150, 168, 179), L) is None


def test_names_keep_short_words_in_capitals():
    assert _tidy("DCG ROOM") == "DCG Room"
    assert _tidy("WC & SHOWER") == "WC & Shower"


def test_skirting_lengths_follow_edges():
    sk = LegendEntry("skirting", 1, "", "", (127, 127, 255))
    r = Room(box(0, 0, 4, 3), None, "fill", None)
    r.vertices = [(0, 0), (4, 0), (4, 3), (0, 3)]
    r.edge_skirting = [sk, None, sk, sk]
    assert r.skirting_lengths() == {"SK1": 4 + 4 + 3}


def test_product_suggestions_use_codes_and_colour_names():
    entries = [
        LegendEntry("floor", 13, "", "2.5MM THICKNESS, FORBO 'MARMOLEUM CONCRETE'. COLOUR NEPTUNE 3756", None),
        LegendEntry("floor", 7, "", "2.5MM THICKNESS, FORBO 'MARMOLEUM CONCRETE'. COLOUR BLUE ICE 3753", None),
    ]
    names = ["FF8 - Forbo Concrete Neptune 3756", "FF7 - Forbo Marmoleum Fresco sage 3891"]
    s = {x.legend_code: x for x in suggest_materials(entries, names)}
    assert s["FF13"].material == "FF8 - Forbo Concrete Neptune 3756"
    assert s["FF7"].material is None  # never guessed


@pytest.fixture(scope="module")
def cashel():
    if not ROOT:
        pytest.skip("PREMEASURE_FIXTURES not set")
    pdf = Path(ROOT) / "cashel" / "finishes-P2.pdf"
    if not pdf.exists():
        pytest.skip("Cashel P2 drawing missing")
    from premeasure_engine.drawing.legend import read_legend
    from premeasure_engine.drawing.pdf_reader import read_drawing
    from premeasure_engine.drawing.rooms import find_rooms

    dwg = read_drawing(pdf)
    legend = read_legend(dwg)
    rooms, report = find_rooms(dwg, legend, exclude=[(61.9, -10.7, 79.1, 0)])
    return dwg, legend, rooms, report


def test_cashel_legend(cashel):
    dwg, legend, _, _ = cashel
    assert dwg.scale == 100
    assert [e.code for e in legend.floors()] == [f"FF{i}" for i in (1, 2, 3, 4, 5, 7, 8, 9, 10, 11, 12, 13, 14)]
    ff13 = next(e for e in legend.floors() if e.number == 13)
    assert ff13.fill == (209, 202, 185) and "NEPTUNE 3756" in ff13.description


def test_cashel_rooms_match_crean_where_both_measure_to_the_wall(cashel):
    _, _, rooms, report = cashel
    by = {r.codes[0]: r for r in rooms if r.codes}
    # verified Callidus areas from Crean's own file
    for code, area in {"F11": 115.94, "G11": 50.08, "G18": 20.39, "G19": 15.09}.items():
        assert abs(by[code].area - area) < 0.1, code
    assert by["G22"].finish.code == "FF1" and by["G22"].source == "hatch"
    assert report["manual_finishes"] == ["FF5"]


def test_cashel_coved_skirting_found_in_wet_rooms(cashel):
    _, _, rooms, _ = cashel
    by = {r.codes[0]: r for r in rooms if r.codes}
    for code in ("F09", "F13", "F14", "G04", "G06", "G14", "G15", "G16", "G17"):
        assert by[code].skirting_lengths().get("SK1", 0) > 5, code


def test_same_code_finishes_need_the_words_that_tell_them_apart():
    # Limerick 2F: one code, three colours. Crean priced only the Cinder one.
    def e(code, desc):
        return LegendEntry("floor", 0, "", desc, (1, 2, 3), custom_code=code)

    entries = [
        e("M50-150", "M50-150 FORBO MARMOLEUM MARBLED - CINDER"),
        e("M50-150 (2)", "M50-150 ACCENT AREA -FORBO MARMOLEUM MARBLED - SPARROW"),
        e("M50-150 (3)", "M50-150 ACCENT AREA -FORBO MARMOLEUM MARBLED - LEAF"),
    ]
    names = ["M50-150 LINOLEUM - MARBLED - CINDER 3889"]
    s = {x.legend_code: x for x in suggest_materials(entries, names)}
    assert s["M50-150"].material == names[0]
    assert s["M50-150 (2)"].material is None
    assert s["M50-150 (3)"].material is None


def test_fill_hole_drawn_the_other_way_round_is_cut_out():
    from premeasure_engine.drawing.pdf_reader import _fill_geom

    outer = [(0, 0), (10, 0), (10, 10), (0, 10)]  # anticlockwise
    island = [(4, 4), (4, 6), (6, 6), (6, 4)]  # clockwise
    assert _fill_geom([outer, island], even_odd=False).area == pytest.approx(96)
    same_way = [(4, 4), (6, 4), (6, 6), (4, 6)]
    assert _fill_geom([outer, same_way], even_odd=False).area == pytest.approx(100)


def test_fill_is_cut_to_its_clip_path(tmp_path):
    # Revit draws a room as a big rectangle clipped to the room shape.
    from premeasure_engine.drawing.pdf_reader import read_drawing

    content = b"q 0 0 72 72 re W n 1 0 0 rg 0 0 288 288 re f Q"
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 288 288] /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offs = []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offs)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    pdf = tmp_path / "clip.pdf"
    pdf.write_bytes(bytes(out))

    d = read_drawing(pdf, scale=100)
    (fill,) = d.fills
    # 72 pt = 1 inch = 2.54 m at 1:100
    assert fill.geom.area == pytest.approx(2.54 * 2.54, rel=1e-3)


def _floor(code, fill, hatch=()):
    return LegendEntry("floor", 0, "", code, fill, list(hatch), custom_code=code)


def test_colour_missing_from_legend_uses_the_finish_written_in_the_room():
    # Shellybanks GF: F1-H is written in the staff room but left off the legend.
    from shapely.geometry import box as rect

    from premeasure_engine.drawing.pdf_reader import Drawing, TextItem
    from premeasure_engine.drawing.rooms import _tagged_finish

    legend = Legend([_floor("F1-A", (192, 133, 80)), _floor("F1-G", (197, 199, 186)), _floor("F5", (255, 255, 255))], None)
    room = rect(0, 0, 5, 5)
    texts = [TextItem("F1-H", (2, 2, 2.4, 2.2)), TextItem("D1", (1, 1, 1.2, 1.1))]
    dwg = Drawing("x.pdf", 0, 10, 10, 100, [], [], texts)
    e = _tagged_finish(dwg, legend, (63, 107, 122), room)
    assert e is not None and e.code == "F1-H"
    # a door tag alone is not a finish
    dwg = Drawing("x.pdf", 0, 10, 10, 100, [], [], [TextItem("D1", (1, 1, 1.2, 1.1))])
    assert _tagged_finish(dwg, legend, (63, 107, 122), room) is None
    # two different tags in one colour: no guess
    texts.append(TextItem("F1-J", (3, 3, 3.4, 3.2)))
    dwg = Drawing("x.pdf", 0, 10, 10, 100, [], [], texts)
    assert _tagged_finish(dwg, legend, (63, 107, 122), room) is None


def test_finish_code_does_not_beat_a_different_product_number():
    # The contractor's own numbering can differ from the architect's: here the
    # drawing's F2-A is Neptune 3756, but the contractor's "F2A" is Sirius 3754.
    entries = [LegendEntry("floor", 0, "", "MARMOLEUM CONCRETE NEPTUNE 3756", (1, 2, 3), custom_code="F2-A")]
    names = ["F2A - Marmoleum Concrete Sirius 3754", "F7 - Marmoleum Concrete Neptune 3756"]
    (s,) = suggest_materials(entries, names)
    assert s.material == names[1]


def test_room_names_stay_short_when_rooms_merge():
    from premeasure_engine.drawing.to_callidus import MAX_NAME, room_name

    r = Room(box(0, 0, 4, 4), _floor("F1-A", (1, 2, 3)), "fill", (1, 2, 3))
    r.codes = [f"00-SUP-0{i:02d}" for i in range(12)]
    r.names = ["CLEAN UTILITY"] + ["STORE"] * 11
    name = room_name(r, 1)
    assert name == "00-SUP-000 and 11 more - Clean Utility" and len(name) <= MAX_NAME


def test_same_code_light_finish_never_takes_the_dark_product():
    # Limerick GF: the PDF splits "GREY" as "G rey" in one entry only
    def e(code, desc):
        return LegendEntry("floor", 0, "", desc, (1, 2, 3), custom_code=code)

    entries = [
        e("M50-156", "M50-156 VINYL - ANTI STATIC R9 SLIP RESISTANCE - Forbo Sphera SD - Dark Neutral G rey"),
        e("M50-156 (2)", "M50-156 VINYL - ANTI STATIC R9 SLIP RESISTANCE - Forbo Sphera SD - Light Neutral Grey"),
    ]
    names = ["M50-156 VINYL - ANTI STATIC FORBO SPHERA SD DARK NEUTRAL GREY"]
    s = {x.legend_code: x for x in suggest_materials(entries, names)}
    assert s["M50-156"].material == names[0]
    assert s["M50-156 (2)"].material is None


def test_product_number_on_another_finish_beats_a_matching_code():
    # Cashel: the drawing's FF5 is matting, but Crean's "FF5" is Puffin AQI2005, the drawing's FF12
    entries = [
        LegendEntry("floor", 0, "", "MATTING MUOVIHAKA 12mm RECESSED COMBO 16mm", None, custom_code="FF5"),
        LegendEntry("floor", 0, "", "ALTRO AQUARIUS SAFETY FLOORING COLOUR PUFFIN AQI2005", None, custom_code="FF12"),
    ]
    names = ["FF5 - Altro Aquarius R11 Puffin AQI2005", "FF4 - muovihaka 12mm recessed combo matting 16mm"]
    s = {x.legend_code: x.material for x in suggest_materials(entries, names)}
    assert s == {"FF5": names[1], "FF12": names[0]}


def test_product_number_in_the_description_beats_a_matching_code():
    # Cashel: the drawing's FF4 is Altro Classic X2546R11; Crean's "FF4" is matting
    entries = [LegendEntry("floor", 0, "", "ALTRO CLASSIC 25 GRAPHITE X2546R11", None, custom_code="FF4")]
    names = ["FF4 - muovihaka 12mm recessed combo matting 16mm", "FF3 - Altro Classic R11 Graphite X2546R11"]
    (s,) = suggest_materials(entries, names)
    assert s.material == names[1]
