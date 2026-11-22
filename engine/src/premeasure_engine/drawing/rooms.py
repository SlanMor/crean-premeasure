"""Find rooms and their finishes on a finishes drawing.

How it works:

1. Visible fills. Shapes painted later cover earlier ones, so each fill only
   counts where it can actually be seen. This is worked out per 1 m cell,
   top layer first.
2. Finish fills. A fill colour is a floor finish if it matches a legend
   swatch, or if the hatch drawn over it matches a legend hatch. Thin strips
   (skirting and edge bands, a few centimetres wide) are not finishes; each
   strip joins the room it runs along, so rooms reach the wall face.
3. Hatch-only finishes. Some finishes have no fill (carpet and matting on
   Cashel). They are found as unfilled floor inside the building outline that
   carries that finish's hatch.
4. Room tags (G01, F06 ...) and the name under them are attached to the room
   they sit in.
5. Every room gets a status. "Verified" only when nothing was uncertain;
   otherwise "Needs review" with the reasons, so nothing is filled in silently.

White fills are walls and masking, never floor.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import numpy as np
from shapely.geometry import MultiPolygon, Point, Polygon, box
from shapely.ops import unary_union
from shapely.strtree import STRtree

from .legend import Legend, LegendEntry
from .pdf_reader import Colour, Drawing

WHITE: Colour = (255, 255, 255)
CELL = 1.0  # metres, for the visibility pass
CLOSE = 0.02  # joins pieces of one fill split by hairline gaps (metres)
STRIP_TEST = 0.03  # a fill that mostly vanishes when shrunk by this is a strip
MIN_ROOM = 0.2  # m2
MAX_FURNITURE_HOLE = 2.0  # m2: holes smaller than this (furniture, symbols) are floor
SEAL = 1.0  # metres: closes doors and entrances when finding the building outline
BLACK: Colour = (0, 0, 0)
STAIRS = re.compile(r"\bstair", re.I)
CODE = re.compile(r"^(?=.{3,14}$)(?=.*\d)(?=.*[A-Z])[A-Z0-9]+(?:[-.][A-Z0-9]+){0,3}$")
AREA_TEXT = re.compile(r"\d\s*m(²|2)\b|^\d+(\.\d+)?$", re.I)


@dataclass
class Room:
    geom: Polygon
    finish: LegendEntry | None
    source: str  # "fill" or "hatch"
    fill: Colour | None
    codes: list[str] = field(default_factory=list)
    names: list[str] = field(default_factory=list)
    status: str = "Verified"
    reasons: list[str] = field(default_factory=list)
    # outline with a vertex wherever skirting starts or stops, and the
    # skirting type along each edge (edge i runs from vertex i to i+1)
    vertices: list[tuple[float, float]] = field(default_factory=list)
    edge_skirting: list[LegendEntry | None] = field(default_factory=list)

    def skirting_lengths(self) -> dict[str, float]:
        out: dict[str, float] = {}
        n = len(self.vertices)
        for i, e in enumerate(self.edge_skirting):
            if e is not None:
                (ax, ay), (bx, by) = self.vertices[i], self.vertices[(i + 1) % n]
                out[e.code] = out.get(e.code, 0.0) + ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5
        return out

    @property
    def area(self) -> float:
        return self.geom.area

    @property
    def label(self) -> str:
        if not self.codes:
            return ""
        tags = " / ".join(self.codes)
        names = " / ".join(_tidy(n) for n in self.names if n)
        return f"{tags} - {names}" if names else tags

    def flag(self, reason: str) -> None:
        self.status = "Needs review"
        if reason not in self.reasons:
            self.reasons.append(reason)


def _tidy(name: str) -> str:
    """Title case, keeping short words (WC, UAC, DCG) in capitals."""
    return " ".join(w if len(w) <= 3 else w.capitalize() for w in name.split())


def _parts(g) -> list[Polygon]:
    if g is None or g.is_empty:
        return []
    if isinstance(g, Polygon):
        return [g]
    return [p for p in getattr(g, "geoms", []) if isinstance(p, Polygon) and not p.is_empty] + [
        q for p in getattr(g, "geoms", []) if isinstance(p, MultiPolygon) for q in p.geoms
    ]


def visible_fills(dwg: Drawing, area) -> dict[Colour, object]:
    """For each fill colour, the part of the sheet where that colour is seen."""
    covered: dict = {}
    seen: dict = defaultdict(lambda: defaultdict(list))
    for f in reversed(dwg.fills):
        g = f.geom
        if not g.intersects(area):
            continue
        x0, y0, x1, y1 = g.bounds
        for i in range(int(x0 // CELL), int(x1 // CELL) + 1):
            for j in range(int(y0 // CELL), int(y1 // CELL) + 1):
                c = box(i * CELL, j * CELL, (i + 1) * CELL, (j + 1) * CELL)
                part = g.intersection(c)
                if part.is_empty or part.area < 1e-10:
                    continue
                cov = covered.get((i, j))
                vis = part if cov is None else part.difference(cov)
                if not vis.is_empty and vis.area > 1e-10:
                    seen[f.colour][(i, j)].append(vis)
                covered[(i, j)] = part if cov is None else cov.union(part)
    return {
        col: unary_union([unary_union(v) for v in cells.values()]).intersection(area)
        for col, cells in seen.items()
    }


class _Hatch:
    """Stroke midpoints by colour, for counting hatch inside an area."""

    def __init__(self, dwg: Drawing, colours: set | None = None):
        pts, cols, ang = [], [], []
        for s in dwg.strokes:
            if colours is not None and s.colour not in colours:
                continue
            for line in s.lines:
                for (ax, ay), (bx, by) in zip(line, line[1:]):
                    pts.append(((ax + bx) / 2, (ay + by) / 2))
                    cols.append(s.colour)
                    ang.append(np.degrees(np.arctan2(by - ay, bx - ax)) % 180)
        self.xy = np.array(pts) if pts else np.zeros((0, 2))
        self.cols = cols
        self.ang = np.array(ang)
        self.tree = STRtree([Point(p) for p in self.xy])

    def colours_in(self, geom) -> Counter:
        if geom.is_empty:
            return Counter()
        idx = self.tree.query(geom, predicate="contains")
        return Counter(self.cols[i] for i in idx)

    def parallel_share(self, geom, colour: Colour) -> float:
        """Share of that colour's strokes that run in the most common direction."""
        idx = [i for i in self.tree.query(geom, predicate="contains") if self.cols[i] == colour]
        if not idx:
            return 0.0
        a = np.round(self.ang[idx] / 5) % 36
        return float(Counter(a.tolist()).most_common(1)[0][1] / len(idx))


