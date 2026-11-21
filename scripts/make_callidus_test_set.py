"""Build a set of Callidus test files from one real job.

Each file makes one small, known change to the original, so whoever opens
them in Callidus can tell us exactly what Callidus accepts:

  T0 control       unchanged copy
  T1 rename        one room renamed
  T2 finish        one room switched to another sheet product
  T3 move all      one room moved 1 m in x, with its zone and layout points
  T4 move outline  the same room's outline only moved 1 m in x

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
