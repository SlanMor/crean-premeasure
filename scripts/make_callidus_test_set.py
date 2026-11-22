"""Build a set of Callidus test files from one real job.

Each file makes one small, known change to the original, so whoever opens
them in Callidus can tell us exactly what Callidus accepts:

  T0 control       unchanged copy
  T1 rename        one room renamed
  T2 finish        one room switched to another sheet product
  T3 move all      one room moved 1 m in x, with its zone and layout points
  T4 move outline  the same room's outline only moved 1 m in x
  T5 new room      a no-layout room replaced by a room our writer builds from
                   scratch (see build_t5)
  T6 whole job     every sheet room rewritten by our writer into a fresh file
                   with no layouts, optionally on a background made from the
                   PDF (see build_t6)

Usage:
  python scripts/make_callidus_test_set.py JOB.est OUT_DIR --prefix CASHEL \
      --rename-room G19 --move-room G25 --finish-room G25 \
      --finish "FF9 - Marmoleum Fresco Silver Shadow 3860"
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from premeasure_engine.callidus import read_est
from premeasure_engine.callidus.patch import EstPatcher


def build(
    est_path: Path,
    out: Path,
    prefix: str,
    rename_room: str,
    move_room: str,
    finish_room: str,
    finish: str,
    calback: Path | None = None,
) -> list[Path]:
    calback = calback or Path(str(est_path) + ".CalBack")
    if not calback.exists():
        raise SystemExit(f"background file not found: {calback}")
    out.mkdir(parents=True, exist_ok=True)

    def write(tag: str, patcher: EstPatcher) -> Path:
        target = out / f"{prefix}-{tag}.est"
        patcher.save(target)
        shutil.copyfile(calback, Path(str(target) + ".CalBack"))
        return target

    files = []
    files.append(write("T0-control", EstPatcher(est_path)))

    p = EstPatcher(est_path)
    p.rename_room(rename_room, f"{rename_room} - TEST RENAME")
    files.append(write("T1-rename", p))

    p = EstPatcher(est_path)
    p.set_room_material(finish_room, finish)
    files.append(write("T2-finish", p))

    p = EstPatcher(est_path)
    p.move_room(move_room, 1.0, 0.0, include_zone=True)
    files.append(write("T3-move-all", p))

    p = EstPatcher(est_path)
    p.move_room(move_room, 1.0, 0.0, include_zone=False)
    files.append(write("T4-move-outline", p))

    for f in files:
        est = read_est(f)
        print(f"{f.name}: {len(est.rooms)} rooms, read back OK")
    return files


# Coved skirting on every edge, in the form Callidus writes it: material
# number at byte 12, float32 0.2 at bytes 14 to 17.
def skirting_edge(material_index: int) -> bytes:
    edge = bytearray(18)
    edge[12] = material_index
    edge[14:18] = bytes.fromhex("cdcc4c3e")
    return bytes(edge)


def build_t5(
    est_path: Path,
    out: Path,
    prefix: str,
    replace: str,
    new_name: str,
    finish: str,
    skirting: str,
    rect: tuple[float, float, float, float],
    calback: Path | None = None,
) -> Path:
    """T5: replace a no-layout room with a rectangle built by our writer.

    rect is (x, y, width, height) in metres, sheet coordinates.
    """
    calback = calback or Path(str(est_path) + ".CalBack")
    x, y, w, h = rect
    vertices = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    p = EstPatcher(est_path)
    edge = skirting_edge(p.material_index(skirting))
    p.replace_room(
        replace,
        new_name=new_name,
        material_name=finish,
        vertices=vertices,
        edges=[edge] * 4,
        label=(x + w / 2, y + h + 1.5),
    )
    target = out / f"{prefix}-T5-new-room.est"
    out.mkdir(parents=True, exist_ok=True)
    p.save(target)
    shutil.copyfile(calback, Path(str(target) + ".CalBack"))
    print(f"{target.name}: {len(read_est(target).rooms)} rooms, read back OK")
    return target


def build_t6(
    est_path: Path,
    out: Path,
    prefix: str,
    skip_materials: set[str],
    pdf: Path | None = None,
    calback: Path | None = None,
    scale: float = 100.0,
) -> list[Path]:
    """T6: a whole job written by our writer.

    Keeps the job's header and finishes, writes every room (except rooms in
    skip_materials) with no layout, and empties the stock-roll lists. Writes
    T6a with the job's own background and, if pdf is given, T6b with a
    background rendered from the PDF.
    """
    from premeasure_engine.callidus.calback_writer import (
        encode_tiff,
        render_pdf_page,
        sheet_extents,
        write_calback,
    )
    from premeasure_engine.callidus.job_writer import ROOM_CLASS_DECL, build_job, check_rooms, load_template
    from premeasure_engine.callidus.room_writer import RoomRecord, parse_room_head

    calback = calback or Path(str(est_path) + ".CalBack")
    data = est_path.read_bytes()
    est = read_est(est_path)
    template = load_template(est_path)
    rooms: list[RoomRecord] = []
    for room in sorted(est.rooms, key=lambda r: r.offset):
        if room.material is None or room.material.name in skip_materials:
            continue
        decl = room.offset - 4 - len(ROOM_CLASS_DECL)
        start = decl if data[decl : room.offset - 4] == ROOM_CLASS_DECL else room.offset - 6
        head = parse_room_head(data, start)
        rooms.append(
            RoomRecord(
                room_class=0, zone_class=0, room_index=0,
                material=head.material, name=head.name, callidus_id=head.callidus_id,
                vertices=head.vertices, edges=head.edges, zone_edges=head.edges,
                origin=head.origin, angle=head.angle, angle2=head.angle2,
                label=head.label, ref_edge=head.ref_edge,
                ref_point=head.ref_point or head.vertices[0],
                flags=head.flags or (0, 1),
            )
        )
    check_rooms(rooms, {m.index for m in est.materials})
    out.mkdir(parents=True, exist_ok=True)
    job = build_job(template, rooms)
    files = []
    a = out / f"{prefix}-T6a-whole-job.est"
    a.write_bytes(job)
    shutil.copyfile(calback, Path(str(a) + ".CalBack"))
    files.append(a)
    if pdf:
        from premeasure_engine.callidus import read_calback

        b = out / f"{prefix}-T6b-whole-job-pdf-background.est"
        b.write_bytes(job)
        img = render_pdf_page(pdf, 0, 300)
        tpl = read_calback(calback).sheets[0]
        ext = sheet_extents(img.width, img.height, 300, scale)
        if abs(ext[2] - tpl.x_right) < 1e-3 and abs(ext[1] - tpl.y_top) < 1e-3:
            ext = (tpl.x_left, tpl.y_top, tpl.x_right, tpl.y_bottom)
        write_calback(calback, Path(str(b) + ".CalBack"), encode_tiff(img), ext)
        files.append(b)
    for f in files:
        print(f"{f.name}: {len(read_est(f).rooms)} rooms, read back OK")
    return files


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("est", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--prefix", required=True)
    ap.add_argument("--rename-room", required=True)
    ap.add_argument("--move-room", required=True)
    ap.add_argument("--finish-room", required=True)
    ap.add_argument("--finish", required=True)
    ap.add_argument("--calback", type=Path, help="background file, if not named <est>.CalBack")
    a = ap.parse_args()
    build(a.est, a.out, a.prefix, a.rename_room, a.move_room, a.finish_room, a.finish, a.calback)


if __name__ == "__main__":
    main()
