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