def _is_strip(g) -> bool:
    if g.area <= 0:
        return False
    # Eroding by d removes at most perimeter x d, so a shape that keeps 30%
    # whatever happens needs no buffer (the buffer is slow on big shapes).
    if g.length * STRIP_TEST <= 0.7 * g.area:
        return False
    return g.buffer(-STRIP_TEST).area < 0.3 * g.area


def _classify_colour(col: Colour, g, legend: Legend, hatch: _Hatch) -> tuple[LegendEntry | None, str]:
    floors = legend.floors()
    exact = [e for e in floors if e.fill == col]
    if len(exact) == 1:
        return exact[0], "legend fill"
    if len(exact) > 1:
        return None, f"fill {col} is used by more than one legend finish: " + ", ".join(e.code for e in exact)
    close = [e for e in floors if e.fill and e.fill != WHITE and sum((a - b) ** 2 for a, b in zip(e.fill, col)) <= 36]
    if len(close) == 1:
        return close[0], "legend fill"
    found = hatch.colours_in(g.buffer(-0.1))
    for c, n in found.most_common():
        if c == (0, 0, 0):
            continue
        match = [e for e in floors if e.hatch and e.hatch[0] == c]
        if n < 5 or not match:
            continue
        if len(match) == 1:
            return match[0], "legend hatch"
        if len(match) > 2:
            continue  # a hatch most finishes share says nothing about which one this is
        filled = [e for e in match if e.fill]
        if filled:
            best = min(filled, key=lambda e: sum((a - b) ** 2 for a, b in zip(e.fill, col)))
            others = ", ".join(e.code for e in match if e is not best)
            return best, f"legend hatch shared with {others}; chosen by nearest fill colour"
    return None, "colour not in legend"


