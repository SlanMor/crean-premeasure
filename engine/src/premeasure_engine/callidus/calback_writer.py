"""Write Callidus .CalBack background files from a PDF drawing.

Callidus keeps the drawing an estimator measures on as a 1-bit TIFF inside
the .CalBack. Every sample file uses the same TIFF form: big-endian, CCITT
Group 4, WhiteIsZero, a few rows per strip. We render the PDF page with
pdfium, dither it to 1 bit, let Pillow (libtiff) do the Group 4 coding, and
then re-wrap the coded strips in exactly that TIFF form.

The .CalBack container around the image is copied from a template file (any
Crean .CalBack): we only replace the image, its length and the sheet extents.
That keeps every header field we have not decoded exactly as Callidus wrote
it.
"""

from __future__ import annotations

import io
import struct
from pathlib import Path

from .calback_reader import _RECORD, read_calback

MM_PER_INCH = 25.4


def render_pdf_page(pdf_path: str | Path, page: int = 0, dpi: int = 300):
    """Render one PDF page as a 1-bit PIL image (Floyd-Steinberg dither)."""
    import pypdfium2 as pdfium
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = None
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        pg = pdf[page]
        w_pt, h_pt = pg.get_size()
        grey = pg.render(scale=dpi / 72, grayscale=True).to_pil()
    finally:
        pdf.close()
    # Callidus rounds the pixel size to the nearest pixel (A1 at 300 dpi is
    # 9933 x 7016); pdfium may round up, so pad or crop to that size.
    size = (round(w_pt / 72 * dpi), round(h_pt / 72 * dpi))
    canvas = Image.new("L", size, 255)
    canvas.paste(grey, (0, 0))
    return canvas.convert("1")


def _g4_strips(img, rows_per_strip: int) -> tuple[list[bytes], int, int]:
    """Group 4 code a 1-bit image; return the coded strips."""
    from PIL import Image, TiffImagePlugin

    # With WhiteIsZero requested, Pillow flips the bits itself.
    info = TiffImagePlugin.ImageFileDirectory_v2()
    info[262] = 0
    info[278] = rows_per_strip
    buf = io.BytesIO()
    img.convert("1").save(buf, format="TIFF", compression="group4", tiffinfo=info)
    raw = buf.getvalue()
    t = Image.open(io.BytesIO(raw))
    offsets, counts = t.tag_v2[273], t.tag_v2[279]
    strips = [raw[o : o + c] for o, c in zip(offsets, counts, strict=True)]
    return strips, img.width, img.height


def encode_tiff(img, dpi: int = 300, rows_per_strip: int = 6) -> bytes:
    """Encode a 1-bit image as a big-endian Group 4 TIFF like Callidus's."""
    strips, width, height = _g4_strips(img, rows_per_strip)
    entries: list[tuple[int, int, int, bytes]] = []  # tag, type, count, value

    def short(tag: int, v: int):
        entries.append((tag, 3, 1, struct.pack(">HH", v, 0)))

    def long(tag: int, v: int):
        entries.append((tag, 4, 1, struct.pack(">I", v)))

    n_tags = 15
    ifd_size = 2 + 12 * n_tags + 4
    header = 8
    # Out-of-line data after the IFD: strip offsets, strip counts, 2 rationals.
    extra_at = header + ifd_size
    offsets_at = extra_at
    counts_at = offsets_at + 4 * len(strips)
    xres_at = counts_at + 4 * len(strips)
    yres_at = xres_at + 8
    data_at = yres_at + 8
    strip_offsets, pos = [], data_at
    for s in strips:
        strip_offsets.append(pos)
        pos += len(s)

    long(254, 0)
    long(256, width)
    long(257, height)
    short(258, 1)
    short(259, 4)
    short(262, 0)
    entries.append((273, 4, len(strips), struct.pack(">I", offsets_at)))
    short(274, 1)
    short(277, 1)
    long(278, rows_per_strip)
    entries.append((279, 4, len(strips), struct.pack(">I", counts_at)))
    entries.append((282, 5, 1, struct.pack(">I", xres_at)))
    entries.append((283, 5, 1, struct.pack(">I", yres_at)))
    short(284, 1)
    short(296, 2)
    assert len(entries) == n_tags
    entries.sort()

    out = bytearray(b"MM\x00\x2a" + struct.pack(">I", header))
    out += struct.pack(">H", n_tags)
    for tag, typ, count, value in entries:
        out += struct.pack(">HHI", tag, typ, count) + value
    out += struct.pack(">I", 0)
    out += b"".join(struct.pack(">I", o) for o in strip_offsets)
    out += b"".join(struct.pack(">I", len(s)) for s in strips)
    out += struct.pack(">II", dpi, 1) + struct.pack(">II", dpi, 1)
    out += b"".join(strips)
    return bytes(out)


def write_calback(
    template: str | Path,
    out: str | Path,
    tiff: bytes,
    extents: tuple[float, float, float, float],
) -> Path:
    """Write a one-sheet .CalBack from a one-sheet template.

    extents are (x_left, y_top, x_right, y_bottom) in metres.
    """
    template = Path(template)
    tpl = read_calback(template)
    if len(tpl.sheets) != 1:
        raise ValueError("template must have exactly one sheet")
    data = template.read_bytes()
    s = tpl.sheets[0]
    head = bytearray(data[: s.tiff_offset])
    rec = s.tiff_offset - _RECORD
    struct.pack_into("<ffff", head, rec + 8, *extents)
    struct.pack_into("<I", head, rec + 0x28, len(tiff))
    tail = data[s.tiff_offset + s.tiff_length :]
    out = Path(out)
    out.write_bytes(bytes(head) + tiff + tail)
    return out


def sheet_extents(width_px: int, height_px: int, dpi: int, scale: float) -> tuple[float, float, float, float]:
    """Sheet extents in metres: the bottom edge is y = 0, the top is negative."""
    w = width_px / dpi * MM_PER_INCH * scale / 1000
    h = height_px / dpi * MM_PER_INCH * scale / 1000
    return (0.0, -h, w, 0.0)
