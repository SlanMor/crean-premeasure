"""Command line: premeasure est | calback | reconcile."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from .callidus import read_calback, read_est
from .quantities import quantities
from .reconcile import read_measure_sheet, reconcile


def _cmd_est(args) -> int:
    for path in args.files:
        est = read_est(path)
        rows = quantities(est)
        if args.json:
            print(json.dumps([asdict(r) for r in rows], indent=2))
            continue
        print(f"{path}: '{est.title}', {len(est.materials)} materials, {len(est.rooms)} rooms")
        for r in rows:
            extras = ", ".join(f"{k} {v:.3f} lm" for k, v in r.edge_products_lm.items())
            print(f"  {r.net_m2:9.3f} m2  {r.room[:40]:40s}  {r.material[:45]:45s}  {extras}")
        total = sum(r.net_m2 for r in rows if r.material != "NONE")
        print(f"  total excluding NONE: {total:.3f} m2")
    return 0


def _cmd_calback(args) -> int:
    for path in args.files:
        cb = read_calback(path)
        print(f"{path}: {len(cb.sheets)} sheet(s)")
        for i, s in enumerate(cb.sheets, start=1):
            print(
                f"  sheet {i}: x {s.x_left:.3f} to {s.x_right:.3f} m, y {s.y_top:.3f} to {s.y_bottom:.3f} m, "
                f"image {s.tiff_length:,} bytes"
            )
    return 0


def _cmd_reconcile(args) -> int:
    ests = [read_est(p) for p in args.est]
    rows = read_measure_sheet(args.xlsx)
    r = reconcile(ests, rows)
    print(f"area rows matched:   {r.area_matched} of {r.area_rows}")
    print(f"linear rows matched: {r.linear_matched} of {r.linear_rows}")
    for row in r.unmatched_area_rows:
        print(f"  area row {row.row} not found: {row.title} | {row.material} | {row.room} | {row.net_m2}")
    for row in r.unmatched_linear_rows:
        print(f"  linear row {row.row} not matched: {row.title} | {row.material} | {row.room} | {row.base_lm}")
    for title, room, material, net in r.rooms_not_in_sheet:
        print(f"  in Callidus, not in sheet: {title} | {material} | {room or '(unnamed)'} | {net}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="premeasure")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("est", help="list rooms and quantities in .est files")
    p.add_argument("files", nargs="+")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_est)

    p = sub.add_parser("calback", help="list the sheets in .CalBack files")
    p.add_argument("files", nargs="+")
    p.set_defaults(func=_cmd_calback)

    p = sub.add_parser("reconcile", help="check .est files against a measure spreadsheet")
    p.add_argument("--xlsx", required=True)
    p.add_argument("est", nargs="+")
    p.set_defaults(func=_cmd_reconcile)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
