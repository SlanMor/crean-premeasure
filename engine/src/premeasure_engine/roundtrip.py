"""The checked Callidus job back from the estimator: Crean's quantities
spreadsheet from it, and how it compares with what was pre-measured.

The spreadsheet is the Callidus room report as Crean lays it out (Cashel,
Shellybanks and Limerick all follow it): one row per room and product with
title, material name, room name, net m2 and skirting lm; a subtotal under
each product; a grand total; and a short summary by type of product.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from statistics import median

from shapely.geometry import Polygon
from shapely.validation import make_valid

from .callidus.est_reader import EstFile, Room
from .quantities import edge_products, net_area, room_level_skirting

NONE = "NONE"  # Callidus's "no material": construction lines, voids


@dataclass
class Row:
    title: str
    material: str
    room: str
    net_m2: float | None
    base_lm: float | None


def crean_rows(est: EstFile, room_skirting: bool = False) -> list[Row]:
    """Every priced room of one Callidus job, as rows of Crean's spreadsheet.
    Skirting set as an edge product gets its own row under that product.
    ``room_skirting``: the job sets skirting on the room instead (Limerick),
    so it goes in the room's own row."""
    rows = []
    for room in est.rooms:
        name = room.material.name if room.material else f"material #{room.material_index}"
        if name == NONE:
            continue
        rows.append(Row(est.title, name.strip(), room.name.strip(), round(net_area(room, est.rooms), 3),
                        round(room_level_skirting(room), 3) if room_skirting else None))
        for product, length in edge_products(room, est).items():
            if length > 0:
                rows.append(Row(est.title, product.strip(), room.name.strip(), None, round(length, 3)))
    return rows


def crean_workbook(ests: list[EstFile], kinds: dict[str, str] | None = None, room_skirting: bool = False,
                   title: str = "") -> bytes:
    """Crean's quantities spreadsheet for one or more Callidus jobs (one per
    floor). ``kinds`` names the type of each product (Vinyl, Carpet tiles,
    Coved skirting...) for the summary at the bottom."""
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    rows = [r for est in ests for r in crean_rows(est, room_skirting)]
    groups: dict[str, list[Row]] = {}
    for r in rows:
        groups.setdefault(r.material, []).append(r)

    wb = Workbook()
    ws = wb.active
    ws.title = "ROOMS"
    bold = Font(bold=True)
    ws.append(["Title", "Material Name", "Room Name", "Net (m2)", "Skirting Length (lm)"])
    for c in ws[1]:
        c.font = bold
    totals: dict[str, int] = {}  # product -> row of its total
    for material in sorted(groups, key=str.lower):
        first = ws.max_row + 1
        for r in groups[material]:
            ws.append([r.title, r.material, r.room, r.net_m2, r.base_lm])
        last = ws.max_row
        ws.append([None, f"{material} Total", None, f"=SUBTOTAL(9,D{first}:D{last})", f"=SUBTOTAL(9,E{first}:E{last})"])
        totals[material] = ws.max_row
        for c in ws[ws.max_row]:
            c.font = bold
    end = ws.max_row
    ws.append([None, "Grand Total", None, f"=SUBTOTAL(9,D2:D{end})", f"=SUBTOTAL(9,E2:E{end})"])
    for c in ws[ws.max_row]:
        c.font = bold

    # the summary by type, as Crean adds it under the report
    if kinds is not None:
        by_kind: dict[tuple[str, str], list[str]] = {}
        for material, rows_ in groups.items():
            linear = all(r.net_m2 is None for r in rows_)
            kind = kinds.get(material) or ("Other skirting and trims" if linear else "Other floors")
            by_kind.setdefault((kind, "lm" if linear else "m2"), []).append(material)
        ws.append([])
        ws.append([])
        ws.append([None, None, "Totals", None, None])
        ws.cell(ws.max_row, 3).font = bold
        for (kind, unit), materials in sorted(by_kind.items(), key=lambda kv: (kv[0][1] == "lm", kv[0][0])):
            col = "E" if unit == "lm" else "D"
            ws.append([None, None, kind, "=" + "+".join(f"{col}{totals[m]}" for m in sorted(materials)), unit])

    for i, width in enumerate((14, 60, 40, 12, 20), start=1):
        ws.column_dimensions[get_column_letter(i)].width = width
    for row in ws.iter_rows(min_row=2, min_col=4, max_col=5):
        for c in row:
            c.number_format = "0.000"
    ws.freeze_panes = "A2"

    st = wb.create_sheet("Stairs")
    st.append(["Stairs (measured by hand)"])
    st["A1"].font = bold
    st.append([])
    st.append(["Stair", "Flight", "Steps", "Length (m)", "Total lm", "", "Angles (nr)", "Landing trim (lm)"])
    for c in st[3]:
        c.font = bold
    for n in range(4, 10):
        st.append([None, None, None, None, f"=C{n}*D{n}"])
    for i, width in enumerate((10, 12, 8, 12, 10, 4, 12, 18), start=1):
        st.column_dimensions[get_column_letter(i)].width = width
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# --- how the checked job compares with the pre-measure ----------------------------


