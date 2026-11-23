"""Two revisions of the same finishes drawing: what changed.

Rooms are paired by where they are. If the architect moved the plan on the
sheet between revisions, the shift is found first from rooms that kept their
finish and size, so every room is not reported as changed.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from shapely import affinity
from shapely.ops import unary_union

from .premeasure import Analysis, _page_image, rooms_in_order

SAME_AREA = 0.01  # within 1%: the room has not changed
MATCH_IOU = 0.5
STEP = 0.05  # metres: shifts are compared to this
LIKELY_SAME_DRAWING = 0.5  # share of the new floor in rooms kept as they were, for two drawings to be revisions


@dataclass
class RoomDiff:
    new_id: int | None  # the room's number in the new revision's summary
    old_id: int | None
    label: str
    finish_old: str | None
    finish_new: str | None
    area_old: float | None
    area_new: float | None

    @property
    def state(self) -> str:
        if self.old_id is None:
            return "added"
        if self.new_id is None:
            return "removed"
        if self.finish_old != self.finish_new:
            return "finish changed"
        if not self.area_old or abs(self.area_new / self.area_old - 1) > SAME_AREA:
            return "outline changed"
        return "unchanged"


@dataclass
class RevisionDiff:
    rooms: list[RoomDiff] = field(default_factory=list)
    shift: tuple[float, float] = (0.0, 0.0)
    agreement: float = 0.0  # share of the floor in both revisions, after the shift
    scale_changed: bool = False

    @property
    def kept(self) -> float:
        """Share of the new revision's floor in rooms that kept their place and finish."""
        total = sum(r.area_new or 0 for r in self.rooms)
        same = sum(r.area_new for r in self.rooms if r.state in ("unchanged", "outline changed"))
        return same / total if total else 0.0

    @property
    def likely_same_drawing(self) -> bool:
        """Revisions keep most rooms as they were. Two different floors of one
        building can share a footprint, but not their rooms and finishes."""
        return self.kept >= LIKELY_SAME_DRAWING and not self.scale_changed

    def counts(self) -> dict[str, int]:
        return dict(Counter(r.state for r in self.rooms))

    def by_finish(self) -> list[dict]:
        totals: dict[str, list[float]] = {}
        for r in self.rooms:
            if r.finish_old:
                totals.setdefault(r.finish_old, [0.0, 0.0])[0] += r.area_old or 0
            if r.finish_new:
                totals.setdefault(r.finish_new, [0.0, 0.0])[1] += r.area_new or 0
        return [{"finish": k, "old": round(v[0], 2), "new": round(v[1], 2), "change": round(v[1] - v[0], 2)}
                for k, v in sorted(totals.items())]

    def as_dict(self) -> dict:
        return {
            "shift": [round(self.shift[0], 3), round(self.shift[1], 3)],
            "agreement": round(self.agreement, 4),
            "kept": round(self.kept, 4),
            "scale_changed": self.scale_changed,
            "likely_same_drawing": self.likely_same_drawing,
            "counts": self.counts(),
            "by_finish": self.by_finish(),
            "rooms": [{"new_id": r.new_id, "old_id": r.old_id, "label": r.label, "finish_old": r.finish_old,
                       "finish_new": r.finish_new, "area_old": r.area_old, "area_new": r.area_new,
                       "state": r.state} for r in self.rooms],
        }


def _code(room) -> str | None:
    return room.finish.code if room.finish else None


def find_shift(old_rooms, new_rooms) -> tuple[float, float]:
    """How far the plan moved on the sheet: the offset most rooms of the same
    finish and size agree on. (0, 0) when nothing agrees."""
    votes: Counter = Counter()
    for a in old_rooms:
        for b in new_rooms:
            if _code(a) != _code(b) or a.area < 2 or abs(b.area / a.area - 1) > 0.02:
                continue
            ca, cb = a.geom.centroid, b.geom.centroid
            votes[(round((cb.x - ca.x) / STEP), round((cb.y - ca.y) / STEP))] += 1
    if not votes:
        return 0.0, 0.0
    (dx, dy), n = votes.most_common(1)[0]
    if n < 3 or votes.get((0, 0), 0) >= n:
        return 0.0, 0.0
    return dx * STEP, dy * STEP


