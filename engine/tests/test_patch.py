"""Patcher checks on a real job. Skipped unless PREMEASURE_FIXTURES is set."""

import os
from pathlib import Path

import pytest

from premeasure_engine.callidus import read_est
from premeasure_engine.callidus.patch import EstPatcher, PatchError

ROOT = os.environ.get("PREMEASURE_FIXTURES")
pytestmark = pytest.mark.skipif(not ROOT, reason="PREMEASURE_FIXTURES not set")
FF9 = "FF9 - Marmoleum Fresco Silver Shadow 3860"


@pytest.fixture
def cashel():
    files = sorted((Path(ROOT) / "cashel").glob("*.est"))
    if not files:
        pytest.skip("cashel fixtures missing")
    return files[0]


def _by_name(est):
    return {r.name: r for r in est.rooms}


def _reread(patcher):
    return read_est(patcher.path, data=bytes(patcher.data))


def test_no_edit_is_byte_identical(cashel):
    assert bytes(EstPatcher(cashel).data) == cashel.read_bytes()


def test_rename_changes_only_that_room(cashel):
    before = _by_name(read_est(cashel))
    p = EstPatcher(cashel)
    p.rename_room("G19", "G19 - TEST RENAME")
    after = _by_name(_reread(p))
    assert "G19" not in after and "G19 - TEST RENAME" in after
    assert after["G19 - TEST RENAME"].vertices == before["G19"].vertices
    assert len(after) == len(before)
    assert all(after[n].vertices == before[n].vertices for n in before if n != "G19")


def test_set_material(cashel):
    p = EstPatcher(cashel)
    p.set_room_material("G25", FF9)
    assert _by_name(_reread(p))["G25"].material.name == FF9


def test_move_keeps_area_and_shifts_vertices(cashel):
    before = _by_name(read_est(cashel))["G25"]
    p = EstPatcher(cashel)
    p.move_room("G25", 1.0, 0.0)
    after = _by_name(_reread(p))["G25"]
    assert abs(after.area - before.area) < 1e-3
    for (x0, y0), (x1, y1) in zip(before.vertices, after.vertices):
        assert abs(x1 - x0 - 1.0) < 1e-4 and abs(y1 - y0) < 1e-6


def test_move_refuses_room_without_expected_zone(cashel):
    p = EstPatcher(cashel)
    with pytest.raises(PatchError):
        p.move_room("GF Circulation West Design 1", 1.0, 0.0)
