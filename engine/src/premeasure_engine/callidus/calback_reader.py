"""Reader for Callidus .CalBack background files.

A .CalBack holds the drawing sheets the estimator measured on. Layout:

* uint32 at offset 4: number of sheet images.
* For each sheet, a 0x2c-byte record directly before its image:
  float32 x-left, y-top, x-right, y-bottom at record offsets 8, 12, 16, 20
  (metres, in the floor's coordinates), and uint32 image length at 0x28.
* Each image is a 1-bit TIFF (CCITT Group 4), typically 300 to 600 dpi.
"""

from __future__ import annotations

import io
import struct
from dataclasses import dataclass
from pathlib import Path

_TIFF_BIG_ENDIAN = b"MM\x00*"
_TIFF_LITTLE_ENDIAN = b"II*\x00"
_RECORD = 0x2C


@dataclass(frozen=True)
class SheetImage:
    x_left: float
    y_top: float
    x_right: float
    y_bottom: float
    tiff_offset: int
    tiff_length: int

    @property
    def width_m(self) -> float:
        return self.x_right - self.x_left

    @property
    def height_m(self) -> float:
        return self.y_bottom - self.y_top


@dataclass
class CalBackFile:
    path: Path
    sheets: list[SheetImage]

    def image(self, index: int):
        """Return sheet ``index`` as a PIL image (1-bit)."""
        from PIL import Image

        Image.MAX_IMAGE_PIXELS = None
        s = self.sheets[index]
        data = self.path.read_bytes()[s.tiff_offset : s.tiff_offset + s.tiff_length]
        img = Image.open(io.BytesIO(data))
        img.load()
        return img


def _find_tiff(data: bytes, start: int) -> int:
    hits = [i for i in (data.find(_TIFF_BIG_ENDIAN, start), data.find(_TIFF_LITTLE_ENDIAN, start)) if i >= 0]
    return min(hits) if hits else -1


def read_calback(path: str | Path) -> CalBackFile:
    path = Path(path)
    data = path.read_bytes()
    count = struct.unpack_from("<I", data, 4)[0]
    sheets: list[SheetImage] = []
    pos = 0
    for _ in range(count):
        t = _find_tiff(data, pos)
        if t < _RECORD:
            raise ValueError(f"sheet image {len(sheets) + 1} of {count} not found in {path.name}")
        rec = t - _RECORD
        x_left, y_top, x_right, y_bottom = struct.unpack_from("<ffff", data, rec + 8)
        length = struct.unpack_from("<I", data, rec + 0x28)[0]
        sheets.append(SheetImage(x_left, y_top, x_right, y_bottom, t, length))
        pos = t + length
    return CalBackFile(path=path, sheets=sheets)
