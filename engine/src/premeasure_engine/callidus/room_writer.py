"""Write Callidus rooms that have no layout.

A room with no layout is a normal state in Callidus: it is how every room
starts before the estimator lays it out, and Crean's own files hold many (for
example every FF8 room in the Cashel file). In the .est stream such a room is
exactly two MFC objects, the Room and its OldSheetZone, so one can replace
another without renumbering anything else in the file.

`parse_room` reads a no-layout room into a `RoomRecord`; `serialise_room`
writes one back. The round trip is byte-exact on every no-layout sheet room in
the nine sample files (see tests), which is what lets us trust rooms we build
ourselves with the same code.

Everything here refuses anything it does not recognise. A room with a layout,
sub-rooms (islands), or a tile or net zone raises `RoomFormatError`.
"""

from __future__ import annotations

import math
import struct
import uuid
from dataclasses import dataclass, field

from .est_reader import VERTEX_MARKER

ENCODING = "cp1252"
ZERO18 = bytes(18)

# Fixed runs inside the room block, checked on read and written as-is.
_AFTER_ORIGIN = bytes(8)  # +33
_AFTER_ANGLES = b"\x01\x00\x00\x00\x00"  # +49
_COUNTS = struct.pack("<II", 1, 1)  # +84
_BEFORE_REF = bytes(14)  # +100, after the uint16 at +96 and the sub-room count at +98
_TAIL_FIXED = bytes.fromhex(  # +144 to +236
    "0000000001000000010000000000000000000000000000000100000000000000"
    "010000000000003f0000003f0000000000000000000000000000000000000000"
    "000000000000000000000000c8c20000000000000000000000000100"
)
# Two bytes in that run vary between files and rooms (meaning not yet known);
# they are kept as read.
_TAIL_FLAGS = (78, 90)
_EMPTY_LISTS = bytes.fromhex("e803000000000000e803000000000000e8030000")
_M1000 = struct.pack("<I", VERTEX_MARKER)


class RoomFormatError(ValueError):
    """The bytes are not a no-layout sheet room in the expected form."""


@dataclass
class RoomRecord:
    room_class: int  # MFC class index of Room
    zone_class: int  # MFC class index of OldSheetZone
    zone_index: int  # MFC object index of this room's zone
    material: int  # MFC object index of the room's material
    name: str
    callidus_id: str
    vertices: list[tuple[float, float]]
    edges: list[bytes]  # 18 bytes per edge, edge i runs from vertex i to i+1
    origin: tuple[float, float]  # layout origin, also the zone's point
    angle: float  # layout direction, radians
    angle2: float = 0.0
    label: tuple[float, float] = (0.0, 0.0)
    ref_edge: int = 0
    ref_point: tuple[float, float] = (0.0, 0.0)
    flags: tuple[int, int] = (0, 1)
    zone_edges: list[bytes] = field(default_factory=list)


class _Reader:
    def __init__(self, data: bytes, pos: int):
        self.d, self.p = data, pos

    def take(self, n: int) -> bytes:
        if self.p + n > len(self.d):
            raise RoomFormatError("ran past the end of the file")
        b = self.d[self.p : self.p + n]
        self.p += n
        return b

    def u16(self) -> int:
        return struct.unpack("<H", self.take(2))[0]

    def u32(self) -> int:
        return struct.unpack("<I", self.take(4))[0]

    def f32(self) -> float:
        return struct.unpack("<f", self.take(4))[0]

    def expect(self, raw: bytes, what: str) -> None:
        got = self.take(len(raw))
        if got != raw:
            raise RoomFormatError(f"unexpected {what} at {self.p - len(raw):#x}")

    def marker(self) -> None:
        if self.u32() != VERTEX_MARKER:
            raise RoomFormatError(f"missing 1000 marker at {self.p - 4:#x}")

    def point(self, what: str) -> tuple[float, float]:
        x, y = self.f32(), self.f32()
        if self.take(18) != ZERO18:
            raise RoomFormatError(f"{what} has non-zero extra bytes")
        self.marker()
        return (x, y)

    def poly(self) -> tuple[list[tuple[float, float]], list[bytes]]:
        n = self.u32()
        if not 3 <= n <= 2000:
            raise RoomFormatError(f"implausible vertex count {n}")
        verts, edges = [], []
        for _ in range(n):
            verts.append((self.f32(), self.f32()))
            edges.append(self.take(18))
            self.marker()
        self.marker()
        return verts, edges

    def cstring(self) -> bytes:
        n = self.take(1)[0]
        if n == 0xFF:
            n = self.u16()
            if n == 0xFFFE:
                raise RoomFormatError("Unicode strings are not supported")
            if n == 0xFFFF:
                n = self.u32()
        return self.take(n)


def _name_xml(name: str, callidus_id: str) -> bytes:
    name_tag = f"<name>{name}</name>" if name else "<name/>"
    return (
        "<roomName>\r\n"
        f"{name_tag}\r\n"
        f"<CallidusID>{callidus_id}</CallidusID>\r\n"
        "<TPPid/>\r\n"
        "</roomName>\r\n"
    ).encode(ENCODING)


