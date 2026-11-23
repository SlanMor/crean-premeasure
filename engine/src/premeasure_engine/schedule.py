"""The finishes schedule: which floor finish each room gets, by room number.

Crean price from the main contractor's finishes schedule where there is one,
not from the drawing's legend. The schedule settles what the plan cannot:
two finishes the legend draws in the same colour (Cashel P3: Rosemary Green
in 2.5 mm and in 3.5 mm acoustic) and rooms where the drawing and the
schedule disagree.

A schedule is a table: room numbers in one column, the floor finish in
another, one row per room or per group of rooms. Rows are found from the
room numbers the drawing itself carries, so a number on the schedule only
counts when the plan has a room with that number. A row's floor finish is
matched to the legend by the product and colour numbers both quote
(335535, AQI2013, X2546R11): names alone are too alike to trust.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

ID = re.compile(r"\b(?:AQI\d{4}|X\d{4}R\d{2}|[A-Z]{1,3}\d{4,7}|\d{4,7})\b")


STANDARD = re.compile(r"\b(?:BS\s*)?(?:EN|ISO|DIN)(?:\s*ISO)?\s*\d[\d\-:]*", re.I)


def product_ids(text: str) -> set[str]:
    """Product and colour numbers quoted in a description (standards such as
    EN 13845 left out: many products quote the same one)."""
    return set(ID.findall(STANDARD.sub(" ", text).upper()))


@dataclass
class ScheduleRow:
    rooms: set[str]  # room numbers, as on the drawing (G07, F11)
    floor: str  # the floor finish text
    page: int

    @property
    def ids(self) -> set[str]:
        return product_ids(self.floor)


@dataclass
class Schedule:
    source: str
    rows: list[ScheduleRow] = field(default_factory=list)

    def for_room(self, code: str) -> list[ScheduleRow]:
        code = code.upper()
        return [r for r in self.rows if code in r.rooms]


def _segments(page):
    """Text runs with their boxes (left, top, right, bottom; top down, in
    points). Runs pdfium splits mid-word ("F0" + "2") are joined again."""
    tp = page.get_textpage()
    h = page.get_height()
    raw = []
    for i in range(tp.count_rects()):
        l, b, r, t = tp.get_rect(i)
        text = tp.get_text_bounded(l, b, r, t).strip()
        if text:
            raw.append([l, h - t, r, h - b, text])
    raw.sort(key=lambda s: (round(s[1]), s[0]))
    out: list[list] = []
    for s in raw:
        last = out[-1] if out else None
        if last and abs(s[1] - last[1]) < 1.5 and 0 <= s[0] - last[2] < 1.5:
            last[2], last[3], last[4] = s[2], max(last[3], s[3]), last[4] + s[4]
        else:
            out.append(s)
    return [tuple(s) for s in out]


def _columns(segs):
    """The x ranges of the room number and floor finish columns, from the
    header row. Headers are often centred over their column, so a column runs
    from halfway across the gap to the header on its left to halfway across
    the gap to the one on its right."""
    floor = next((s for s in segs if re.fullmatch(r"FLOOR\s+FINISH(ES)?", s[4].upper())), None)
    if floor is None:
        return None, None
    lh = floor[3] - floor[1]
    line = sorted((s for s in segs if abs(s[1] - floor[1]) < 0.5 * lh), key=lambda s: s[0])

    def span(head):
        i = line.index(head)
        left = (line[i - 1][2] + head[0]) / 2 if i > 0 else 0.0
        right = (head[2] + line[i + 1][0]) / 2 if i + 1 < len(line) else 1e9
        return left, right

    def is_room_head(s) -> bool:
        up = s[4].upper().strip()
        if abs(s[1] - floor[1]) >= 0.5 * lh:
            return False
        if re.match(r"ROOM\s*(NO|NUMBER|REF)", up):
            return True
        # "ROOM" with "NO." on the line below it
        return up == "ROOM" and any(0 < o[1] - s[1] < 2.5 * lh and abs(o[0] - s[0]) < 15
                                    and o[4].upper().startswith("NO") for o in segs)

    room = next((s for s in line if is_room_head(s)), None)
    return (span(room) if room else None), span(floor)


def read_schedule(pdf: str | Path, room_codes: set[str]) -> Schedule:
    """Rows of the schedule for the rooms the drawing has (``room_codes``)."""
    import pypdfium2 as pdfium

    codes = {c.upper() for c in room_codes if c}
    sched = Schedule(source=Path(pdf).name)
    if not codes:
        return sched
    doc = pdfium.PdfDocument(str(pdf))
    for n, page in enumerate(doc):
        segs = _segments(page)
        room_x, floor_x = _columns(segs)
        if floor_x is None:
            continue
        header_bottom = max(s[3] for s in segs if floor_x[0] <= s[0] < floor_x[1] and "FINISH" in s[4].upper())
        tokens = []
        for s in segs:
            if s[1] <= header_bottom:
                continue
            if room_x is not None and not (room_x[0] <= s[0] < room_x[1]):
                continue
            for t in re.findall(r"[A-Z0-9][A-Z0-9.\-/]*", s[4].upper()):
                if t in codes:
                    tokens.append((s[1], s[3], t))
        if not tokens:
            continue
        tokens.sort()
        lh = max(1.0, min(b - t for t, b, _ in tokens))
        groups: list[list] = []
        for tok in tokens:
            if groups and tok[0] - groups[-1][-1][1] <= 1.2 * lh:
                groups[-1].append(tok)
            else:
                groups.append([tok])
        tops = [g[0][0] - 0.5 * lh for g in groups] + [1e9]
        for i, g in enumerate(groups):
            text = " ".join(s[4] for s in sorted(segs, key=lambda s: (s[1], s[0]))
                            if tops[i] <= s[1] < tops[i + 1] and floor_x[0] <= s[0] < floor_x[1])
            sched.rows.append(ScheduleRow(rooms={t for _, _, t in g}, floor=re.sub(r"\s+", " ", text).strip(), page=n))
    return sched


def apply_schedule(rooms, legend, sched: Schedule) -> dict:
    """Settle shared-colour rooms from the schedule, and flag rooms where the
    schedule names a different finish from the drawing. Nothing is changed
    on a guess: a room is settled only when its schedule row quotes the
    numbers of exactly one of the finishes its colour could be."""
    floors = [e for e in legend.floors() if e.description]
    by_code = {e.code: e for e in floors}
    report = {"source": sched.source, "rows": len(sched.rows), "settled_m2": 0.0, "settled": [],
              "differs": [], "rooms_found": 0}

    def matching(row_ids: set[str], entries):
        return [e for e in entries if row_ids & product_ids(e.description)]

    for r in rooms:
        if r.finish is None or not r.codes:
            continue
        rows = [row for c in r.codes for row in sched.for_room(c)]
        if not rows:
            continue
        report["rooms_found"] += 1
        row_ids = set().union(*(row.ids for row in rows))
        code = r.finish.code
        label = " / ".join(r.codes)
        if " or " in code:
            pair = [by_code[c] for c in code.split(" or ") if c in by_code]
            hits = matching(row_ids, pair)
            if len(hits) == 1:
                r.finish = hits[0]
                r.reasons = [w for w in r.reasons if "same colour" not in w]
                if not r.reasons:
                    r.status = "Verified"
                report["settled_m2"] += r.area
                report["settled"].append({"room": label, "finish": hits[0].code, "area_m2": round(r.area, 2)})
            continue
        hits = matching(row_ids, floors)
        if row_ids and not hits and product_ids(r.finish.description):
            # the schedule names a product this drawing's legend does not have at all
            says = ", ".join(sorted(row_ids))
            r.flag(f"the finishes schedule gives {says} for this room, which is not in this drawing's legend "
                   f"(the drawing says {code}): check which is current")
            report["differs"].append({"room": label, "drawing": code, "schedule": says,
                                      "area_m2": round(r.area, 2)})
        elif hits and r.finish not in hits:
            known = set().union(*(product_ids(e.description) for e in floors))
            says = ", ".join([e.code for e in hits] + sorted(row_ids - known))
            r.flag(f"the finishes schedule gives {says} for this room, the drawing {code}: check which is current")
            report["differs"].append({"room": label, "drawing": code, "schedule": says,
                                      "area_m2": round(r.area, 2)})
    report["settled_m2"] = round(report["settled_m2"], 2)
    return report
