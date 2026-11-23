"""Two revisions of one drawing. Needs the fixtures (PREMEASURE_FIXTURES)."""

import copy
import os
from pathlib import Path

import pytest
from shapely import affinity

from premeasure_engine.premeasure import analyse, rooms_in_order
from premeasure_engine.revisions import compare_revisions, render_changes

ROOT = os.environ.get("PREMEASURE_FIXTURES")
pytestmark = pytest.mark.skipif(not ROOT, reason="PREMEASURE_FIXTURES not set")


@pytest.fixture(scope="module")
def cashel():
    return analyse(Path(ROOT) / "cashel" / "finishes-P2.pdf", [])


def test_a_revision_that_moved_on_the_sheet(cashel, tmp_path):
    new = copy.deepcopy(cashel)
    rooms = rooms_in_order(new)
    gone, smaller, refinished = rooms[5], rooms[7], rooms[9]
    new.rooms = [r for r in new.rooms if r is not gone]
    smaller.geom = affinity.scale(smaller.geom, 0.9, 1.0)
    refinished.finish = next(e for e in new.legend.floors() if e.code != refinished.finish.code)
    for r in new.rooms:
        r.geom = affinity.translate(r.geom, 1.0, -0.5)
    d = compare_revisions(cashel, new)
    assert d.shift == pytest.approx((1.0, -0.5))
    assert d.counts() == {"removed": 1, "outline changed": 1, "finish changed": 1,
                          "unchanged": len(cashel.rooms) - 3}
    assert d.likely_same_drawing
    by = {f["finish"]: f for f in d.as_dict()["by_finish"]}
    assert by[gone.finish.code]["change"] < 0
    assert render_changes(cashel, new, d, tmp_path / "c.png").stat().st_size > 1000


def test_another_drawing_is_not_a_revision(cashel):
    other = copy.deepcopy(cashel)
    codes = [e for e in other.legend.floors()]
    for r in other.rooms:  # same footprint, every room a different finish: another floor, not a revision
        if r.finish is not None:
            r.finish = next(e for e in codes if e.code != r.finish.code)
    d = compare_revisions(cashel, other)
    assert not d.likely_same_drawing
