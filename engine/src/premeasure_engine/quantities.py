"""Quantity rules, matching how Callidus reports a job.

Rules verified against Crean's own spreadsheets on three jobs:

* Net area = room area minus any room that sits inside it
  (for example OT2 at Limerick: 60.42 - 10.21 = 50.21 m2).
* Skirting comes in two forms, depending on how the estimator set the job up:
  - edge products: each edge names its product (coved skirting, matwell
    frame) by material index. Cashel and Shellybanks reconcile exactly.
  - room-level skirting: every edge with no flags counts.
    Limerick reconciles exactly on 178 of 211 rooms.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from shapely.geometry import Polygon
from shapely.validation import make_valid

from .callidus.est_reader import EstFile, Room

CONTAINED_SHARE = 0.9  # a room counts as inside another if this share of it overlaps


def _polygon(room: Room):
    return make_valid(Polygon(room.vertices))


def nested_rooms(room: Room, rooms: list[Room]) -> list[Room]:
    outer = _polygon(room)
    inner = []
    for other in rooms:
        if other is room or other.area >= room.area:
            continue
        if outer.intersection(_polygon(other)).area > CONTAINED_SHARE * other.area:
            inner.append(other)
    return inner


def net_area(room: Room, rooms: list[Room]) -> float:
    return room.area - sum(r.area for r in nested_rooms(room, rooms))


def edge_products(room: Room, est: EstFile) -> dict[str, float]:
    """Length per edge product (for example '100mm high coved skirting'), in metres."""
    totals: dict[str, float] = defaultdict(float)
    for edge in room.edges:
        idx = edge.edge_product_index
        if idx:
            material = est.material_by_index(idx)
            totals[material.name if material else f"material #{idx}"] += edge.length
    return dict(totals)


def room_level_skirting(room: Room) -> float:
    return sum(e.length for e in room.edges if e.is_plain)


@dataclass
class RoomQuantity:
    floor: str
    room: str
    material: str
    net_m2: float
    edge_products_lm: dict[str, float]
    room_level_skirting_lm: float


def quantities(est: EstFile) -> list[RoomQuantity]:
    out = []
    for room in est.rooms:
        out.append(
            RoomQuantity(
                floor=est.title,
                room=room.name,
                material=room.material.name if room.material else f"material #{room.material_index}",
                net_m2=net_area(room, est.rooms),
                edge_products_lm=edge_products(room, est),
                room_level_skirting_lm=room_level_skirting(room),
            )
        )
    return out
