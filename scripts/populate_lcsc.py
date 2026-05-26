#!/usr/bin/env python3
"""Suggest LCSC fields from a reviewed common-parts catalog.

This script does not edit schematics by default. It prints a proposal table so
part substitutions stay explicit.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

from kicad_jlc_common import find_board_files, footprint_package, normalized_value, parse_kicad_schematic, read_catalog


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("board", type=Path, help="Board directory containing *.kicad_sch")
    parser.add_argument(
        "--catalog",
        type=Path,
        default=Path(__file__).with_name("common_parts.csv"),
        help="Approved/common parts CSV",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    schematic_path, _ = find_board_files(args.board)
    parts = parse_kicad_schematic(schematic_path)
    catalog = read_catalog(args.catalog)

    by_value_package = defaultdict(list)
    for entry in catalog.values():
        by_value_package[(normalized_value(entry.value), entry.package)].append(entry)

    writer = csv.writer(sys.stdout)
    writer.writerow(("Reference", "Value", "Footprint", "Package", "Current LCSC", "Suggested LCSC", "Notes"))
    for part in sorted(parts.values(), key=lambda p: p.ref):
        if not part.in_bom or not part.on_board or part.dnp or not part.footprint:
            continue
        package = footprint_package(part.footprint)
        candidates = by_value_package.get((normalized_value(part.value), package), [])
        if part.lcsc:
            if candidates and part.lcsc not in {candidate.lcsc for candidate in candidates}:
                writer.writerow(
                    (
                        part.ref,
                        part.value,
                        part.footprint,
                        package,
                        part.lcsc,
                        "",
                        "current LCSC differs from catalog candidate",
                    )
                )
            continue
        if len(candidates) == 1:
            writer.writerow((part.ref, part.value, part.footprint, package, "", candidates[0].lcsc, "single catalog match"))
        elif len(candidates) > 1:
            lcscs = "|".join(candidate.lcsc for candidate in candidates)
            writer.writerow((part.ref, part.value, part.footprint, package, "", lcscs, "multiple catalog matches"))
        else:
            writer.writerow((part.ref, part.value, part.footprint, package, "", "", "no catalog match"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
