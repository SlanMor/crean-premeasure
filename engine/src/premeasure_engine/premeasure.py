"""One drawing in, a pre-measured Callidus job out.

This is what the platform calls. Two steps, because the estimator confirms
the products in between:

1. ``analyse``: read the finishes drawing and the project's Callidus starter
   file (the .est that holds Crean's products for the job). Returns the rooms
   found, every finish on the legend with the area found and a suggested
   Callidus product, and what was set aside.
2. ``write_outputs``: with the products the estimator confirmed, write the
   .est, its .CalBack background, a review spreadsheet (CSV) and a READ ME.

Accuracy rule: a room is only written when its finish has a confirmed
product. Anything uncertain is written with "CHECK - " in front of its name
and its reasons go in the review file. Nothing is guessed.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from pathlib import Path

from .callidus import read_calback, read_est
from .callidus.calback_writer import encode_tiff, render_pdf_page, sheet_extents, write_calback
from .callidus.job_writer import build_job, check_rooms
from .callidus.material_writer import NewProduct, extend_template
from .drawing import Legend, LegendEntry, Room, find_rooms, read_drawing, read_legend
from .drawing.pdf_reader import PictureOnly
from .drawing.picture import AREA_CHECK, read_picture
from .drawing.products import suggest_materials
from .drawing.to_callidus import room_name, to_records

BACKGROUND_DPI = 300  # what Callidus backgrounds use
MIN_SCORE = 2.5  # below this a product suggestion is not offered
NOT_A_PRODUCT = {"NONE"}


@dataclass
class Finish:
    """One finish on the drawing: a legend entry, or a code written in rooms."""

    code: str
    kind: str  # "floor" or "skirting"
    description: str
    fill: tuple[int, int, int] | None
    in_legend: bool
    custom_code: bool  # the drawing wrote this code itself (F1-A), rather than "FLOOR FINISH TYPE 1"
    amount: float  # m2 for floors, m for skirting, found on this drawing
    rooms: int
    suggestion: str | None
    why: str


@dataclass
class Analysis:
    pdf: Path
    page: int
    scale: float
    width_m: float
    height_m: float
    rooms: list[Room]
    legend: Legend
    report: dict
    finishes: list[Finish]
    materials: list[str]  # product names in the starter file, in file order
    picture: bool = False  # read from a picture of the drawing (scanned or flattened), not its linework


def pdf_pages(pdf: str | Path) -> int:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(pdf))
    try:
        return len(doc)
    finally:
        doc.close()


def plan_pages(pdf: str | Path) -> list[int]:
    """Pages that look like finishes plans: a drawing scale and the word
    "finish" on the sheet. Cover sheets, schedules and details are skipped.
    Fast: reads the text only."""
    import re

    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(pdf))
    try:
        found = []
        for i in range(len(doc)):
            page = doc[i]
            tp = page.get_textpage()
            text = tp.get_text_range().upper()
            tp.close()
            page.close()
            if re.search(r"1\s*:\s*\d{2,4}", text) and "FINISH" in text:
                found.append(i)
        return found
    finally:
        doc.close()


def calback_sheets(path: str | Path) -> int:
    return len(read_calback(path).sheets)


def analyse(
    pdf: str | Path, products: str | Path | list[str], page: int = 0, exclude: list[tuple] | None = None,
    min_opening: float | None = None,
    scale: float | None = None,
    sheet: str | None = None,
) -> Analysis:
    """``products``: the product names to suggest from, or a Callidus .est holding them.
    ``min_opening``: openings smaller than this (m2) are measured through.
    ``scale`` (1:N) and ``sheet`` (A0..A4): only needed when the drawing does not
    show them; the sheet size only matters for a picture of a drawing."""
    pdf = Path(pdf)
    kwargs = {} if min_opening is None else {"min_opening": min_opening}
    try:
        dwg = read_drawing(pdf, page=page, scale=scale)
    except PictureOnly:
        dwg = None
    if dwg is not None:
        legend = read_legend(dwg)
        rooms, report = find_rooms(dwg, legend, exclude=exclude, **kwargs)
        scale, width_m, height_m = dwg.scale, dwg.width_m, dwg.height_m
    else:
        pic = read_picture(pdf, page=page, sheet=sheet, scale=scale, **kwargs)
        legend, rooms, report = pic.legend, pic.rooms, pic.report
        scale, width_m, height_m = pic.scale, pic.width_m, pic.height_m
    if isinstance(products, (str, Path)):
        products = [m.name for m in read_est(products).materials]
    materials = [m for m in products if m not in NOT_A_PRODUCT]

    entries: list[LegendEntry] = list(legend.entries)
    in_legend = {id(e) for e in entries}
    for r in rooms:  # finishes found by a tag written in the rooms, not in the legend
        if r.finish is not None and id(r.finish) not in in_legend and r.finish.code not in {e.code for e in entries}:
            entries.append(r.finish)
    suggestions = {}
    for kind in ("floor", "skirting"):
        for s in suggest_materials([e for e in entries if e.kind == kind], materials):
            suggestions[s.legend_code] = s

    finishes = []
    for e in entries:
        if e.kind == "floor":
            mine = [r for r in rooms if r.finish is not None and r.finish.code == e.code]
            amount = sum(r.area for r in mine)
        else:
            mine = [r for r in rooms if e.code in r.skirting_lengths()]
            amount = sum(r.skirting_lengths()[e.code] for r in mine)
        s = suggestions.get(e.code)
        good = s is not None and s.material is not None and s.score >= MIN_SCORE
        finishes.append(
            Finish(
                code=e.code,
                kind=e.kind,
                description=e.description,
                fill=tuple(e.fill) if e.fill else None,
                in_legend=id(e) in in_legend,
                custom_code=bool(e.custom_code),
                amount=round(amount, 2),
                rooms=len(mine),
                suggestion=s.material if good else None,
                why=s.why if good else "",
            )
        )
    return Analysis(
        pdf=pdf, page=page, scale=scale, width_m=width_m, height_m=height_m,
        rooms=rooms, legend=legend, report=report, finishes=finishes, materials=materials,
        picture=dwg is None,
    )


def summary(a: Analysis) -> dict:
    """Everything the review screen shows, as plain JSON."""
    rooms = []
    for i, r in enumerate(rooms_in_order(a)):
        p = r.geom.representative_point()
        rooms.append(
            {
                "id": i,
                "label": r.label,
                "finish": r.finish.code if r.finish else None,
                "area_m2": round(r.area, 2),
                "skirting_m": {k: round(v, 2) for k, v in r.skirting_lengths().items()},
                "status": r.status,
                "reasons": r.reasons,
                "at": [round(p.x, 2), round(p.y, 2)],
                "bounds": [round(v, 2) for v in r.geom.bounds],
            }
        )
    rep = a.report
    return {
        "scale": a.scale,
        "sheet_m": [round(a.width_m, 3), round(a.height_m, 3)],
        "materials": a.materials,
        "finishes": [
            {
                "code": f.code, "kind": f.kind, "description": f.description,
                "fill": list(f.fill) if f.fill else None, "in_legend": f.in_legend, "custom_code": f.custom_code,
                "amount": f.amount, "rooms": f.rooms, "suggestion": f.suggestion, "why": f.why,
            }
            for f in a.finishes
        ],
        "rooms": rooms,
        "manual_finishes": list(rep.get("manual_finishes", [])),
        "unknown_fills": [
            {"colour": list(c), "area_m2": v} for c, v in sorted(rep.get("unknown_fills", {}).items(), key=lambda kv: -kv[1])
        ],
        "tagged_not_in_legend": rep.get("tagged_not_in_legend", {}),
        "picture": _picture_summary(a),
    }


def _picture_summary(a: Analysis) -> dict | None:
    if not getattr(a, "picture", False):
        return None
    rep = a.report
    pa = rep.get("printed_areas", {})
    return {
        "sheet": rep.get("sheet"),
        "sources": rep.get("sources", {}),
        "printed_areas": dict(pa, off=None if pa.get("ratio") is None else round(abs(pa["ratio"] - 1) * 100, 1)),
        "agrees": pa.get("ratio") is not None and abs(pa["ratio"] - 1) <= AREA_CHECK,
        "unclear_codes": rep.get("unclear_codes", []),
        "colour_twins": rep.get("colour_twins", []),
        "shape_note": rep.get("shape_note"),
    }


def _room_order(r: Room):
    return (r.label == "", r.label, -r.area)


def rooms_in_order(a: Analysis) -> list[Room]:
    """The rooms in the order the summary numbers them (its "id")."""
    return sorted(a.rooms, key=_room_order)


def apply_checks(a: Analysis, decisions: dict[int, str]) -> tuple[Analysis, list[Room]]:
    """The estimator's room checks, by summary id: "ok" (the outline is right,
    so the room is no longer marked to check) or "by_hand" (the outline is
    wrong or the room is measured by hand, so it is left out of Callidus).
    Returns a copy of the analysis and the rooms left out."""
    import copy

    out, by_hand = [], []
    for i, r in enumerate(rooms_in_order(a)):
        d = decisions.get(i)
        if d == "by_hand":
            by_hand.append(r)
            continue
        if d == "ok" and r.status != "Verified":
            r = copy.copy(r)
            r.status = "Verified"
            r.reasons = [f"checked by the estimator ({'; '.join(r.reasons)})"] if r.reasons else []
        out.append(r)
    b = copy.copy(a)
    b.rooms = out
    return b, by_hand


def render_room(a: Analysis, room_id: int, out_png: str | Path, page=None, dpi: int = 100,
                margin_m: float = 3.0) -> Path:
    """A close-up of one room: the drawing around it with its outline drawn
    heavily and the other rooms faintly. ``page`` is the page already rendered
    at ``dpi`` (grey), to save rendering it for every room."""
    from PIL import Image, ImageDraw

    room = rooms_in_order(a)[room_id]
    img = page if page is not None else _page_image(a, dpi)
    img = img.convert("RGB")
    k = img.width / a.width_m
    x0, y0, x1, y1 = room.geom.bounds
    span = max(x1 - x0, y1 - y0)
    m = max(margin_m, span * 0.25)
    # sheet y is negative down the page from the top: pixel y = height + y * k
    left, right = max(0, int((x0 - m) * k)), min(img.width, int((x1 + m) * k))
    top, bottom = max(0, int(img.height + (y0 - m) * k)), min(img.height, int(img.height + (y1 + m) * k))
    if top > bottom:
        top, bottom = bottom, top
    crop = img.crop((left, top, right, bottom))
    over = Image.new("RGBA", crop.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(over)

    def pts(poly):
        return [(x * k - left, img.height + y * k - top) for x, y in poly.exterior.coords]

    for other in a.rooms:
        if other is room or not other.geom.intersects(room.geom.buffer(m)):
            continue
        for poly in [other.geom] if other.geom.geom_type == "Polygon" else list(other.geom.geoms):
            d.polygon(pts(poly), outline=(120, 120, 120, 160), width=1)
    for poly in [room.geom] if room.geom.geom_type == "Polygon" else list(room.geom.geoms):
        d.polygon(pts(poly), fill=(230, 140, 0, 60), outline=(200, 90, 0, 255), width=3)
        for hole in poly.interiors:
            d.line([(x * k - left, img.height + y * k - top) for x, y in hole.coords], fill=(200, 90, 0, 255), width=2)
    crop = Image.alpha_composite(crop.convert("RGBA"), over).convert("RGB")
    out_png = Path(out_png)
    crop.save(out_png, optimize=True)
    return out_png


@dataclass
class Outputs:
    est: Path
    calback: Path
    review: Path
    readme: Path
    written: int
    written_m2: float
    left_out: list[Room] = field(default_factory=list)
    quantities: Path | None = None


def write_outputs(
    a: Analysis,
    starter_est: str | Path,
    calback_template: str | Path,
    finish_products: dict[str, str],
    skirting_products: dict[str, str],
    out_dir: str | Path,
    name: str,
    new_products: list[NewProduct] | None = None,
    wastage: dict[str, float] | None = None,
) -> Outputs:
    """Write the pre-measured job. ``finish_products`` maps a finish code to a
    product name in the starter file, or in ``new_products`` (products added
    to the job, each copying the settings of a product already in it);
    finishes not mapped are left out. ``skirting_products`` does the same for
    skirting put on room edges."""
    template, index = extend_template(starter_est, list(new_products or []))
    unknown = [p for p in list(finish_products.values()) + list(skirting_products.values()) if p not in index]
    if unknown:
        raise ValueError(f"not in the starter file: {', '.join(sorted(set(unknown)))}")
    fmap = {code: index[p] for code, p in finish_products.items()}
    smap = {code: index[p] for code, p in skirting_products.items()}

    written, left_out = to_records(a.rooms, fmap, smap)
    records = [w.record for w in written]
    check_rooms(records, set(index.values()))
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    est_path = out_dir / f"{name}.est"
    est_path.write_bytes(build_job(template, records))
    back = read_est(est_path)  # fails loudly if we wrote something we cannot read back
    if {m.name: m.index for m in back.materials} != index or len(back.rooms) != len(records):
        raise ValueError("the written file does not read back as written")

    img = _page_image(a, BACKGROUND_DPI, bilevel=True)
    calback_path = Path(str(est_path) + ".CalBack")
    write_calback(calback_template, calback_path, encode_tiff(img, dpi=BACKGROUND_DPI),
                  sheet_extents(img.width, img.height, BACKGROUND_DPI, a.scale))

    names = {id(w.room): w.name for w in written}
    review = out_dir / f"{name} - review.csv"
    review.write_text(_review_csv(a, names, finish_products), encoding="utf-8-sig")
    readme = out_dir / "READ ME FIRST.txt"
    readme.write_text(_readme(a, name, written, left_out, finish_products), encoding="utf-8")
    quantities = out_dir / f"{name} - quantities.csv"
    quantities.write_text(
        _quantities_csv([w.room for w in written], finish_products, skirting_products, wastage or {}),
        encoding="utf-8-sig",
    )
    return Outputs(est_path, calback_path, review, readme, len(written),
                   round(sum(w.room.area for w in written), 2), left_out, quantities)


def quantities(rooms: list[Room], finish_products: dict[str, str], skirting_products: dict[str, str]) -> dict:
    """Net quantity per product over the rooms written: m2 for floors, m for skirting."""
    out: dict[str, tuple[str, float]] = {}
    for r in rooms:
        if r.finish is not None and r.finish.code in finish_products:
            p = finish_products[r.finish.code]
            out[p] = ("m2", out.get(p, ("m2", 0.0))[1] + r.area)
        for code, length in r.skirting_lengths().items():
            if code in skirting_products:
                p = skirting_products[code]
                out[p] = ("m", out.get(p, ("m", 0.0))[1] + length)
    return out


def _quantities_csv(rooms, finish_products, skirting_products, wastage: dict[str, float]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Product", "Unit", "Net quantity", "Wastage %", "Quantity with wastage", "Note"])
    for product, (unit, net) in sorted(quantities(rooms, finish_products, skirting_products).items()):
        pct = float(wastage.get(product, 0) or 0)
        w.writerow([product, unit, f"{net:.2f}", f"{pct:g}", f"{net * (1 + pct / 100):.2f}",
                    "net from the drawing; Callidus adds cutting waste when the rooms are laid out"])
    return buf.getvalue()


def outline_area(room: Room) -> float:
    """Area of the outline as written to Callidus (openings not cut out)."""
    from shapely.geometry import Polygon

    return Polygon(room.geom.exterior).area


def _review_csv(a: Analysis, names: dict[int, str], finish_products: dict[str, str]) -> str:
    skirtings = [f.code for f in a.finishes if f.kind == "skirting"]
    desc = {f.code: f.description for f in a.finishes}
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Room", "Drawing finish", "Legend description", "Callidus product", "Net area m2",
                *[f"Skirting {c} m" for c in skirtings], "Status", "Reasons", "In Callidus file"])
    n = 0
    for r in sorted(a.rooms, key=_room_order):
        code = r.finish.code if r.finish else ""
        product = finish_products.get(code, "")
        reasons = list(r.reasons)
        if id(r) in names:
            room, where = names[id(r)], "yes"
        else:
            n += 1
            room, where = room_name(r, n).removeprefix("CHECK - "), "no"
            reasons.insert(0, "no Callidus product chosen for this finish" if r.finish else "finish not identified")
        sk = r.skirting_lengths()
        w.writerow([room, code, desc.get(code, ""), product, f"{r.area:.2f}",
                    *[f"{sk.get(c, 0):.2f}" for c in skirtings],
                    r.status if where == "yes" else "Not written", "; ".join(reasons), where])
    for code in a.report.get("manual_finishes", []):
        w.writerow(["(not measured)", code, desc.get(code, ""), "", "", *["" for _ in skirtings],
                    "Measure by hand", "drawn in black linework only, which the software cannot yet tell apart", "no"])
    return buf.getvalue()


def _readme(a: Analysis, name: str, written, left_out, finish_products) -> str:
    manual = a.report.get("manual_finishes", [])
    unpriced = sorted({r.finish.code for r in left_out if r.finish})
    lines = [
        f"{name}: pre-measured from {a.pdf.name}",
        "",
        "Copy these files to a local folder (not the Z: drive) before opening in Callidus.",
        "",
        "What is in it",
        "-------------",
        f"{len(written)} rooms, {sum(w.room.area for w in written):.2f} m2, read from the drawing:",
        "- Outlines from the finish colours on the drawing, to the wall face.",
        "- Room names from the room tags on the drawing.",
        "- Products as confirmed on the platform.",
        "- Skirting on the room edges where the drawing shows it, for the skirting types given a product.",
        "- Background made from the same PDF.",
        "",
        'Rooms with "CHECK - " in front of the name need a look. The reasons are in the review file.',
        "No room has a layout yet: select a room and press F9 to lay it out.",
        "",
        "Not in the Callidus file",
        "------------------------",
    ]
    if unpriced:
        lines.append(f"- Finishes with no product chosen: {', '.join(unpriced)} "
                     f"({sum(r.area for r in left_out if r.finish):.2f} m2). Listed in the review file.")
    if manual:
        lines.append(f"- Finishes drawn in black linework only (often matwells): {', '.join(manual)}. Measure by hand.")
    unknown = a.report.get("unknown_fills", {})
    if unknown:
        lines.append(f"- Colours on the plan that are not in the legend ({len(unknown)} colours). "
                     "Usually walls, furniture or other trades; worth a glance on the drawing.")
    if not (unpriced or manual or unknown):
        lines.append("- Nothing.")
    return "\n".join(lines) + "\n"


def _page_image(a: Analysis, dpi: int, bilevel: bool = False):
    """The drawing as an image at ``dpi`` on the printed sheet: black and white
    for a Callidus background, grey otherwise. A picture of a drawing is resized
    to the sheet's real size, so it lines up with the rooms."""
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = None
    if not getattr(a, "picture", False):
        if bilevel:
            return render_pdf_page(a.pdf, a.page, dpi)
        import pypdfium2 as pdfium

        doc = pdfium.PdfDocument(str(a.pdf))
        try:
            return doc[a.page].render(scale=dpi / 72, grayscale=True).to_pil()
        finally:
            doc.close()
    from .drawing.picture import load_picture

    img = load_picture(a.pdf, a.page).convert("L")
    px_per_m = 1000 / a.scale / 25.4 * dpi
    size = (max(1, round(a.width_m * px_per_m)), max(1, round(a.height_m * px_per_m)))
    if size != img.size:
        img = img.resize(size, Image.LANCZOS)
    return img.convert("1") if bilevel else img


def render_preview(a: Analysis, out_png: str | Path, dpi: int = 60, highlight: set[str] | None = None) -> Path:
    """A small picture of the drawing with every room outlined: green when
    verified, amber when it needs review, blue when its finish has no product
    (``highlight`` is the set of finish codes with a product; None = all)."""
    from PIL import Image, ImageDraw

    img = _page_image(a, dpi).convert("L").convert("RGB")
    img = Image.blend(img, Image.new("RGB", img.size, "white"), 0.45)
    k = img.width / a.width_m  # pixels per sheet metre
    over = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(over)
    for r in a.rooms:
        if highlight is not None and (r.finish is None or r.finish.code not in highlight):
            colour = (40, 100, 220)
        elif r.status == "Verified":
            colour = (22, 140, 70)
        else:
            colour = (230, 140, 0)
        for poly in [r.geom] if r.geom.geom_type == "Polygon" else list(r.geom.geoms):
            pts = [(x * k, img.height + y * k) for x, y in poly.exterior.coords]
            d.polygon(pts, fill=colour + (70,), outline=colour + (255,), width=2)
    img = Image.alpha_composite(img.convert("RGBA"), over).convert("RGB")
    out_png = Path(out_png)
    img.save(out_png, optimize=True)
    return out_png