@dataclass
class RoomChange:
    ours: str | None  # room name as pre-measured (None: added by the estimator)
    final: str | None  # room name in the checked job (None: removed)
    material_ours: str | None
    material_final: str | None
    area_ours: float | None
    area_final: float | None
    redrawn: bool = False  # unpaired, but the floor is there: split or joined differently

    @property
    def change(self) -> float | None:
        if self.area_ours is None or self.area_final in (None, 0):
            return None
        return self.area_ours / self.area_final - 1

    @property
    def state(self) -> str:
        if self.redrawn:
            return "split or joined"
        if self.ours is None:
            return "added"
        if self.final is None:
            return "removed"
        if self.material_ours != self.material_final:
            return "product changed"
        if abs(self.change or 0) <= SAME:
            return "unchanged"
        return "outline changed"


SAME = 0.02  # within 2% counts as right first time
MATCH_IOU = 0.5
REDRAWN = 0.8  # an unpaired room this much inside the other job's floor was split or joined, not lost


@dataclass
class Comparison:
    rooms: list[RoomChange] = field(default_factory=list)
    area_ours: float = 0.0
    area_final: float = 0.0
    area_both: float = 0.0  # floor inside both a pre-measured outline and a final one
    floor_final: float = 0.0  # the final job's floor, rooms overlapping counted once

    @property
    def outline_agreement(self) -> float:
        """Share of the final floor that lies inside the pre-measure's outlines,
        whatever the rooms are called or how they are split."""
        return min(1.0, self.area_both / self.floor_final) if self.floor_final else 0.0

    @property
    def right_first_time(self) -> float:
        """Share of the checked job's area that was pre-measured right (same
        product, area within 2%)."""
        ok = sum(r.area_final for r in self.rooms if r.state == "unchanged")
        return ok / self.area_final if self.area_final else 0.0

    @property
    def median_change(self) -> float | None:
        """Typical difference on rooms pre-measured and kept (5 m2 and over)."""
        ch = [abs(r.change) for r in self.rooms if r.ours and r.final and r.change is not None and r.area_final >= 5]
        return median(ch) if ch else None

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.rooms:
            out[r.state] = out.get(r.state, 0) + 1
        return out

    def as_dict(self) -> dict:
        return {
            "area_ours": round(self.area_ours, 2),
            "area_final": round(self.area_final, 2),
            "right_first_time": round(self.right_first_time, 4),
            "outline_agreement": round(self.outline_agreement, 4),
            "median_change": None if self.median_change is None else round(self.median_change, 4),
            "counts": self.counts(),
            "rooms": [
                {"ours": r.ours, "final": r.final, "material_ours": r.material_ours,
                 "material_final": r.material_final, "area_ours": r.area_ours, "area_final": r.area_final,
                 "state": r.state, "change": None if r.change is None else round(r.change, 4),
                 "redrawn": r.redrawn}
                for r in self.rooms
            ],
        }


def _priced(est: EstFile) -> list[tuple[Room, object, float, str]]:
    out = []
    for room in est.rooms:
        name = room.material.name if room.material else ""
        if name == NONE or len(room.vertices) < 3:
            continue
        geom = make_valid(Polygon(room.vertices))
        out.append((room, geom, net_area(room, est.rooms), name.strip()))
    return out


def compare_jobs(ours: EstFile, final: EstFile) -> Comparison:
    """Room by room: the job as pre-measured against the job as the estimator
    saved it. Rooms are paired by where they are (outlines overlapping by half
    or more), so renaming a room does not break the pairing."""
    a, b = _priced(ours), _priced(final)
    pairs = []
    for i, (_, ga, _, _) in enumerate(a):
        for j, (_, gb, _, _) in enumerate(b):
            if not ga.intersects(gb):
                continue
            inter = ga.intersection(gb).area
            if inter <= 0:
                continue
            iou = inter / ga.union(gb).area
            if iou >= MATCH_IOU:
                pairs.append((iou, i, j))
    used_a, used_b = set(), set()
    from shapely.ops import unary_union

    union_a = unary_union([x[1] for x in a]) if a else Polygon()
    union_b = unary_union([x[1] for x in b]) if b else Polygon()
    out = Comparison(area_ours=sum(x[2] for x in a), area_final=sum(x[2] for x in b),
                     area_both=union_a.intersection(union_b).area, floor_final=union_b.area)
    for _, i, j in sorted(pairs, reverse=True):
        if i in used_a or j in used_b:
            continue
        used_a.add(i)
        used_b.add(j)
        (ra, _, na, ma), (rb, _, nb, mb) = a[i], b[j]
        out.rooms.append(RoomChange(ra.name, rb.name, ma, mb, round(na, 3), round(nb, 3)))
    def covered(g, union) -> bool:
        return g.area > 0 and g.intersection(union).area >= REDRAWN * g.area

    for i, (ra, ga, na, ma) in enumerate(a):
        if i not in used_a:
            out.rooms.append(RoomChange(ra.name, None, ma, None, round(na, 3), None, covered(ga, union_b)))
    for j, (rb, gb, nb, mb) in enumerate(b):
        if j not in used_b:
            out.rooms.append(RoomChange(None, rb.name, None, mb, None, round(nb, 3), covered(gb, union_a)))
    order = {"removed": 0, "added": 1, "product changed": 2, "outline changed": 3, "split or joined": 4,
             "unchanged": 5}
    out.rooms.sort(key=lambda r: (order[r.state], -(r.area_final or r.area_ours or 0)))
    return out
