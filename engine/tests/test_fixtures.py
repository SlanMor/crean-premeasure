"""Checks against real Crean jobs. Skipped unless PREMEASURE_FIXTURES is set.

Expected layout (client data, never committed):

    $PREMEASURE_FIXTURES/
        limerick/     *.est, *.CalBack, measure .xlsx
        cashel/
        shellybanks/

Expected figures come from the November 2026 review. Rows that do not match
are explained by edits made to the Callidus files after the spreadsheets were
produced.
"""

import os
from pathlib import Path

import pytest

from premeasure_engine.callidus import read_calback, read_est
from premeasure_engine.reconcile import read_measure_sheet, reconcile

ROOT = os.environ.get("PREMEASURE_FIXTURES")
pytestmark = pytest.mark.skipif(not ROOT, reason="PREMEASURE_FIXTURES not set")

EXPECTED = {
    # job: (rooms, area matched, area rows, linear matched, linear rows)
    # Limerick: 6 area rows edited after export; room-level skirting differs on some rooms.
    "limerick": (231, 211, 217, 179, 217),
    # Cashel: West circulation now has no-finish cut-outs; one row is a room named "42".
    "cashel": (56, 50, 51, 10, 11),
    # Shellybanks: matwell added inside Stairs 01; PS-00-33/37 typo; GF matwell frames moved.
    "shellybanks": (128, 124, 125, 44, 47),
}


def _job(name):
    folder = Path(ROOT) / name
    ests = [read_est(p) for p in sorted(folder.glob("*.est"))]
    sheets = sorted(folder.glob("*.xlsx"))
    return folder, ests, sheets


@pytest.mark.parametrize("job", sorted(EXPECTED))
def test_reconciles_with_crean_spreadsheet(job):
    folder, ests, sheets = _job(job)
    if not ests or not sheets:
        pytest.skip(f"{job} fixtures missing")
    rooms, area_ok, area_rows, linear_ok, linear_rows = EXPECTED[job]
    assert sum(len(e.rooms) for e in ests) == rooms
    assert all(r.material is not None for e in ests for r in e.rooms)
    result = reconcile(ests, read_measure_sheet(sheets[0]))
    assert (result.area_matched, result.area_rows) == (area_ok, area_rows)
    if linear_ok is not None:
        assert (result.linear_matched, result.linear_rows) == (linear_ok, linear_rows)


@pytest.mark.parametrize("job", sorted(EXPECTED))
def test_calback_sheets_read(job):
    folder = Path(ROOT) / job
    files = sorted(folder.glob("*.CalBack"))
    if not files:
        pytest.skip(f"{job} fixtures missing")
    for f in files:
        cb = read_calback(f)
        assert cb.sheets
        for s in cb.sheets:
            assert 50 < s.width_m < 130  # A0 or A1 sheet at 1:100
