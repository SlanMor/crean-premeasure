"""Turn rooms read from a drawing into Callidus room records.

Only rooms whose finish has a confirmed Callidus material are written. Rooms
that need review keep their place in the file with "CHECK - " in front of
the name, so the estimator sees them in Callidus; the reasons are in the
review report. Nothing is written for a finish with no Callidus material:
those rooms are listed in the report instead.
"""

from __future__ import annotations

import math
import struct
import uuid
from dataclasses import dataclass

from ..callidus.room_writer import ZERO18, RoomRecord
from .rooms import Room, _tidy

# Edge product data as Callidus writes it: material number at byte 12,
# float32 0.2 at bytes 14 to 17 (seen on every skirted edge in the samples).
_EDGE_TAIL = struct.pack("<f", 0.2)


def edge_bytes(material_index: int | None) -> bytes:
    if material_index is None:
        return ZERO18
    e = bytearray(18)
    e[12] = material_index
    e[14:18] = _EDGE_TAIL
    return bytes(e)


@dataclass
class Written:
    room: Room
    record: RoomRecord
    name: str


MAX_NAME = 100  # long enough for a tag and a name; several merged rooms are shortened


def room_name(room: Room, n: int) -> str:
    base = room.label or f"Untagged {room.finish.code if room.finish else 'area'} {n}"
    if len(base) > MAX_NAME - 8 and len(room.codes) > 1:
        base = f"{room.codes[0]} and {len(room.codes) - 1} more - {_first_name(room)}"
    name = f"CHECK - {base}" if room.status != "Verified" else base
    name = name.replace("<", "(").replace(">", ")").replace("&", "and")
    return name if len(name) <= MAX_NAME else name[: MAX_NAME - 3].rstrip() + "..."


def _first_name(room: Room) -> str:
    return _tidy(next((n for n in room.names if n), "").strip())[:40]


def to_records(
    rooms: list[Room],
    finish_material: dict[str, int],
    skirting_material: dict[str, int],
) -> tuple[list[Written], list[Room]]:
    """Records for rooms with a mapped finish, and the rooms left out."""
    written, left_out = [], []
    untagged = 0
    for r in sorted(rooms, key=lambda r: (r.label == "", r.label, -r.area)):
        if r.finish is None or r.finish.code not in finish_material:
            left_out.append(r)
            continue
        if not r.label:
            untagged += 1
        verts = r.vertices or list(r.geom.exterior.coords)[:-1]
        edges = [
            edge_bytes(skirting_material.get(e.code)) if e is not None else ZERO18
            for e in (r.edge_skirting or [None] * len(verts))
        ]
        (x0, y0), (x1, y1) = verts[0], verts[1]
        rec = RoomRecord(
            room_class=0, zone_class=0, room_index=0,
            material=finish_material[r.finish.code],
            name=room_name(r, untagged),
            callidus_id=str(uuid.uuid4()),
            vertices=[(float(x), float(y)) for x, y in verts],
            edges=edges,
            zone_edges=edges,
            origin=verts[0],
            angle=math.atan2(y1 - y0, x1 - x0) % (2 * math.pi),
            label=(r.geom.representative_point().x, r.geom.representative_point().y),
            ref_point=verts[0],
        )
        written.append(Written(r, rec, rec.name))
    return written, left_out
