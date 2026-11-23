"""Crean's measure spreadsheet from checked Callidus jobs, and the comparison
with the pre-measure. Needs the fixtures (PREMEASURE_FIXTURES)."""

import dataclasses
import io
import os
from pathlib import Path

import pytest

from premeasure_engine.callidus import read_est
from premeasure_engine.reconcile import read_measure_sheet
from premeasure_engine.roundtrip import compare_jobs, crean_rows, crean_workbook

ROOT = os.environ.get("PREMEASURE_FIXTURES")
pytestmark = pytest.mark.skipif(not ROOT, reason="PREMEASURE_FIXTURES not set")


def _sb():
    folder = Path(ROOT) / "shellybanks"
    return [read_est(p) for p in sorted(folder.glob("*.est"))], folder / "Floor Finishes Measure Rev 1.xlsx"


def test_rows_match_cranes_own_sheet():
    ests, sheet = _sb()
    rows = [r for e in ests for r in crean_rows(e)]
    theirs = read_measure_sheet(sheet)
    ours_areas = sorted(round(r.net_m2, 2) for r in rows if r.net_m2 is not None)
    matched = sum(1 for t in theirs if t.net_m2 is not None and round(t.net_m2, 2) in ours_areas)
    # Crean edited their sheet by hand after exporting it (one room moved, matwells taken out)
    assert matched >= 124
    assert {r.title for r in rows} == {"Basement", "Ground Floor", "First Floor", "Second Floor"}


def test_workbook_layout():
    from openpyxl import load_workbook

    ests, _ = _sb()
    wb = load_workbook(io.BytesIO(crean_workbook(ests, kinds={"100mm high coved skirting": "Coved skirting"})))
    ws = wb["ROOMS"]
    assert [c.value for c in ws[1]] == ["Title", "Material Name", "Room Name", "Net (m2)", "Skirting Length (lm)"]
    values = [tuple(c.value for c in r) for r in ws.iter_rows(min_row=2)]
    totals = [v for v in values if isinstance(v[1], str) and v[1].endswith(" Total")]
    assert totals and all(v[3].startswith("=SUBTOTAL(9,D") for v in totals)
    assert any(v[1] == "Grand Total" for v in values)
    assert any(v[2] == "Coved skirting" and v[4] == "lm" for v in values)
    assert "Stairs" in wb.sheetnames


def test_comparison_pairs_rooms_by_place():
    est = read_est(next((Path(ROOT) / "cashel").glob("*.est")))
    same = compare_jobs(est, est)
    assert same.right_first_time == pytest.approx(1.0)
    assert set(same.counts()) == {"unchanged"}
    # the estimator deletes one room and renames another: one removed, the rest still pair up
    priced = [r for r in est.rooms if r.material and r.material.name != "NONE"]
    gone = max(priced, key=lambda r: r.area)
    renamed = [dataclasses.replace(r, name="Renamed") if r is priced[0] and r is not gone else r
               for r in est.rooms if r is not gone]
    after = dataclasses.replace(est, rooms=renamed)
    c = compare_jobs(est, after)
    assert c.counts().get("removed") == 1
    assert c.counts().get("added") is None
    assert c.right_first_time == pytest.approx(1.0)
    assert c.outline_agreement == pytest.approx(1.0)


def test_premeasure_against_creans_own_cashel_measure(tmp_path):
    """Our pre-measure of Cashel P2 against the job Crean's estimator measured
    by hand in Callidus on the same background: nearly all of their floor is
    inside our outlines, however the rooms are split."""
    from premeasure_engine.premeasure import analyse, write_outputs

    folder = Path(ROOT) / "cashel"
    est, cb = next(folder.glob("*.est")), next(folder.glob("*.CalBack"))
    a = analyse(folder / "finishes-P2.pdf", est)
    any_product = next(m.name for m in read_est(est).materials if m.name != "NONE")
    out = write_outputs(a, est, cb, {f.code: any_product for f in a.finishes if f.kind == "floor"}, {},
                        tmp_path, "P2")
    c = compare_jobs(read_est(out.est), read_est(est))
    assert c.outline_agreement > 0.97
    assert c.area_ours < c.area_final * 1.06  # little floor measured that Crean left out


