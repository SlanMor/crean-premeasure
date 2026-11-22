"""Add new products (Callidus "materials") to a job.

Each product in an .est is a Material object followed by its Product object,
in one run of bytes (see docs/callidus-format.md, "Products"). For a sheet
or edge product that run is:

  class reference to Material (0x8001)
  CString  <matName><name>...</name><CallidusID>...</CallidusID><TPPid/></matName>
  412 bytes of settings: display colour at 128 (R, G, B), a number at 343,
           roll and waste settings we copy unchanged
  class reference to the product class (ProductOLDSheet, ProductNet, ...)
  uint16   object number of its Material (a reference back)
  uint32 1000, uint32 1000

We add a product by copying an existing product of the same kind from the
job, giving it a new name, a new ID and its own colour, and appending it to
the material list. Rooms come after the material list, so every new product
moves the Room class number on by two; ``job_writer`` numbers rooms from
the template, so returning an adjusted template is enough. The tail section
has one stock-roll list per product, so it gets one more empty list each.

Tile products also carry a tile grid; they are not added this way yet.
"""

from __future__ import annotations

import re
import struct
import uuid
from dataclasses import dataclass, replace
from html import escape
from pathlib import Path

from .est_reader import read_est
from .job_writer import JobFormatError, Template, load_template

MATERIAL_DECL = b"\xff\xff\x01\x00\x08\x00Material"
MATERIAL_REF = struct.pack("<H", 0x8001)
SETTINGS = 412  # bytes between the name block and the product object
COLOUR_AT = 128
NUMBER_AT = 343
PRODUCT_TAIL = 10  # uint16 back reference + two uint32 1000
END_1000 = struct.pack("<II", 1000, 1000)


@dataclass
class NewProduct:
    name: str
    like: str  # name of an existing product of the same kind to copy settings from
    colour: tuple[int, int, int] | None = None


@dataclass
class _Chunk:
    start: int
    end: int
    name: str
    index: int  # MFC object number of the Material


def _cstring(raw: bytes) -> bytes:
    if len(raw) < 0xFF:
        return bytes([len(raw)]) + raw
    if len(raw) < 0xFFFE:
        return b"\xff" + struct.pack("<H", len(raw)) + raw
    raise JobFormatError("product name block too long")


def _chunks(data: bytes) -> tuple[int, list[_Chunk]]:
    """Position of the material count, and each product's run of bytes."""
    decl = data.find(MATERIAL_DECL)
    if decl < 2:
        raise JobFormatError("material list not found")
    count_at = decl - 2
    count = struct.unpack_from("<H", data, count_at)[0]
    est = read_est(Path("template.est"), data=data)
    if len(est.materials) != count:
        raise JobFormatError(f"material count {count} does not match the {len(est.materials)} products found")
    starts = []
    for m in re.finditer(rb"<matName>", data):
        s = m.start()
        if data[s - 3] == 0xFF:  # long name block: 0xFF + WORD length
            s -= 3
        else:
            s -= 1
        starts.append(s - 2)  # the class tag before the string
        if len(starts) == count:
            break
    starts[0] = decl
    room_decl = data.find(b"\xff\xff\x01\x00\x04\x00Room")
    ends = starts[1:] + [room_decl - 2]
    return count_at, [
        _Chunk(a, b, m.name, m.index) for a, b, m in zip(starts, ends, est.materials)
    ]


def _plain(data: bytes, c: _Chunk) -> bool:
    """A sheet or edge product that refers to its classes rather than declaring them."""
    raw = data[c.start : c.end]
    return raw.startswith(MATERIAL_REF) and b"\xff\xff\x01\x00" not in raw and raw.endswith(END_1000)


