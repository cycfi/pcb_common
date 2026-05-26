#!/usr/bin/env python3
"""Collect unique LCSC/value/footprint combinations from board BOMs."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from kicad_jlc_common import (
    CatalogPart,
    find_board_files,
    footprint_package,
    parse_bom_csv,
    write_catalog,
    write_catalog_rows,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("boards", nargs="+", type=Path, help="Board directories")
    parser.add_argument("--out", type=Path, help="Write CSV catalog instead of printing")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    parts: dict[str, CatalogPart] = {}
    for board in args.boards:
        _, bom_path = find_board_files(board)
        for row in parse_bom_csv(bom_path):
            if not row.lcsc:
                continue
            source = board.name
            existing = parts.get(row.lcsc)
            if existing:
                source = ";".join(sorted(set(existing.source.split(";") + [board.name])))
            parts[row.lcsc] = CatalogPart(
                lcsc=row.lcsc,
                value=row.comment,
                footprint=row.footprint,
                package=footprint_package(row.footprint),
                source=source,
                notes="reviewed-from-current-bom",
            )

    if args.out:
        write_catalog(args.out, parts.values())
    else:
        write_catalog_rows(sys.stdout, parts.values())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