def _code_shape(code: str) -> str:
    return re.sub(r"[A-Z]", "A", re.sub(r"\d", "9", code.upper()))


def _tagged_finish(dwg: Drawing, legend: Legend, col: Colour, g) -> LegendEntry | None:
    """A colour missing from the legend whose areas all carry the same finish
    tag, written like the legend's own codes (F1-H beside F1-A..F1-G). The
    architect added a finish and forgot the legend. Never used for walls or
    furniture, which carry no finish tag."""
    codes = [e.custom_code.upper() for e in legend.floors() if e.custom_code]
    # short shapes like "F5" also fit door and grid tags, so only longer codes count
    shapes = {_code_shape(c) for c in codes if len(c) >= 3}
    leads = {re.match(r"[A-Z]*", c).group() for c in codes} - {""}
    if not shapes:
        return None
    found = set()
    for t in dwg.texts:
        txt = t.text.strip().upper()
        if txt in codes or _code_shape(txt) not in shapes or re.match(r"[A-Z]*", txt).group() not in leads:
            continue
        if g.contains(Point(t.centre)):
            found.add(txt)
    if len(found) != 1:
        return None
    code = found.pop()
    return LegendEntry("floor", 0, "", f"{code} (written on the drawing, not in the legend)", col, custom_code=code)