def _cstring(raw: bytes) -> bytes:
    if len(raw) < 0xFF:
        return bytes([len(raw)]) + raw
    if len(raw) < 0xFFFE:
        return b"\xff" + struct.pack("<H", len(raw)) + raw
    return b"\xff\xff\xff" + struct.pack("<I", len(raw)) + raw


@dataclass
class RoomHead:
    """The part of any room before its zone: outline, name and settings."""

    vertices: list[tuple[float, float]]
    edges: list[bytes]
    name: str
    callidus_id: str
    material: int
    origin: tuple[float, float]
    angle: float
    angle2: float
    label: tuple[float, float]
    ref_edge: int
    sub_rooms: int
    ref_point: tuple[float, float] | None = None
    flags: tuple[int, int] | None = None


def _read_tag(r: _Reader) -> int | None:
    """Read a class tag; return the class index, or None for a declaration."""
    tag = r.u16()
    if tag == 0xFFFF:
        r.u16()  # schema
        r.take(r.u16())  # class name
        return None
    if not tag & 0x8000:
        raise RoomFormatError("expected a class tag")
    return tag & 0x7FFF


def parse_room_head(data: bytes, pos: int) -> RoomHead:
    """Read the room fields that come before the zone, for any room.

    Works for rooms with layouts and for the first room in a file (which
    declares the Room class). For rooms with sub-rooms, the fields after the
    sub-rooms are not read.
    """
    r = _Reader(data, pos)
    _read_tag(r)
    vertices, edges = r.poly()
    xml = r.cstring().decode(ENCODING)
    if "<name/>" in xml:
        name = ""
    else:
        name = xml.split("<name>", 1)[1].split("</name>", 1)[0]
    cid = xml.split("<CallidusID>", 1)[1].split("</CallidusID>", 1)[0]
    material = r.u16()
    r.expect(b"\x00", "byte after material")
    origin = r.point("origin")
    r.expect(_AFTER_ORIGIN, "bytes after origin")
    angle, angle2 = r.f32(), r.f32()
    r.expect(_AFTER_ANGLES, "bytes after angles")
    label = r.point("label")
    r.expect(_COUNTS, "counts")
    ref_edge = r.u32()
    r.expect(b"\x00\x00", "uint16 at +96")
    sub_rooms = r.u16()
    head = RoomHead(vertices, edges, name, cid, material, origin, angle, angle2, label, ref_edge, sub_rooms)
    if sub_rooms == 0:
        r.expect(_BEFORE_REF, "bytes before reference point")
        head.ref_point = r.point("reference point")
        tail = r.take(len(_TAIL_FIXED))
        flags = tuple(tail[i] for i in _TAIL_FLAGS)
        if _tail(flags) != tail:
            raise RoomFormatError("unexpected fixed room fields")
        head.flags = flags
    return head


def parse_room(data: bytes, pos: int) -> tuple[RoomRecord, int]:
    """Parse a no-layout room whose object tag starts at `pos`.

    Returns the record and the position just after the room.
    """
    r = _Reader(data, pos)
    tag = r.u16()
    if not tag & 0x8000 or tag == 0xFFFF:
        raise RoomFormatError("room does not start with a class reference")
    vertices, edges = r.poly()
    xml = r.cstring().decode(ENCODING)
    try:
        if "<name/>" in xml:
            name = ""
        else:
            name = xml.split("<name>", 1)[1].split("</name>", 1)[0]
        cid = xml.split("<CallidusID>", 1)[1].split("</CallidusID>", 1)[0]
    except IndexError as exc:
        raise RoomFormatError("room name XML not recognised") from exc
    material = r.u16()
    r.expect(b"\x00", "byte after material")
    origin = r.point("origin")
    r.expect(_AFTER_ORIGIN, "bytes after origin")
    angle, angle2 = r.f32(), r.f32()
    r.expect(_AFTER_ANGLES, "bytes after angles")
    label = r.point("label")
    r.expect(_COUNTS, "counts")
    ref_edge = r.u32()
    r.expect(b"\x00\x00", "uint16 at +96")
    if r.u16() != 0:
        raise RoomFormatError("room has sub-rooms")
    r.expect(_BEFORE_REF, "bytes before reference point")
    ref_point = r.point("reference point")
    tail = r.take(len(_TAIL_FIXED))
    flags = tuple(tail[i] for i in _TAIL_FLAGS)
    if _tail(flags) != tail:
        raise RoomFormatError("unexpected fixed room fields")
    ztag = r.u16()
    if not ztag & 0x8000 or ztag == 0xFFFF:
        raise RoomFormatError("zone is not a class reference")
    zverts, zedges = r.poly()
    if zverts != vertices:
        raise RoomFormatError("zone outline differs from room outline")
    if r.u16() != 0:
        raise RoomFormatError("zone has a parent")
    if r.point("zone point") != origin:
        raise RoomFormatError("zone point differs from room origin")
    if r.f32() != angle:
        raise RoomFormatError("zone angle differs from room angle")
    r.marker()
    zone_index = r.u16()
    if r.u16() != material:
        raise RoomFormatError("zone material differs from room material")
    r.expect(_EMPTY_LISTS, "zone lists")
    if r.u16() != 0:
        raise RoomFormatError("room has a layout")
    r.marker()
    rec = RoomRecord(
        room_class=tag & 0x7FFF,
        zone_class=ztag & 0x7FFF,
        zone_index=zone_index,
        material=material,
        name=name,
        callidus_id=cid,
        vertices=vertices,
        edges=edges,
        origin=origin,
        angle=angle,
        angle2=angle2,
        label=label,
        ref_edge=ref_edge,
        ref_point=ref_point,
        flags=flags,
        zone_edges=zedges,
    )
    if serialise_room(rec) != data[pos : r.p]:
        raise RoomFormatError("room does not round-trip")
    return rec, r.p