def test_cashel_p3_shared_colours_are_measured_and_flagged(tmp_path):
    """P3 gives two pairs of finishes the same colour (Rosemary Green and Silver
    Shadow, each in 2.5 mm and 3.5 mm acoustic). Their rooms used to be dropped
    as 'not in the legend', some 650 m2. They are now measured under a combined
    code, flagged for the estimator, and never given one of the pair's products."""
    from premeasure_engine.premeasure import analyse, write_outputs

    folder = Path(ROOT) / "cashel"
    est, cb = next(folder.glob("*.est")), next(folder.glob("*.CalBack"))
    a = analyse(folder / "finishes-P3.pdf", est)
    shared = {f.code: f for f in a.finishes if " or " in f.code}
    assert set(shared) == {"FF7 or FF11", "FF8 or FF10"}
    assert sum(f.amount for f in shared.values()) > 600
    assert all(f.suggestion is None for f in shared.values())  # never a guessed product
    assert {f.code: f.suggestion for f in a.finishes if f.code in ("FF7", "FF13")} == {
        "FF7": "FF11 - Forbo Marmoleum Walton Rosemary Green 335535", "FF13": "FF7 - Forbo Marmoleum Fresco sage 3891"}
    for r in a.rooms:
        if r.finish is not None and " or " in r.finish.code:
            assert r.status != "Verified" and any("same colour" in why for why in r.reasons)
    assert not ({"FF7", "FF8", "FF10", "FF11"} & {r.finish.code for r in a.rooms if r.finish})
    # with a product for every finish, the outlines hold nearly all of Crean's floor
    any_product = next(m.name for m in read_est(est).materials if m.name != "NONE")
    out = write_outputs(a, est, cb, {f.code: any_product for f in a.finishes if f.kind == "floor"}, {},
                        tmp_path, "P3")
    c = compare_jobs(read_est(out.est), read_est(est))
    assert c.outline_agreement > 0.97
    assert c.area_ours < c.area_final * 1.06


def test_finishes_schedule_settles_p3_and_flags_p2(tmp_path):
    """Crean price Cashel from the main contractor's finishes schedule. On P3
    it settles every shared-colour room by room number, and with the
    products the platform suggests, no room is left on a different product
    from Crean's own job. On P2 (older than the schedule) it flags the rooms
    whose finish changed, which is why Crean's price differed from P2."""
    from premeasure_engine.premeasure import analyse, write_outputs
    from premeasure_engine.schedule import product_ids

    folder = Path(ROOT) / "cashel"
    sched = folder / "finishes-schedule.pdf"
    est, cb = next(folder.glob("*.est")), next(folder.glob("*.CalBack"))
    assert product_ids("EN ISO 24346, EN 13845; Colour: HIPPO/ AQI2013") == {"AQI2013"}

    a = analyse(folder / "finishes-P3.pdf", est, schedule=sched)
    s = a.report["schedule"]
    assert s["rows"] == 12 and s["settled_m2"] > 680
    assert not any(" or " in r.finish.code for r in a.rooms if r.finish)
    assert {d["room"] for d in s["differs"]} == {"F16", "G24"}  # the corridor row quotes two colours
    floors = {f.code: f.suggestion for f in a.finishes if f.kind == "floor" and f.suggestion}
    out = write_outputs(a, est, cb, floors, {}, tmp_path, "P3")
    c = compare_jobs(read_est(out.est), read_est(est))
    assert "product changed" not in c.counts()
    assert c.outline_agreement > 0.97

    p2 = analyse(folder / "finishes-P2.pdf", est, schedule=sched).report["schedule"]
    assert p2["settled_m2"] == 0
    assert sum(d["area_m2"] for d in p2["differs"]) > 900