def find_rooms(dwg: Drawing, legend: Legend, exclude: list[tuple] | None = None) -> tuple[list[Room], dict]:
    """Find finish rooms on the sheet. Returns rooms and a summary of what was set aside."""
    area = box(0, -dwg.height_m, dwg.width_m, 0)
    for x in ([legend.area] if legend.area else []) + list(exclude or []):
        area = area.difference(box(*x))
    vis = visible_fills(dwg, area)
    hatch = _Hatch(dwg, {c for e in legend.floors() for c in e.hatch[:1]} - {BLACK})
    report: dict = {"unknown_fills": {}, "orphan_strips_m2": 0.0}

    # 1. classify fill colours
    finishes: dict[Colour, tuple[LegendEntry | None, str]] = {}
    strips = []
    for col, g in vis.items():
        if col == WHITE or g.area < 1e-3:
            continue
        if _is_strip(g) or g.area < 1.0:
            strips.append(g)
            continue
        finishes[col] = _classify_colour(col, g, legend, hatch)

    # 2. hatch-only floor, inside the building outline
    hatch_rooms: list[Room] = []
    # Black hatch is not used: black is also walls, doors, stairs and text,
    # so finishes drawn only in black (matting on Cashel) are left to be drawn
    # by hand and listed in the report.
    hatch_only = [
        e for e in legend.floors() if e.hatch and (e.fill is None or e.fill == WHITE) and e.hatch[0] != BLACK
    ]
    report["manual_finishes"] = [
        e.code for e in legend.floors() if e.fill is None and (not e.hatch or e.hatch[0] == BLACK)
    ]
    empty = None
    if hatch_only:
        every = unary_union(list(vis.values()))
        # the outline only needs to be rough: simplify before the slow 1 m buffers
        rough = every.simplify(0.05, preserve_topology=False).buffer(0)
        sealed = rough.buffer(SEAL, join_style=2).buffer(-SEAL, join_style=2)
        outline = unary_union([Polygon(p.exterior) for p in _parts(sealed) if p.area > 50])
        empty = outline.difference(every)
    for p in _parts(empty):
        if p.area < 0.5:
            continue
        found = hatch.colours_in(p.buffer(-0.05))
        for e in hatch_only:
            n = found.get(e.hatch[0], 0)
            if n >= max(5, 1.0 * p.area):
                hatch_rooms.append(Room(Polygon(p.exterior), e, "hatch", None))
                break

    # 3. fill rooms (hatch-only rooms go in first so edge strips can join them too)
    rooms: list[Room] = list(hatch_rooms)
    for col, (entry, why) in finishes.items():
        g = vis[col].buffer(CLOSE, join_style=2).buffer(-CLOSE, join_style=2)
        if entry is None:
            entry = _tagged_finish(dwg, legend, col, g) if why == "colour not in legend" else None
            if entry is None:
                # not a legend finish: walls, furniture, other buildings. Reported, not measured.
                report["unknown_fills"][col] = round(g.area, 2)
                continue
            why = "tagged"
            report.setdefault("tagged_not_in_legend", {})[entry.code] = round(g.area, 2)
        for p in _parts(g):
            if p.area < MIN_ROOM:
                continue
            r = Room(p, entry, "fill", col)
            if why == "tagged":
                r.flag(f"finish {entry.code} is written in the room but is not in the legend; check the product")
            elif why != "legend fill":
                r.flag(f"finish matched by its hatch ({why}); the fill colour {col} differs from the legend")
            rooms.append(r)

    # 4. strips join the room they run along
    if rooms:
        tree = STRtree([r.geom for r in rooms])
        extra = defaultdict(list)
        for s in _parts(unary_union(strips)):
            if s.area < 1e-4:
                continue
            near = s.buffer(0.01)
            best = None
            for i in tree.query(near):
                L = rooms[i].geom.exterior.intersection(near).length
                if L > 0 and (best is None or L > best[0]):
                    best = (L, i)
            if best:
                extra[best[1]].append(s)
            else:
                report["orphan_strips_m2"] += s.area
        for i, ex in extra.items():
            g = unary_union([rooms[i].geom] + ex).buffer(0.005, join_style=2).buffer(-0.005, join_style=2)
            rooms[i].geom = max(_parts(g), key=lambda q: q.area)

    # 5. holes: furniture and symbols are floor; hatch-only rooms and large voids are not
    hatch_union = unary_union([h.geom for h in hatch_rooms]) if hatch_rooms else None
    for r in rooms:
        if r.source == "hatch":
            continue
        keep = []
        for h in r.geom.interiors:
            hp = Polygon(h)
            if hp.area >= MAX_FURNITURE_HOLE or (hatch_union is not None and hp.intersection(hatch_union).area > 0.5 * hp.area):
                keep.append(h)
        r.geom = Polygon(r.geom.exterior, keep)
        if keep:
            # Callidus rooms are written as outlines only for now, so the file
            # would count the opening (or the carpet room inside it) twice.
            cut = sum(Polygon(h).area for h in keep)
            r.flag(
                f"{len(keep)} opening(s) or other finish inside the room ({cut:.2f} m2): the area here leaves "
                "them out, but the Callidus room is the outer outline, so cut them out in Callidus"
            )

    # 6. skirting along each edge
    _skirting(rooms, vis, legend, report)

    # 7. room tags
    _attach_tags(dwg, rooms, legend)
    for r in rooms:
        if not r.codes:
            r.flag("no room tag inside")
        if any(STAIRS.search(n) for n in r.names):
            # plan area of a stair is not what is laid: flights go as treads and risers
            r.flag("stairs: plan area includes the flights; measure treads, risers and landings separately")
    return rooms, report


def _attach_tags(dwg: Drawing, rooms: list[Room], legend: Legend | None = None) -> None:
    """Room numbers (G01, PS-01-27, 02-PLNT-003) and the name printed next to them."""
    finish_codes = {e.code.split(" (")[0].upper() for e in (legend.entries if legend else [])}
    finish_codes |= {r.finish.code.split(" (")[0].upper() for r in rooms if r.finish}
    finish_codes |= {c.replace("-", "") for c in finish_codes}
    pattern = re.compile("|".join(re.escape(c) for c in sorted(finish_codes, key=len, reverse=True) if c))

    def only_finish_codes(text: str) -> bool:
        # "F2-C", or "F2-CF2-C" when two tags overlap
        return bool(finish_codes) and not re.sub(r"[\s,/]", "", pattern.sub("", text.upper()))

    codes = [t for t in dwg.texts if CODE.match(t.text) and not only_finish_codes(t.text)]
    tree = STRtree([r.geom for r in rooms])
    for c in codes:
        cx, cy = c.centre
        h = c.box[3] - c.box[1]
        near = [
            t for t in dwg.texts
            if t is not c and abs(t.centre[1] - cy) < 3.5 * h and t.box[0] < c.box[2] + h and t.box[2] > c.box[0] - h
            and not CODE.match(t.text) and not AREA_TEXT.search(t.text)
            and not only_finish_codes(t.text) and abs(t.box[3] - t.box[1] - h) < 0.35 * h
        ]
        near.sort(key=lambda t: t.box[1])
        name = " ".join(t.text for t in near)
        hits = [i for i in tree.query(Point(cx, cy)) if rooms[i].geom.contains(Point(cx, cy))]
        if not hits:
            near_rooms = tree.query(Point(cx, cy).buffer(0.3))
            hits = sorted(near_rooms, key=lambda i: rooms[i].geom.distance(Point(cx, cy)))[:1]
        for i in hits:
            rooms[i].codes.append(c.text)
            rooms[i].names.append(name)