def compare_revisions(old: Analysis, new: Analysis) -> RevisionDiff:
    """Room by room, the new revision against the old one."""
    old_rooms, new_rooms = rooms_in_order(old), rooms_in_order(new)
    out = RevisionDiff(scale_changed=abs(old.scale - new.scale) > 1e-6)
    dx, dy = find_shift(old_rooms, new_rooms)
    out.shift = (dx, dy)
    moved = [affinity.translate(r.geom, dx, dy) for r in old_rooms]
    floor_old = unary_union(moved) if moved else None
    floor_new = unary_union([r.geom for r in new_rooms]) if new_rooms else None
    if floor_old is not None and floor_new is not None:
        both = floor_old.intersection(floor_new).area
        out.agreement = both / max(floor_old.area, floor_new.area)

    pairs = []
    for i, ga in enumerate(moved):
        for j, b in enumerate(new_rooms):
            if not ga.intersects(b.geom):
                continue
            inter = ga.intersection(b.geom).area
            if inter > 0:
                iou = inter / ga.union(b.geom).area
                if iou >= MATCH_IOU:
                    pairs.append((iou, i, j))
    used_old, used_new = set(), set()
    for _, i, j in sorted(pairs, reverse=True):
        if i in used_old or j in used_new:
            continue
        used_old.add(i)
        used_new.add(j)
        a, b = old_rooms[i], new_rooms[j]
        out.rooms.append(RoomDiff(j, i, b.label or a.label, _code(a), _code(b), round(a.area, 2), round(b.area, 2)))
    for j, b in enumerate(new_rooms):
        if j not in used_new:
            out.rooms.append(RoomDiff(j, None, b.label, None, _code(b), None, round(b.area, 2)))
    for i, a in enumerate(old_rooms):
        if i not in used_old:
            out.rooms.append(RoomDiff(None, i, a.label, _code(a), None, round(a.area, 2), None))
    order = {"added": 0, "removed": 1, "finish changed": 2, "outline changed": 3, "unchanged": 4}
    out.rooms.sort(key=lambda r: (order[r.state], -max(r.area_new or 0, r.area_old or 0)))
    return out


def render_changes(old: Analysis, new: Analysis, diff: RevisionDiff, out_png: str | Path, dpi: int = 60) -> Path:
    """The new revision with what changed drawn on it: added in green, removed
    (where it was) in red, finish or outline changed in orange."""
    from PIL import Image, ImageDraw

    img = _page_image(new, dpi).convert("L").convert("RGB")
    img = Image.blend(img, Image.new("RGB", img.size, "white"), 0.5)
    k = img.width / new.width_m
    over = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(over)
    old_rooms, new_rooms = rooms_in_order(old), rooms_in_order(new)
    colours = {"added": (22, 140, 70), "removed": (200, 30, 30), "finish changed": (230, 140, 0),
               "outline changed": (230, 140, 0)}

    def draw(geom, colour, dashed=False):
        for poly in [geom] if geom.geom_type == "Polygon" else list(getattr(geom, "geoms", [])):
            if poly.geom_type != "Polygon":
                continue
            pts = [(x * k, img.height + y * k) for x, y in poly.exterior.coords]
            d.polygon(pts, fill=colour + ((25,) if dashed else (80,)), outline=colour + (255,), width=3)

    for r in diff.rooms:
        colour = colours.get(r.state)
        if colour is None:
            continue
        if r.state == "removed":
            draw(affinity.translate(old_rooms[r.old_id].geom, *diff.shift), colour, dashed=True)
        else:
            draw(new_rooms[r.new_id].geom, colour)
    img = Image.alpha_composite(img.convert("RGBA"), over).convert("RGB")
    out_png = Path(out_png)
    img.save(out_png, optimize=True)
    return out_png