def _tail(flags: tuple[int, int]) -> bytes:
    tail = bytearray(_TAIL_FIXED)
    for i, v in zip(_TAIL_FLAGS, flags, strict=True):
        tail[i] = v
    return bytes(tail)


def _point(p: tuple[float, float]) -> bytes:
    return struct.pack("<ff", *p) + ZERO18 + _M1000


def _poly(vertices, edges) -> bytes:
    out = [struct.pack("<I", len(vertices))]
    for (x, y), e in zip(vertices, edges, strict=True):
        if len(e) != 18:
            raise RoomFormatError("edge data must be 18 bytes")
        out.append(struct.pack("<ff", x, y) + e + _M1000)
    out.append(_M1000)
    return b"".join(out)


def _class_tag(index: int, name: str, declare: bool) -> bytes:
    """MFC class tag: a new-class declaration the first time, then a reference."""
    if declare:
        raw = name.encode("ascii")
        return b"\xff\xff" + struct.pack("<HH", 1, len(raw)) + raw
    return struct.pack("<H", 0x8000 | index)


def serialise_room(rec: RoomRecord, *, declare_classes: bool = False) -> bytes:
    """Write a no-layout room: the Room object followed by its zone.

    With declare_classes the Room and OldSheetZone classes are declared
    rather than referenced, as Callidus does for the first room in a file.
    """
    zone_edges = rec.zone_edges or [ZERO18] * len(rec.vertices)
    return b"".join(
        [
            _class_tag(rec.room_class, "Room", declare_classes),
            _poly(rec.vertices, rec.edges),
            _cstring(_name_xml(rec.name, rec.callidus_id)),
            struct.pack("<H", rec.material),
            b"\x00",
            _point(rec.origin),
            _AFTER_ORIGIN,
            struct.pack("<ff", rec.angle, rec.angle2),
            _AFTER_ANGLES,
            _point(rec.label),
            _COUNTS,
            struct.pack("<I", rec.ref_edge),
            b"\x00\x00",  # +96, always 0
            b"\x00\x00",  # +98, no sub-rooms
            _BEFORE_REF,
            _point(rec.ref_point),
            _tail(rec.flags),
            _class_tag(rec.zone_class, "OldSheetZone", declare_classes),
            _poly(rec.vertices, zone_edges),
            b"\x00\x00",
            _point(rec.origin),
            struct.pack("<f", rec.angle),
            _M1000,
            struct.pack("<HH", rec.zone_index, rec.material),
            _EMPTY_LISTS,
            b"\x00\x00",
            _M1000,
        ]
    )


def new_room(
    template: RoomRecord,
    *,
    name: str,
    material: int,
    vertices: list[tuple[float, float]],
    edges: list[bytes] | None = None,
    label: tuple[float, float] | None = None,
    callidus_id: str | None = None,
) -> RoomRecord:
    """Build a room that takes the object slots of `template`.

    The new room keeps the template's class indices and zone index, so it can
    replace the template in place. Layout origin is the first vertex and the
    layout runs along the first edge, as Callidus does for a room drawn from
    that vertex.
    """
    if len(vertices) < 3:
        raise RoomFormatError("a room needs at least three vertices")
    if "<" in name or ">" in name or "&" in name:
        raise RoomFormatError("room names may not contain < > or &")
    (x0, y0), (x1, y1) = vertices[0], vertices[1]
    angle = math.atan2(y1 - y0, x1 - x0) % (2 * math.pi)
    if label is None:
        xs, ys = zip(*vertices)
        label = (sum(xs) / len(xs), sum(ys) / len(ys))
    return RoomRecord(
        room_class=template.room_class,
        zone_class=template.zone_class,
        zone_index=template.zone_index,
        material=material,
        name=name,
        callidus_id=callidus_id or str(uuid.uuid4()),
        vertices=list(vertices),
        edges=list(edges) if edges else [ZERO18] * len(vertices),
        zone_edges=list(edges) if edges else [ZERO18] * len(vertices),
        origin=vertices[0],
        angle=angle,
        label=label,
        ref_edge=0,
        ref_point=vertices[0],
        flags=template.flags,
    )
