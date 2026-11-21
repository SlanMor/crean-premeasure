"""Reader for Callidus .est estimate files.

An .est file is a Microsoft MFC ``CArchive`` binary. We do not parse every
object; we locate the parts we need by their structure:

* Materials are serialised with an embedded XML block (``<matName>``) holding
  the product name and its Callidus ID. Rooms refer to a material by its MFC
  object index, which we reconstruct by counting class and object
  declarations in the same order MFC assigns them.
* Rooms are a vertex list followed by an XML block (``<roomName>``). Each
  vertex record is 30 bytes: x, y (float32, metres), 18 bytes of per-edge
  data, then the uint32 marker 1000.

Coordinates are drawing-sheet position multiplied by the drawing scale, in
metres. Y runs negative from the top of the sheet.
"""

from __future__ import annotations

import html
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

VERTEX_RECORD = 30
VERTEX_MARKER = 1000

_MATERIAL_RE = re.compile(
    rb"<matName>\r\n<name>(.*?)</name>\r\n<CallidusID>(.*?)</CallidusID>", re.S
)
_ROOM_RE = re.compile(
    rb"<roomName>\r\n(?:<name>(.*?)</name>|<name/>)\r\n<CallidusID>(.*?)</CallidusID>",
    re.S,
)
_TILE_GRID_RE = re.compile(
    rb'<var name="width" val="([\d.]+)".*?<var name="length" val="([\d.]+)"', re.S
)
_NEW_CLASS_TAG = b"\xff\xff\x01\x00"


@dataclass(frozen=True)
class Material:
    index: int  # MFC object index; rooms and edges refer to materials by this
    name: str
    callidus_id: str
    product_class: str | None  # ProductTile, ProductOLDSheet, ProductNet, ... (first of its class only)
    tile_size_m: tuple[float, float] | None


@dataclass(frozen=True)
class Edge:
    """One room edge, from vertex ``start`` to the next vertex."""

    start: tuple[float, float]
    end: tuple[float, float]
    raw: bytes  # the 18 bytes of per-edge data

    @property
    def length(self) -> float:
        (x0, y0), (x1, y1) = self.start, self.end
        return ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5

    @property
    def is_open(self) -> bool:
        """Edge flagged open (no wall), for example a boundary to another finish."""
        return self.raw[0] == 1

    @property
    def edge_product_index(self) -> int:
        """MFC index of the edge product (skirting, matwell frame), 0 if none."""
        return self.raw[12]

    @property
    def is_plain(self) -> bool:
        """No flags at all: counts as skirting in jobs that use room-level skirting."""
        return not any(self.raw)


@dataclass
class Room:
    name: str
    callidus_id: str
    material_index: int
    material: Material | None
    vertices: list[tuple[float, float]]
    edges: list[Edge]
    offset: int  # byte offset of the vertex list, for debugging

    @property
    def code(self) -> str:
        """Room code when the name starts with one (for example PS-00-37)."""
        return self.name.split(" ", 1)[0] if self.name else ""

    @property
    def area(self) -> float:
        p = self.vertices
        n = len(p)
        return abs(sum(p[i][0] * p[(i + 1) % n][1] - p[(i + 1) % n][0] * p[i][1] for i in range(n))) / 2

    @property
    def perimeter(self) -> float:
        return sum(e.length for e in self.edges)


@dataclass
class EstFile:
    path: Path
    title: str
    materials: list[Material]
    rooms: list[Room] = field(default_factory=list)

    def material_by_index(self, index: int) -> Material | None:
        for m in self.materials:
            if m.index == index:
                return m
        return None


def _read_title(data: bytes) -> str:
    n = data[8] if len(data) > 8 else 0
    raw = data[9 : 9 + n]
    if n and all(32 <= c < 127 for c in raw):
        return raw.decode("ascii")
    return ""


def _read_materials(data: bytes) -> list[Material]:
    matches = list(_MATERIAL_RE.finditer(data))
    class_decls = [m.start() for m in re.finditer(re.escape(_NEW_CLASS_TAG), data)]
    materials: list[Material] = []
    index = 0
    for k, m in enumerate(matches):
        nxt = matches[k + 1].start() if k + 1 < len(matches) else len(data)
        if k == 0:
            index += 1  # the Material class declaration itself
        mat_index = index + 1
        index += 1
        product_class = None
        for c in class_decls:
            if m.start() < c < nxt:
                name_len = data[c + 4]
                cls = data[c + 6 : c + 6 + name_len]
                if cls.startswith(b"Product"):
                    product_class = cls.decode("ascii")
                    index += 1  # class declaration
        index += 1  # product object
        grid = _TILE_GRID_RE.search(data, m.end(), nxt)
        tile = None
        if grid and grid.start() - m.end() < 1500:
            tile = (float(grid.group(1)), float(grid.group(2)))
        materials.append(
            Material(
                index=mat_index,
                name=html.unescape(m.group(1).decode("latin-1")).strip(),
                callidus_id=m.group(2).decode("ascii", "replace"),
                product_class=product_class,
                tile_size_m=tile,
            )
        )
    return materials


def _read_vertices(data: bytes, string_len_pos: int) -> tuple[int, list[tuple[float, float]], list[bytes]]:
    end = string_len_pos - 4
    if struct.unpack_from("<I", data, end)[0] != VERTEX_MARKER:
        raise ValueError(f"no vertex marker before room name at {string_len_pos:#x}")
    n = 0
    start = end - VERTEX_RECORD
    while start > 4 and struct.unpack_from("<I", data, start + 26)[0] == VERTEX_MARKER:
        n += 1
        if struct.unpack_from("<I", data, start - 4)[0] == n:
            break
        start -= VERTEX_RECORD
    else:
        raise ValueError(f"vertex count not found for room at {string_len_pos:#x}")
    vertices = [struct.unpack_from("<ff", data, start + i * VERTEX_RECORD) for i in range(n)]
    raws = [data[start + i * VERTEX_RECORD + 8 : start + i * VERTEX_RECORD + 26] for i in range(n)]
    return start, vertices, raws


def read_est(path: str | Path) -> EstFile:
    path = Path(path)
    data = path.read_bytes()
    est = EstFile(path=path, title=_read_title(data), materials=_read_materials(data))
    by_index = {m.index: m for m in est.materials}
    for m in _ROOM_RE.finditer(data):
        string_len_pos = m.start() - 1
        offset, vertices, raws = _read_vertices(data, string_len_pos)
        string_end = string_len_pos + 1 + data[string_len_pos]
        material_index = struct.unpack_from("<H", data, string_end)[0]
        edges = [
            Edge(start=vertices[i], end=vertices[(i + 1) % len(vertices)], raw=raws[i])
            for i in range(len(vertices))
        ]
        est.rooms.append(
            Room(
                name=html.unescape((m.group(1) or b"").decode("latin-1")).strip(),
                callidus_id=m.group(2).decode("ascii", "replace"),
                material_index=material_index,
                material=by_index.get(material_index),
                vertices=vertices,
                edges=edges,
                offset=offset,
            )
        )
    return est
