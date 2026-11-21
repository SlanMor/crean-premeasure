from premeasure_engine.callidus.est_reader import Edge, EstFile, Material, Room
from premeasure_engine.quantities import edge_products, net_area, room_level_skirting

PLAIN = bytes(18)
OPEN = bytes([1]) + bytes(17)


def _skirting_edge(index: int) -> bytes:
    raw = bytearray(18)
    raw[12] = index
    return bytes(raw)


def _room(name, pts, raws=None, material_index=2):
    raws = raws or [PLAIN] * len(pts)
    edges = [Edge(pts[i], pts[(i + 1) % len(pts)], raws[i]) for i in range(len(pts))]
    return Room(name, "id", material_index, None, pts, edges, 0)


def test_area_and_perimeter():
    r = _room("A", [(0, 0), (4, 0), (4, -3), (0, -3)])
    assert abs(r.area - 12) < 1e-9
    assert abs(r.perimeter - 14) < 1e-9


def test_net_area_subtracts_nested_room():
    outer = _room("OT2", [(0, 0), (10, 0), (10, -6), (0, -6)])
    inner = _room("OT2 light", [(2, -2), (4, -2), (4, -4), (2, -4)])
    beside = _room("Corridor", [(10, 0), (12, 0), (12, -6), (10, -6)])
    rooms = [outer, inner, beside]
    assert abs(net_area(outer, rooms) - 56) < 1e-9
    assert abs(net_area(inner, rooms) - 4) < 1e-9
    assert abs(net_area(beside, rooms) - 12) < 1e-9


def test_room_level_skirting_skips_flagged_edges():
    r = _room("WC", [(0, 0), (2, 0), (2, -2), (0, -2)], [PLAIN, OPEN, PLAIN, PLAIN])
    assert abs(room_level_skirting(r) - 6) < 1e-9


def test_edge_products_by_material_index():
    skirting = Material(17, "125mm coved skirting", "x", None, None)
    est = EstFile(path=None, title="Ground Floor", materials=[skirting])
    r = _room(
        "WC",
        [(0, 0), (2, 0), (2, -3), (0, -3)],
        [_skirting_edge(17), PLAIN, _skirting_edge(17), OPEN],
    )
    assert edge_products(r, est) == {"125mm coved skirting": 4.0}
