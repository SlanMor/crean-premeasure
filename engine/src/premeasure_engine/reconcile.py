"""Reconcile Callidus files against a measure spreadsheet.

The spreadsheet is Callidus's room report as Crean uses it: columns
title, matName, RoomName, net-m2, base-lm (header in row 1, any sheet).
Area rows have net-m2; linear rows (skirting, frames) have base-lm.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from openpyxl import load_workbook

from .callidus.est_reader import EstFile
from .quantities import edge_products, net_area, room_level_skirting

AREA_TOLERANCE_M2 = 0.01
LENGTH_TOLERANCE_M = 0.01
HEADERS = ("title", "matname", "roomname", "net-m2", "base-lm")
# Column names seen in Crean's spreadsheets, mapped to the Callidus export names.
HEADER_ALIASES = {
    "material name": "matname",
    "room name": "roomname",
    "net (m2)": "net-m2",
    "skirting length (lm)": "base-lm",
}


@dataclass
class SheetRow:
    row: int
    title: str
    material: str
    room: str
    net_m2: float | None
    base_lm: float | None


@dataclass
class Reconciliation:
    area_rows: int = 0
    area_matched: int = 0
    linear_rows: int = 0
    linear_matched: int = 0
    unmatched_area_rows: list[SheetRow] = field(default_factory=list)
    unmatched_linear_rows: list[SheetRow] = field(default_factory=list)
    rooms_not_in_sheet: list[tuple[str, str, str, float]] = field(default_factory=list)


def read_measure_sheet(path) -> list[SheetRow]:
    wb = load_workbook(path, data_only=True, read_only=True)
    for ws in wb.worksheets:
        rows = ws.iter_rows(values_only=True)
        header = next(rows, None)
        if not header:
            continue
        names = [str(h).strip().lower() if h is not None else "" for h in header]
        names = [HEADER_ALIASES.get(n, n) for n in names]
        if not all(h in names for h in HEADERS):
            continue
        col = {h: names.index(h) for h in HEADERS}
        out = []
        for i, r in enumerate(rows, start=2):
            room = r[col["roomname"]]
            material = r[col["matname"]]
            if room is None or material is None or str(material).endswith(" Total"):
                continue
            out.append(
                SheetRow(
                    row=i,
                    title=str(r[col["title"]] or "").strip(),
                    material=str(material).strip(),
                    room=str(room).strip(),
                    net_m2=r[col["net-m2"]],
                    base_lm=r[col["base-lm"]],
                )
            )
        return out
    raise ValueError(f"no sheet with columns {', '.join(HEADERS)} in {path}")


def reconcile(ests: list[EstFile], sheet_rows: list[SheetRow]) -> Reconciliation:
    result = Reconciliation()
    rooms = [(est, room, net_area(room, est.rooms)) for est in ests for room in est.rooms]
    used: set[int] = set()

    for row in (r for r in sheet_rows if r.net_m2 is not None):
        result.area_rows += 1
        candidates = [
            (i, est, room)
            for i, (est, room, net) in enumerate(rooms)
            if i not in used and abs(net - row.net_m2) < AREA_TOLERANCE_M2
        ]
        candidates.sort(key=lambda c: (c[2].name != row.room, c[1].title != row.title))
        if candidates:
            used.add(candidates[0][0])
            result.area_matched += 1
        else:
            result.unmatched_area_rows.append(row)

    for row in (r for r in sheet_rows if r.base_lm is not None):
        result.linear_rows += 1
        same_name = [(est, room) for est, room, _ in rooms if room.name == row.room]
        # Several Callidus rooms can share a name: try them together and one by one.
        per_room = [edge_products(room, est).get(row.material, 0.0) for est, room in same_name]
        options = [sum(per_room), *per_room] + [room_level_skirting(room) for _, room in same_name]
        if any(abs(v - row.base_lm) < LENGTH_TOLERANCE_M for v in options):
            result.linear_matched += 1
        else:
            result.unmatched_linear_rows.append(row)

    for i, (est, room, net) in enumerate(rooms):
        if i not in used and (room.material is None or room.material.name != "NONE"):
            result.rooms_not_in_sheet.append(
                (est.title, room.name, room.material.name if room.material else "?", round(net, 3))
            )
    return result
