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
