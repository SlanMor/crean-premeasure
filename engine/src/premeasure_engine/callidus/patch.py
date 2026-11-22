"""Controlled edits to an existing Callidus .est file.

This is phase 1 of the writer: instead of building a file from scratch, we
change a real file in small, known ways and ask Callidus to open it. Every
edit checks the structure it expects and refuses the room if it is not there,
so a file is never written from a guess.

Room block layout (offsets from the end of the room-name string), verified on
479 of 483 rooms across nine files:

* +0   uint16 material reference, then one byte
* +3   point record (x, y, 18 bytes, uint32 1000): layout origin, repeated
  as the zone's point
* +41  float32 layout direction, radians
* +54  point record: label position (confirmed by test T3)
* +114 point record: reference point, at or near the first vertex
* +0xee usually the zone outline (vertex count + vertex records), often
  identical to the room outline
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

from .est_reader import VERTEX_MARKER, VERTEX_RECORD, EstFile, Room, read_est
from .room_writer import RoomFormatError, RoomRecord, new_room, parse_room, serialise_room

POINT_OFFSETS = (3, 54, 114)
ZONE_OFFSET = 0xEE


class PatchError(ValueError):
    """The file does not have the structure this edit needs."""


@dataclass
class _RoomBlock:
    name_len_pos: int  # position of the name string's length byte
    name_end: int  # first byte after the name string


class EstPatcher:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data = bytearray(self.path.read_bytes())

    # -- helpers -------------------------------------------------------------

    def _est(self) -> EstFile:
        return read_est(self.path, data=bytes(self.data))

    def _room(self, name: str) -> Room:
        matches = [r for r in self._est().rooms if r.name == name]
        if len(matches) != 1:
            raise PatchError(f"expected one room called {name!r}, found {len(matches)}")
        return matches[0]

    def _block(self, room: Room) -> _RoomBlock:
        start = self.data.find(b"<roomName>", room.offset)
        pos = start - 1
        return _RoomBlock(name_len_pos=pos, name_end=pos + 1 + self.data[pos])

    def _marker(self, at: int) -> bool:
        return struct.unpack_from("<I", self.data, at)[0] == VERTEX_MARKER

    def _shift_point(self, at: int, dx: float, dy: float) -> None:
        if not self._marker(at + 26):
            raise PatchError(f"no point record at {at:#x}")
        x, y = struct.unpack_from("<ff", self.data, at)
        struct.pack_into("<ff", self.data, at, x + dx, y + dy)

    # -- edits ---------------------------------------------------------------

    def rename_room(self, name: str, new_name: str) -> None:
        room = self._room(name)
        block = self._block(room)
        old_xml = bytes(self.data[block.name_len_pos + 1 : block.name_end])
        old_tag = f"<name>{name}</name>".encode("latin-1")
        if old_xml.count(old_tag) != 1:
            raise PatchError(f"name tag for {name!r} not found exactly once")
        new_xml = old_xml.replace(old_tag, f"<name>{new_name}</name>".encode("latin-1"))
        if len(new_xml) >= 255:
            raise PatchError("room XML would need a long length prefix; not supported yet")
        self.data[block.name_len_pos : block.name_end] = bytes([len(new_xml)]) + new_xml

    def set_room_material(self, name: str, material_name: str) -> None:
        est = self._est()
        room = self._room(name)
        targets = [m for m in est.materials if m.name == material_name]
        if len(targets) != 1:
            raise PatchError(f"expected one material called {material_name!r}, found {len(targets)}")
        block = self._block(room)
        struct.pack_into("<H", self.data, block.name_end, targets[0].index)

    def move_room(self, name: str, dx: float, dy: float, *, include_zone: bool = True) -> None:
        room = self._room(name)
        block = self._block(room)
        for q in POINT_OFFSETS:
            if not self._marker(block.name_end + q + 26):
                raise PatchError(f"room {name!r} does not have the expected point records")
        if include_zone:
            zone = block.name_end + ZONE_OFFSET
            n = struct.unpack_from("<I", self.data, zone)[0]
            zone_pts = [struct.unpack_from("<ff", self.data, zone + 4 + k * VERTEX_RECORD) for k in range(n)] if n == len(room.vertices) else None
            if zone_pts != room.vertices:
                raise PatchError(f"zone outline for {name!r} is not where expected; move refused")
            for k in range(n):
                self._shift_point(zone + 4 + k * VERTEX_RECORD, dx, dy)
            for q in POINT_OFFSETS:
                self._shift_point(block.name_end + q, dx, dy)
        for k in range(len(room.vertices)):
            self._shift_point(room.offset + k * VERTEX_RECORD, dx, dy)

    def material_index(self, material_name: str) -> int:
        targets = [m for m in self._est().materials if m.name == material_name]
        if len(targets) != 1:
            raise PatchError(f"expected one material called {material_name!r}, found {len(targets)}")
        return targets[0].index

    def replace_room(
        self,
        name: str,
        *,
        new_name: str,
        material_name: str,
        vertices: list[tuple[float, float]],
        edges: list[bytes] | None = None,
        label: tuple[float, float] | None = None,
    ) -> RoomRecord:
        """Replace a no-layout room with a new room built by our writer.

        The old room must have no layout, so the new room takes exactly its
        two object slots (room and zone) and nothing else in the file moves.
        """
        room = self._room(name)
        start = room.offset - 6
        try:
            old, end = parse_room(bytes(self.data), start)
        except RoomFormatError as exc:
            raise PatchError(f"room {name!r} cannot be replaced: {exc}") from exc
        rec = new_room(
            old,
            name=new_name,
            material=self.material_index(material_name),
            vertices=vertices,
            edges=edges,
            label=label,
        )
        self.data[start:end] = serialise_room(rec)
        return rec

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.write_bytes(self.data)
        return path