def skirting_type_of(colour: Colour, legend: Legend) -> LegendEntry | None:
    """Skirting strips are drawn blended with the floor under them, so match by
    colour family rather than exact value: blue for the legend's blue skirting,
    warm brown for its brown one."""
    r, g, b = colour
    blue = b > r + 60 and b > g + 60
    brown = r >= g >= b and r - b >= 20 and r < 235
    for e in legend.skirtings():
        if e.fill is None:
            continue
        er, eg, eb = e.fill
        if blue and eb > er + 60 and eb > eg + 60:
            return e
        if brown and er >= eg >= eb and er - eb >= 20:
            return e
    return None


SAMPLE = 0.05  # metres between samples along an edge
INSET = 0.03  # metres inside the room where the strip is looked for
MIN_RUN = 0.15  # metres: shorter runs of skirting or gaps are merged into their neighbours


def _skirting(rooms: list[Room], vis: dict, legend: Legend, report: dict) -> None:
    from shapely.prepared import prep

    by_type: dict[str, list] = defaultdict(list)
    entries = {e.code: e for e in legend.skirtings()}
    mapped = {}
    for col, g in vis.items():
        if col == WHITE or not _is_strip(g):
            continue
        e = skirting_type_of(col, legend)
        if e is not None:
            by_type[e.code].append(g)
            mapped[col] = e.code
    report["skirting_colours"] = mapped
    preps = {code: prep(unary_union(gs)) for code, gs in by_type.items()}
    for r in rooms:
        ring = r.geom.exterior
        coords = list(ring.coords)[:-1]
        if ring.is_ccw is False:
            coords.reverse()  # interior on the left
        samples = []  # (x, y, type)
        n = len(coords)
        for i in range(n):
            (ax, ay), (bx, by) = coords[i], coords[(i + 1) % n]
            L = ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5
            if L == 0:
                continue
            nx, ny = -(by - ay) / L, (bx - ax) / L  # left normal = into the room
            k = max(1, int(L / SAMPLE))
            for j in range(k):
                t = (j + 0.5) / k
                x, y = ax + t * (bx - ax), ay + t * (by - ay)
                pt = Point(x + nx * INSET, y + ny * INSET)
                kind = next((c for c, pg in preps.items() if pg.contains(pt)), None)
                samples.append((i, t, kind, L / k))
        # per edge, split into runs of one type
        verts, types = [], []
        for i in range(n):
            (ax, ay), (bx, by) = coords[i], coords[(i + 1) % n]
            edge = [s for s in samples if s[0] == i]
            runs = []
            for _, t, kind, step in edge:
                if runs and runs[-1][0] == kind:
                    runs[-1][2] = t
                    runs[-1][3] += step
                else:
                    runs.append([kind, t, t, step])
            # merge short runs into the previous one
            merged = []
            for run in runs:
                if merged and (run[3] < MIN_RUN or merged[-1][0] == run[0]):
                    merged[-1][2] = run[2]
                    merged[-1][3] += run[3]
                else:
                    merged.append(run)
            if not merged:
                merged = [[None, 0, 1, 0]]
            for m_i, (kind, t0, _, _) in enumerate(merged):
                t = 0.0 if m_i == 0 else t0
                verts.append((ax + t * (bx - ax), ay + t * (by - ay)))
                types.append(entries.get(kind) if kind else None)
        r.vertices, r.edge_skirting = verts, types
