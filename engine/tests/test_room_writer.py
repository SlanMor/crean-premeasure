"""Room writer: synthetic round trip always; real-file checks with fixtures."""

import os
import struct
from pathlib import Path

import pytest

from premeasure_engine.callidus import read_est
from premeasure_engine.callidus.patch import EstPatcher
from premeasure_engine.callidus.room_writer import (
    RoomFormatError,
    RoomRecord,
    new_room,
    parse_room,
    serialise_room,
)
from premeasure_engine.quantities import edge_products

ROOT = os.environ.get("PREMEASURE_FIXTURES")
needs_fixtures = pytest.mark.skipif(not ROOT, reason="PREMEASURE_FIXTURES not set")
SKIRT = bytes(12) + b"\x11\x00" + bytes.fromhex("cdcc4c3e")


def _template() -> RoomRecord:
    v = [(0.0, 0.0), (2.0, 0.0), (2.0, 1.0), (0.0, 1.0)]
    return RoomRecord(
        room_class=0x23, zone_class=0x25, room_index=0x1FB, material=0x17,
        name="Template", callidus_id="00000000-0000-0000-0000-000000000000",
        vertices=v, edges=[bytes(18)] * 4, origin=v[0], angle=0.0,
        label=(1.0, 2.0), ref_point=v[0], zone_edges=[bytes(18)] * 4,
    )


def test_synthetic_round_trip():
    rec = new_room(
        _template(), name="Store 1", material=0x13,
        vertices=[(8.0, -7.0), (12.0, -7.0), (12.0, -4.0), (8.0, -4.0)],
        edges=[SKIRT] * 4, callidus_id="11111111-2222-3333-4444-555555555555",
    )
    raw = serialise_room(rec)
    back, end = parse_room(raw, 0)
    assert end == len(raw)
    assert back == rec
    assert back.room_index == 0x1FB and back.material == 0x13


def test_refuses_unsafe_names():
    with pytest.raises(RoomFormatError):
        new_room(_template(), name="A & B", material=1, vertices=[(0, 0), (1, 0), (1, 1)])


def test_long_names_use_the_long_length_prefix():
    rec = new_room(_template(), name="x" * 300, material=1, vertices=[(0, 0), (1, 0), (1, 1)])
    raw = serialise_room(rec)
    assert parse_room(raw, 0)[0].name == "x" * 300


@pytest.fixture
def cashel():
    if not ROOT:
        pytest.skip("PREMEASURE_FIXTURES not set")
    files = sorted((Path(ROOT) / "cashel").glob("*.est"))
    if not files:
        pytest.skip("cashel fixtures missing")
    return files[0]


@needs_fixtures
def test_no_layout_rooms_round_trip(cashel):
    data = cashel.read_bytes()
    parsed = []
    for room in read_est(cashel).rooms:
        try:
            rec, end = parse_room(data, room.offset - 6)
        except RoomFormatError:
            continue
        assert serialise_room(rec) == data[room.offset - 6 : end]
        parsed.append(room.name)
    # every FF8 room in Cashel has no layout
    assert len(parsed) >= 10
    assert "F06 - Classroom 6" in parsed


@needs_fixtures
def test_replace_room_touches_only_that_room(cashel):
    before = cashel.read_bytes()
    p = EstPatcher(cashel)
    room = next(r for r in read_est(cashel).rooms if r.name == "Half landing 1 main stair")
    start = room.offset - 6
    _, old_end = parse_room(before, start)
    p.replace_room(
        "Half landing 1 main stair",
        new_name="T5 TEST ROOM",
        material_name="FF6 - Forbo Marmoleum Fresco 3.5mm 19db colour silver shadow 386035",
        vertices=[(8.0, -7.0), (12.0, -7.0), (12.0, -4.0), (8.0, -4.0)],
        edges=[SKIRT] * 4,
    )
    after = bytes(p.data)
    _, new_end = parse_room(after, start)
    assert after[:start] == before[:start]
    assert after[new_end:] == before[old_end:]
    est = read_est(cashel, data=after)
    new = next(r for r in est.rooms if r.name == "T5 TEST ROOM")
    assert round(new.area, 6) == 12.0 and round(new.perimeter, 6) == 14.0
    assert edge_products(new, est) == {"125mm coved skirting": 14.0}
    assert len(est.rooms) == len(read_est(cashel).rooms)
    # the zone keeps the replaced room's object number
    assert struct.unpack_from("<H", after, new_end - 30)[0] == 0x1FB


@needs_fixtures
def test_room_with_layout_is_refused(cashel):
    p = EstPatcher(cashel)
    with pytest.raises(Exception):
        p.replace_room("G25", new_name="X", material_name="NONE", vertices=[(0, 0), (1, 0), (1, 1)])
