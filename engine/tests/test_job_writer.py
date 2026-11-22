"""Whole-job writer and background writer."""

import io
import os
import struct
from pathlib import Path

import pytest

from premeasure_engine.callidus import read_est
from premeasure_engine.callidus.calback_writer import encode_tiff, sheet_extents
from premeasure_engine.callidus.job_writer import (
    ROOM_CLASS_DECL,
    TAIL_ANCHOR,
    build_job,
    load_template,
)
from premeasure_engine.callidus.room_writer import RoomRecord, parse_room, parse_room_head
from premeasure_engine.quantities import net_area

ROOT = os.environ.get("PREMEASURE_FIXTURES")


def test_tiff_round_trip():
    from PIL import Image, ImageDraw

    img = Image.new("1", (203, 77), 255)
    ImageDraw.Draw(img).rectangle((10, 10, 150, 60), outline=0)
    raw = encode_tiff(img, dpi=300, rows_per_strip=6)
    assert raw[:4] == b"MM\x00\x2a"
    back = Image.open(io.BytesIO(raw))
    assert back.info["compression"] == "group4"
    assert back.tag_v2[262] == 0 and back.tag_v2[278] == 6
    assert back.convert("L").tobytes() == img.convert("L").tobytes()


def test_a1_extents_match_callidus():
    # Cashel: A1 at 300 dpi, 1:100, as Callidus stored it
    x0, y_top, x1, y1 = sheet_extents(9933, 7016, 300, 100)
    assert (x0, y1) == (0.0, 0.0)
    assert abs(x1 - 84.0994) < 1e-4 and abs(y_top + 59.4021) < 1e-4


@pytest.fixture
def cashel():
    if not ROOT:
        pytest.skip("PREMEASURE_FIXTURES not set")
    files = sorted((Path(ROOT) / "cashel").glob("*.est"))
    if not files:
        pytest.skip("cashel fixtures missing")
    return files[0]


def _rooms(path):
    data, est = path.read_bytes(), read_est(path)
    out = []
    for room in sorted(est.rooms, key=lambda r: r.offset):
        if room.material is None or room.material.name == "NONE" or room.material.name.startswith("FF1 "):
            continue
        decl = room.offset - 4 - len(ROOM_CLASS_DECL)
        start = decl if data[decl : room.offset - 4] == ROOM_CLASS_DECL else room.offset - 6
        h = parse_room_head(data, start)
        out.append(RoomRecord(0, 0, 0, h.material, h.name, h.callidus_id, h.vertices, h.edges,
                              h.origin, h.angle, h.angle2, h.label, h.ref_edge,
                              h.ref_point or h.vertices[0], h.flags or (0, 1), h.edges))
    return out


def test_whole_job_structure_and_totals(cashel, tmp_path):
    t = load_template(cashel)
    assert t.room_class == 0x23
    rooms = _rooms(cashel)
    job = build_job(t, rooms)
    pos = len(t.prefix)
    n = struct.unpack_from("<H", job, pos)[0]
    assert n == len(rooms) == 51
    # walk every room; swap the first room's declarations for references so
    # parse_room can read it
    body = job[pos + 2 :]
    body = body.replace(ROOM_CLASS_DECL, struct.pack("<H", 0x8023), 1)
    body = body.replace(b"\xff\xff\x01\x00\x0c\x00OldSheetZone", struct.pack("<H", 0x8025), 1)
    p, zones = 0, []
    for _ in range(n):
        rec, p = parse_room(body, p)
        zones.append(rec.zone_index)
    assert zones == [0x26 + 2 * i for i in range(n)]
    assert body[p : p + len(TAIL_ANCHOR)] == TAIL_ANCHOR
    # every finish total matches the original, except FF8, which gains the
    # three cut-outs that were sub-rooms of one FF8 room
    out = tmp_path / "job.est"
    out.write_bytes(job)
    def totals(path):
        est, tot = read_est(path), {}
        for r in est.rooms:
            k = r.material.name.split(" - ")[0]
            tot[k] = round(tot.get(k, 0) + net_area(r, est.rooms), 3)
        return tot
    before, after = totals(cashel), totals(out)
    for k, v in after.items():
        if k != "FF8":
            assert v == before[k], k
    assert round(after["FF8"] - before["FF8"], 3) == before["NONE"]
