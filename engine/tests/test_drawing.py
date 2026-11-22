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
