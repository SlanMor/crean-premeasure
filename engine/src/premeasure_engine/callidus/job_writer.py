"""Write a whole Callidus .est job: Crean's finishes plus rooms we supply.

An .est has three parts:

1. Header and finishes (materials and products), up to the room list. We copy
   this from an existing Crean file, so every finish keeps its Callidus
   product IDs, roll and tile sizes and prices exactly as Callidus wrote them.
2. The room list: a uint16 count, then the rooms. We write every room
   ourselves, with no layout, using `room_writer`.
3. A tail section after the room list. It holds per-finish lists of stock
   rolls that point into the old layouts. We copy it and empty those lists,
   because our file has no layouts.

Object numbers: in an MFC archive every new class and object takes the next
number. The header and finishes keep their numbers; the Room class takes the
next one, then each room and its zone follow in order, so we can work out
every number ourselves.
"""

from __future__ import annotations

import struct
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .est_reader import read_est
from .room_writer import RoomFormatError, RoomRecord, serialise_room

ROOM_CLASS_DECL = b"\xff\xff\x01\x00\x04\x00Room"
TAIL_ANCHOR = b"\x00\x00\x1a\x00\x00\x00\x04Item"
LISTS_AT = 265  # offset of the per-finish lists from the tail anchor; same in all nine samples
END = struct.pack("<I", 1000)


class JobFormatError(ValueError):
    """The template file is not in the form this writer expects."""


@dataclass
class Template:
    prefix: bytes  # everything before the room count
    room_class: int  # MFC number the Room class takes
    tail_fixed: bytes  # tail section up to the per-finish lists
    n_lists: int  # number of per-finish lists


def _tail_lists(tail: bytes) -> int:
    """Count the per-finish lists (uint16 count + uint16 refs each)."""
    p, n = LISTS_AT, 0
    while p < len(tail) - 4:
        count = struct.unpack_from("<H", tail, p)[0]
        p += 2 + 2 * count
        n += 1
    if p != len(tail) - 4 or tail[-4:] != END:
        raise JobFormatError("per-finish lists do not end where expected")
    return n


def load_template(path: str | Path) -> Template:
    path = Path(path)
    data = path.read_bytes()
    est = read_est(path, data=data)
    first = data.find(ROOM_CLASS_DECL)
    if first < 0 or data.find(ROOM_CLASS_DECL, first + 1) >= 0:
        raise JobFormatError("Room class declaration not found exactly once")
    # Every later room refers to the Room class by its number.
    tags = Counter(struct.unpack_from("<H", data, r.offset - 6)[0] for r in est.rooms)
    tag, _ = tags.most_common(1)[0]
    if not tag & 0x8000:
        raise JobFormatError("could not work out the Room class number")
    if data.count(TAIL_ANCHOR) != 1:
        raise JobFormatError("tail section not found exactly once")
    tail = data[data.find(TAIL_ANCHOR) :]
    return Template(
        prefix=data[: first - 2],
        room_class=tag & 0x7FFF,
        tail_fixed=tail[:LISTS_AT],
        n_lists=_tail_lists(tail),
    )


def build_job(template: Template, rooms: list[RoomRecord]) -> bytes:
    """Write a job with the template's finishes and the given rooms.

    Each room's class and object numbers are set here; anything already in
    the records for those is ignored.
    """
    if not rooms:
        raise JobFormatError("a job needs at least one room")
    if len(rooms) >= 0x7FFF:
        raise JobFormatError("too many rooms")
    r_cls = template.room_class
    z_cls = r_cls + 2  # declared straight after the first room object
    parts = [template.prefix, struct.pack("<H", len(rooms))]
    next_index = r_cls + 1  # first room object
    for i, rec in enumerate(rooms):
        first = i == 0
        room_index = next_index
        zone_index = room_index + (2 if first else 1)  # the first zone follows its class declaration
        next_index = zone_index + 1
        rec.room_class, rec.zone_class, rec.zone_index = r_cls, z_cls, zone_index
        if zone_index >= 0x7FFF:
            raise JobFormatError("object numbers beyond 32766 need long references; not supported yet")
        parts.append(serialise_room(rec, declare_classes=first))
    parts.append(template.tail_fixed)
    parts.append(b"\x00\x00" * template.n_lists)
    parts.append(END)
    return b"".join(parts)


def check_rooms(rooms: list[RoomRecord], material_indices: set[int]) -> None:
    """Refuse rooms that point at finishes the template does not have."""
    for rec in rooms:
        if rec.material not in material_indices:
            raise RoomFormatError(f"room {rec.name!r} uses unknown material {rec.material}")
        for e in rec.edges:
            if e[12] and e[12] not in material_indices:
                raise RoomFormatError(f"room {rec.name!r} has an edge product that is not a known material")
