#!/usr/bin/env python3
"""Shared helpers for KiCad/JLCPCB BOM checks.

The code intentionally uses only the Python standard library so it can run on a
fresh workstation before fab release.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, TextIO


@dataclass(frozen=True)
class Part:
    ref: str
    value: str
    footprint: str
    lcsc: str
    in_bom: bool
    on_board: bool
    dnp: bool


@dataclass(frozen=True)
class BomRow:
    comment: str
    refs: tuple[str, ...]
    footprint: str
    lcsc: str


@dataclass(frozen=True)
class CatalogPart:
    lcsc: str
    value: str
    footprint: str
    package: str
    source: str
    notes: str


def iter_sexpr_blocks(text: str, head: str) -> Iterable[str]:
    needle = f"({head}"
    i = 0
    while True:
        i = text.find(needle, i)
        if i < 0:
            return
        j = i
        depth = 0
        in_string = False
        escaped = False
        while j < len(text):
            char = text[j]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
            elif char == '"':
                in_string = True
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    j += 1
                    break
            j += 1
        yield text[i:j]
        i = j


def property_value(block: str, name: str) -> str:
    escaped = re.escape(name)
    match = re.search(rf'\(property\s+"{escaped}"\s+"([^"]*)"', block)
    return match.group(1) if match else ""


def parse_kicad_schematic(path: Path) -> dict[str, Part]:
    text = path.read_text(encoding="utf-8")
    parts: dict[str, Part] = {}
    for block in iter_sexpr_blocks(text, "symbol"):
        ref = property_value(block, "Reference")
        if not ref or ref.startswith("#"):
            continue
        # Library symbol definitions are embedded in KiCad schematics and use
        # generic references such as "U" or "D". Actual placed symbols have a
        # numbered reference designator.
        if not re.match(r"^[A-Za-z]+[0-9]+[A-Za-z]?$", ref):
            continue
        parts[ref] = Part(
            ref=ref,
            value=property_value(block, "Value"),
            footprint=property_value(block, "Footprint"),
            lcsc=property_value(block, "LCSC"),
            in_bom="(in_bom yes)" in block,
            on_board="(on_board yes)" in block,
            dnp="(dnp yes)" in block,
        )
    return parts


def split_designators(text: str) -> tuple[str, ...]:
    return tuple(ref.strip() for ref in text.split(",") if ref.strip())


def parse_bom_csv(path: Path) -> list[BomRow]:
    rows: list[BomRow] = []
    with path.open(newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        for row in reader:
            rows.append(
                BomRow(
                    comment=(row.get("Comment") or "").strip(),
                    refs=split_designators(row.get("Designator") or ""),
                    footprint=(row.get("Footprint") or "").strip(),
                    lcsc=(row.get("LCSC") or "").strip(),
                )
            )
    return rows


def read_catalog(path: Path) -> dict[str, CatalogPart]:
    catalog: dict[str, CatalogPart] = {}
    if not path.exists():
        return catalog
    with path.open(newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        for row in reader:
            lcsc = (row.get("lcsc") or "").strip()
            if not lcsc:
                continue
            catalog[lcsc] = CatalogPart(
                lcsc=lcsc,
                value=(row.get("value") or "").strip(),
                footprint=(row.get("footprint") or "").strip(),
                package=(row.get("package") or "").strip(),
                source=(row.get("source") or "").strip(),
                notes=(row.get("notes") or "").strip(),
            )
    return catalog


def write_catalog_rows(file: TextIO, parts: Iterable[CatalogPart]) -> None:
    writer = csv.DictWriter(
        file,
        fieldnames=("lcsc", "value", "footprint", "package", "source", "notes"),
    )
    writer.writeheader()
    for part in sorted(parts, key=lambda p: (normalized_value(p.value), p.package, p.lcsc)):
        writer.writerow(part.__dict__)


def write_catalog(path: Path, parts: Iterable[CatalogPart]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as file:
        write_catalog_rows(file, parts)


def normalized_value(value: str) -> str:
    value = value.strip().lower()
    value = value.replace("ω", "ohm")
    value = value.replace("Ω", "ohm")
    value = value.replace(" ", "")
    return value


def footprint_package(footprint: str) -> str:
    fp = footprint.split(":")[-1]

    metric = re.search(r"_(0201|0402|0603|0805|1206)_\d+Metric", fp)
    if metric:
        return metric.group(1)

    led = re.search(r"LED_(0603|0805|1206)_", fp)
    if led:
        return led.group(1)

    direct_patterns = [
        "SOT-23-6",
        "SOT-23-5",
        "SOT-23-3",
        "SOT-23",
        "SOT-223",
        "SOT-89-3",
        "SOD-123",
        "SOD-523",
        "TO-252-2",
        "TSSOP-20",
        "TSSOP-14",
        "VSSOP-8",
        "LQFP-144",
        "DFN",
        "QFN",
        "USB-C",
    ]
    upper = fp.upper()
    for pattern in direct_patterns:
        if pattern.upper() in upper:
            return pattern

    if "PinHeader" in fp or "pin_header" in fp:
        return "PinHeader"
    if "Crystal_SMD_3225" in fp:
        return "SMD-3225"
    if "CP_Elec_6.3x5.7" in fp:
        return "CAP-SMD-6.3x5.7"
    if "CP_Elec_10x10" in fp:
        return "CAP-SMD-10x10"
    return ""


def compatible_package(expected: str, actual: str) -> bool:
    if not expected or not actual:
        return True
    a = expected.upper().replace("_", "-").replace(" ", "")
    b = actual.upper().replace("_", "-").replace(" ", "")
    if a == b:
        return True
    if a in b:
        return True
    aliases = {
        "0201": {"0201", "0603METRIC"},
        "0402": {"0402", "1005METRIC"},
        "0603": {"0603", "1608METRIC"},
        "0805": {"0805", "2012METRIC"},
        "SMD-3225": {"3225", "SMD-3225", "SMD3225", "SMD3225-4P"},
    }
    return b in aliases.get(a, set()) or a in aliases.get(b, set())


def find_board_files(board_dir: Path) -> tuple[Path, Path]:
    schematics = sorted(board_dir.glob("*.kicad_sch"))
    if not schematics:
        raise FileNotFoundError(f"No .kicad_sch found in {board_dir}")
    bom = board_dir / "fab" / "bom.csv"
    if not bom.exists():
        raise FileNotFoundError(f"No generated BOM found at {bom}")
    return schematics[0], bom