def _clone(data: bytes, src: _Chunk, new: NewProduct, new_index: int, number: int) -> bytes:
    raw = data[src.start : src.end]
    if not raw.startswith(MATERIAL_REF):
        raise JobFormatError(f"{src.name!r} cannot be copied (it declares a class)")
    block_at = 2
    if raw[block_at] == 0xFF:
        n = struct.unpack_from("<H", raw, block_at + 1)[0]
        block, after = raw[block_at + 3 : block_at + 3 + n], block_at + 3 + n
    else:
        n = raw[block_at]
        block, after = raw[block_at + 1 : block_at + 1 + n], block_at + 1 + n
    rest = bytearray(raw[after:])
    if len(rest) != SETTINGS + 2 + PRODUCT_TAIL or not rest.endswith(END_1000):
        raise JobFormatError(f"{src.name!r} is not a sheet or edge product this writer knows")
    tag = struct.unpack_from("<H", rest, SETTINGS)[0]
    if not tag & 0x8000 or tag == 0xFFFF:
        raise JobFormatError(f"{src.name!r} declares its product class; copy a later product of that kind")
    if struct.unpack_from("<H", rest, SETTINGS + 2)[0] != src.index:
        raise JobFormatError(f"{src.name!r}: product does not refer back to its material")

    name = escape(new.name, quote=False)
    try:
        raw_name = name.encode("cp1252")  # Callidus is a Windows program: names are in the Windows character set
    except UnicodeEncodeError as exc:
        raise JobFormatError(f"product name {new.name!r} has a character Callidus cannot show") from exc
    block = re.sub(rb"<name>.*?</name>", b"<name>" + raw_name + b"</name>", block, count=1, flags=re.S)
    block = re.sub(rb"<CallidusID>.*?</CallidusID>",
                   b"<CallidusID>" + str(uuid.uuid4()).encode() + b"</CallidusID>", block, count=1, flags=re.S)
    if new.colour is not None:
        rest[COLOUR_AT : COLOUR_AT + 3] = bytes(new.colour)
    rest[NUMBER_AT] = number
    struct.pack_into("<H", rest, SETTINGS + 2, new_index)
    return MATERIAL_REF + _cstring(block) + bytes(rest)


def extend_template(path: str | Path, products: list[NewProduct]) -> tuple[Template, dict[str, int]]:
    """The job's template with ``products`` added; returns it and the object
    number of every product (old and new) by name."""
    data = Path(path).read_bytes()
    template = load_template(path)
    count_at, chunks = _chunks(data)
    by_name = {c.name: c for c in chunks}
    index = {c.name: c.index for c in chunks}
    if not products:
        return template, index
    if len(chunks) + len(products) > 120:
        raise JobFormatError("too many products: edge products are referred to by one byte")
    numbers = [data[c.end - PRODUCT_TAIL - 2 - SETTINGS + NUMBER_AT] for c in chunks[1:] if _plain(data, c)]
    number = max(numbers, default=0)
    next_index = template.room_class  # the first new Material takes the Room class's old number
    added = []
    for p in products:
        if p.name in index:
            raise JobFormatError(f"product {p.name!r} is already in the job")
        if p.like not in by_name:
            raise JobFormatError(f"no product {p.like!r} to copy settings from")
        number = min(number + 1, 255)
        added.append(_clone(data, by_name[p.like], p, next_index, number))
        index[p.name] = next_index
        next_index += 2  # Material object, then its Product object
    prefix = bytearray(template.prefix)
    struct.pack_into("<H", prefix, count_at, len(chunks) + len(products))
    prefix += b"".join(added)
    return (
        replace(template, prefix=bytes(prefix), room_class=next_index, n_lists=template.n_lists + len(products)),
        index,
    )


KINDS = {"ProductOLDSheet": "sheet", "ProductTile": "tile", "ProductNet": "edge"}


@dataclass
class ExistingProduct:
    name: str
    kind: str  # sheet, tile, edge (skirting, matwell frames, NONE), or the class name if unknown
    copyable: bool  # can be used as the settings for a new product
    colour: tuple[int, int, int] | None = None  # display colour in Callidus, where we can read it


def existing_products(path: str | Path) -> list[ExistingProduct]:
    """Every product in a job, with its kind and whether new products can copy it."""
    data = Path(path).read_bytes()
    _, chunks = _chunks(data)
    classes: dict[int, str] = {}
    declared: dict[str, str] = {}
    for c in chunks:
        raw = data[c.start : c.end]
        for m in re.finditer(rb"\xff\xff\x01\x00(..)", raw, re.S):
            n = struct.unpack("<H", m.group(1))[0]
            cls = raw[m.end() : m.end() + n].decode("ascii", "replace")
            if cls.startswith("Product"):
                classes[c.index + 1] = cls
                declared[c.name] = cls
    out = []
    for c in chunks:
        if c.name in declared:
            cls = declared[c.name]
        else:
            tag = struct.unpack_from("<H", data, c.end - PRODUCT_TAIL - 2)[0]
            cls = classes.get(tag & 0x7FFF, "unknown") if tag & 0x8000 else "unknown"
        kind = KINDS.get(cls, cls)
        plain = _plain(data, c)
        colour = None
        if plain:
            at = c.end - PRODUCT_TAIL - 2 - SETTINGS + COLOUR_AT
            colour = tuple(data[at : at + 3])
        out.append(ExistingProduct(c.name, kind, plain and kind in ("sheet", "edge") and c.name != "NONE", colour))
    return out
