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


def pdf_pages(pdf: str | Path) -> int:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(pdf))
    try:
        return len(doc)
    finally:
        doc.close()


def calback_sheets(path: str | Path) -> int:
    return len(read_calback(path).sheets)


def analyse(
    pdf: str | Path, products: str | Path | list[str], page: int = 0, exclude: list[tuple] | None = None
) -> Analysis:
    """``products``: the product names to suggest from, or a Callidus .est holding them."""
    pdf = Path(pdf)
    dwg = read_drawing(pdf, page=page)
    legend = read_legend(dwg)
    rooms, report = find_rooms(dwg, legend, exclude=exclude)
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
                amount=round(amount, 2),
                rooms=len(mine),
                suggestion=s.material if good else None,
                why=s.why if good else "",
            )
        )
    return Analysis(
        pdf=pdf, page=page, scale=dwg.scale, width_m=dwg.width_m, height_m=dwg.height_m,
        rooms=rooms, legend=legend, report=report, finishes=finishes, materials=materials,
    )


def summary(a: Analysis) -> dict:
    """Everything the review screen shows, as plain JSON."""
    rooms = []
    for i, r in enumerate(sorted(a.rooms, key=_room_order)):
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
                "fill": list(f.fill) if f.fill else None, "in_legend": f.in_legend,
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
    }


def _room_order(r: Room):
    return (r.label == "", r.label, -r.area)


@dataclass
class Outputs:
    est: Path
    calback: Path
    review: Path
    readme: Path
    written: int
    written_m2: float
    left_out: list[Room] = field(default_factory=list)


def write_outputs(
    a: Analysis,
    starter_est: str | Path,
    calback_template: str | Path,
    finish_products: dict[str, str],
    skirting_products: dict[str, str],
    out_dir: str | Path,
    name: str,
    new_products: list[NewProduct] | None = None,
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

    img = render_pdf_page(a.pdf, a.page, BACKGROUND_DPI)
    calback_path = Path(str(est_path) + ".CalBack")
    write_calback(calback_template, calback_path, encode_tiff(img, dpi=BACKGROUND_DPI),
                  sheet_extents(img.width, img.height, BACKGROUND_DPI, a.scale))

    names = {id(w.room): w.name for w in written}
    review = out_dir / f"{name} - review.csv"
    review.write_text(_review_csv(a, names, finish_products), encoding="utf-8-sig")
    readme = out_dir / "READ ME FIRST.txt"
    readme.write_text(_readme(a, name, written, left_out, finish_products), encoding="utf-8")
    return Outputs(est_path, calback_path, review, readme, len(written),
                   round(sum(w.room.area for w in written), 2), left_out)


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


def render_preview(a: Analysis, out_png: str | Path, dpi: int = 60, highlight: set[str] | None = None) -> Path:
    """A small picture of the drawing with every room outlined: green when
    verified, amber when it needs review, grey when its finish has no product
    (``highlight`` is the set of finish codes with a product; None = all)."""
    import pypdfium2 as pdfium
    from PIL import Image, ImageDraw

    doc = pdfium.PdfDocument(str(a.pdf))
    try:
        img = doc[a.page].render(scale=dpi / 72, grayscale=True).to_pil().convert("RGB")
    finally:
        doc.close()
    img = Image.blend(img, Image.new("RGB", img.size, "white"), 0.45)
    k = 1000 / a.scale / 25.4 * dpi  # pixels per sheet metre
    over = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(over)
    for r in a.rooms:
        if highlight is not None and (r.finish is None or r.finish.code not in highlight):
            colour = (120, 120, 120)
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
