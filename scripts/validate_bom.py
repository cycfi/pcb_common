#!/usr/bin/env python3
"""Validate a KiCad/JLCPCB BOM against schematic fields and a parts catalog."""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import csv
import html
import hmac
import json
import os
import re
import secrets
import string
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from kicad_jlc_common import (
    compatible_package,
    find_board_files,
    footprint_package,
    normalized_value,
    parse_bom_csv,
    parse_kicad_schematic,
)


@dataclass(frozen=True)
class LivePart:
    lcsc: str
    manufacturer: str
    mfr_part: str
    package: str
    description: str
    source_url: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("board", type=Path, help="Board directory containing *.kicad_sch and fab/bom.csv")
    parser.add_argument(
        "--strict-catalog",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--part-source",
        choices=("jlcpcb-api", "jlcpcb-web", "jlcsearch"),
        default="jlcpcb-api",
        help="Live source used for authoritative LCSC/JLCPCB package data",
    )
    parser.add_argument(
        "--http-timeout",
        type=float,
        default=8.0,
        help="Timeout in seconds for each live part lookup",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=8,
        help="Maximum number of concurrent live part lookups",
    )
    parser.add_argument(
        "--allow-missing-footprint",
        action="append",
        default=[r"PinHeader", r"pin_header", r"mounting_hole", r"TestPoint"],
        help="Regex for footprints allowed to have blank LCSC fields",
    )
    parser.add_argument(
        "--ignore-missing-bom-footprint",
        action="append",
        default=[r"mounting_hole", r"TestPoint"],
        help="Regex for schematic footprints allowed to be absent from the generated BOM",
    )
    return parser.parse_args()


def matches_any(footprint: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, footprint, re.IGNORECASE) for pattern in patterns)


def designator_key(ref: str) -> tuple[str, int, str]:
    match = re.match(r"^([A-Za-z]+)([0-9]+)(.*)$", ref)
    if not match:
        return (ref, -1, "")
    return (match.group(1), int(match.group(2)), match.group(3))


def format_refs(refs: Iterable[str]) -> str:
    return ",".join(sorted(refs, key=designator_key))


def require_csv_columns(path: Path, required: set[str]) -> list[str]:
    if not path.exists():
        return [f"{path}: file does not exist"]
    with path.open(newline="", encoding="utf-8-sig") as file:
        reader = csv.reader(file)
        header = next(reader, [])
    missing = sorted(required - set(header))
    return [f"{path}: missing required CSV column(s): {', '.join(missing)}"] if missing else []


def make_nonce() -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(32))


def jlcpcb_auth_header(method: str, path: str, body: str, app_id: str, access_key: str, secret_key: str) -> str:
    nonce = make_nonce()
    timestamp = int(time.time())
    string_to_sign = f"{method.upper()}\n{path}\n{timestamp}\n{nonce}\n{body}\n"
    signature = hmac.new(secret_key.encode("utf-8"), string_to_sign.encode("utf-8"), "sha256").digest()
    signature_b64 = base64.b64encode(signature).decode("utf-8")
    return (
        f'JOP appid="{app_id}", accesskey="{access_key}", '
        f'nonce="{nonce}", timestamp="{timestamp}", signature="{signature_b64}"'
    )


def normalize_json_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.lower())


def find_json_value(data: object, candidate_keys: set[str]) -> str:
    if isinstance(data, dict):
        for key, value in data.items():
            if normalize_json_key(str(key)) in candidate_keys and value not in (None, ""):
                return str(value).strip()
        for value in data.values():
            found = find_json_value(value, candidate_keys)
            if found:
                return found
    elif isinstance(data, list):
        for value in data:
            found = find_json_value(value, candidate_keys)
            if found:
                return found
    return ""


def text_lines_from_html(body: str) -> list[str]:
    text = re.sub(r"(?is)<(script|style).*?</\1>", "\n", body)
    text = re.sub(r"(?s)<[^>]+>", "\n", text)
    text = html.unescape(text)
    return [line.strip() for line in text.splitlines() if line.strip()]


def field_after(lines: list[str], label: str) -> str:
    for index, line in enumerate(lines):
        if line == label:
            for value in lines[index + 1 :]:
                if value:
                    return value
    return ""


def parse_jlcpcb_partdetail_page(lcsc: str, source_url: str, body: str) -> LivePart:
    lines = text_lines_from_html(body)
    page_lcsc = field_after(lines, "JLCPCB Part #")
    if page_lcsc and page_lcsc != lcsc:
        raise ValueError(f"{lcsc}: JLCPCB page returned part {page_lcsc}")

    package = field_after(lines, "Package")
    if not package:
        raise ValueError(f"{lcsc}: JLCPCB page did not contain a Package field")

    return LivePart(
        lcsc=lcsc,
        manufacturer=field_after(lines, "Manufacturer"),
        mfr_part=field_after(lines, "MFR.Part #"),
        package=package,
        description=field_after(lines, "Description"),
        source_url=source_url,
    )


def fetch_jlcpcb_web_part(lcsc: str, timeout: float) -> LivePart:
    source_url = f"https://jlcpcb.com/partdetail/{urllib.parse.quote(lcsc)}"
    request = urllib.request.Request(
        source_url,
        headers={
            "User-Agent": "Mozilla/5.0 pcb-common-bom-validator/1.0",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8", errors="replace")
    return parse_jlcpcb_partdetail_page(lcsc, source_url, body)


def parse_jlcpcb_component(lcsc: str, source_url: str, component: object) -> LivePart:
    if not isinstance(component, dict):
        raise ValueError(f"{lcsc}: JLCPCB component entry is not a JSON object")

    package = find_json_value(
        component,
        {"componentspecificationen", "componentspecification", "package", "packagetype", "productpackage"},
    )
    if not package:
        keys = ", ".join(sorted(str(key) for key in component.keys()))
        raise ValueError(f"{lcsc}: JLCPCB component entry did not contain a recognized package field; keys: {keys}")

    return LivePart(
        lcsc=lcsc,
        manufacturer=find_json_value(component, {"componentbranden", "componentbrand", "manufacturer", "brandname"}),
        mfr_part=find_json_value(component, {"componentmodelen", "componentmodel", "mfrpartnumber", "mpn"}),
        package=package,
        description=find_json_value(component, {"componentdescriptionen", "description", "componentname"}),
        source_url=source_url,
    )


def parse_jlcpcb_components_page(payload: object) -> tuple[list[dict], str]:
    if not isinstance(payload, dict):
        raise ValueError("JLCPCB API response is not a JSON object")

    code = payload.get("code")
    if code not in (200, "200"):
        raise ValueError(f"JLCPCB API returned code {code}: {payload.get('msg') or payload.get('message') or payload}")

    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError("JLCPCB API response has no data object")

    components = data.get("componentInfos")
    if not isinstance(components, list):
        raise ValueError("JLCPCB API response data has no componentInfos list")
    return components, str(data.get("lastKey") or "")


def fetch_jlcpcb_components_page(last_key: str, timeout: float) -> tuple[list[dict], str]:
    app_id = os.environ.get("JLCPCB_APP_ID")
    access_key = os.environ.get("JLCPCB_API_KEY")
    secret_key = os.environ.get("JLCPCB_API_SECRET")
    if not app_id or not access_key or not secret_key:
        raise ValueError("JLCPCB_APP_ID, JLCPCB_API_KEY, and JLCPCB_API_SECRET must be exported")

    path = "/overseas/openapi/component/getComponentInfos"
    payload = {"lastKey": last_key} if last_key else {}
    body = json.dumps(payload, separators=(",", ":"))
    source_url = f"https://open.jlcpcb.com{path}"
    request = urllib.request.Request(
        source_url,
        data=body.encode("utf-8"),
        headers={
            "User-Agent": "pcb-common-bom-validator/1.0",
            "Accept": "application/json",
            "Authorization": jlcpcb_auth_header("POST", path, body, app_id, access_key, secret_key),
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")
        try:
            body_json = json.loads(body_text)
            message = body_json.get("message") or body_json.get("msg") or body_json
        except json.JSONDecodeError:
            message = body_text[:500] or exc.reason
        raise ValueError(f"JLCPCB HTTP {exc.code}: {message}") from exc
    return parse_jlcpcb_components_page(payload)


def fetch_live_part(lcsc: str, source: str, timeout: float) -> LivePart:
    if source == "jlcpcb-api":
        # JLCPCB's public examples expose a paginated component-info endpoint,
        # not a dedicated single-component endpoint. Batch lookup is handled by
        # fetch_live_parts().
        raise ValueError(f"{source} single-part lookup is not supported")
    if source == "jlcpcb-web":
        return fetch_jlcpcb_web_part(lcsc, timeout)
    if source == "jlcsearch":
        return fetch_jlcsearch_part(lcsc, timeout)
    raise ValueError(f"Unsupported part source: {source}")


def parse_jlcsearch_result(lcsc: str, source_url: str, payload: object) -> LivePart:
    if not isinstance(payload, dict):
        raise ValueError(f"{lcsc}: JLCSearch response is not a JSON object")
    components = payload.get("components")
    if not isinstance(components, list):
        raise ValueError(f"{lcsc}: JLCSearch response has no components list")

    numeric_lcsc = int(lcsc.removeprefix("C"))
    matches = [component for component in components if component.get("lcsc") == numeric_lcsc]
    if not matches:
        raise ValueError(f"{lcsc}: not found in JLCSearch exact search response")

    component = matches[0]
    package = str(component.get("package") or "").strip()
    if not package:
        raise ValueError(f"{lcsc}: JLCSearch result has blank package")

    return LivePart(
        lcsc=lcsc,
        manufacturer="",
        mfr_part=str(component.get("mfr") or "").strip(),
        package=package,
        description=str(component.get("description") or "").strip(),
        source_url=source_url,
    )


def fetch_jlcsearch_part(lcsc: str, timeout: float) -> LivePart:
    params = urllib.parse.urlencode({"search": lcsc, "full": "true"})
    source_url = f"https://jlcsearch.tscircuit.com/components/list.json?{params}"
    request = urllib.request.Request(
        source_url,
        headers={
            "User-Agent": "pcb-common-bom-validator/1.0",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8", errors="replace"))
    return parse_jlcsearch_result(lcsc, source_url, payload)


def fetch_live_parts(
    lcsc_codes: Iterable[str],
    source: str,
    timeout: float,
    jobs: int,
) -> tuple[dict[str, LivePart], dict[str, str]]:
    if source == "jlcpcb-api":
        return fetch_jlcpcb_live_parts(lcsc_codes, timeout)

    live_parts: dict[str, LivePart] = {}
    lookup_errors: dict[str, str] = {}
    unique_codes = sorted(set(lcsc_codes))
    if not unique_codes:
        return live_parts, lookup_errors

    max_workers = max(1, min(jobs, len(unique_codes)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(fetch_live_part, lcsc, source, timeout): lcsc
            for lcsc in unique_codes
        }
        for future in concurrent.futures.as_completed(futures):
            lcsc = futures[future]
            try:
                live_parts[lcsc] = future.result()
            except (TimeoutError, OSError, urllib.error.URLError, json.JSONDecodeError, ValueError) as exc:
                lookup_errors[lcsc] = str(exc)
    return live_parts, lookup_errors


def fetch_jlcpcb_live_parts(lcsc_codes: Iterable[str], timeout: float) -> tuple[dict[str, LivePart], dict[str, str]]:
    wanted = set(lcsc_codes)
    live_parts: dict[str, LivePart] = {}
    lookup_errors: dict[str, str] = {}
    last_key = ""
    source_url = "https://open.jlcpcb.com/overseas/openapi/component/getComponentInfos"

    while wanted - set(live_parts):
        components, last_key = fetch_jlcpcb_components_page(last_key, timeout)
        for component in components:
            code = find_json_value(component, {"componentcode", "lcsc", "lcscpart", "lcscpartnumber"})
            if code in wanted and code not in live_parts:
                try:
                    live_parts[code] = parse_jlcpcb_component(code, source_url, component)
                except ValueError as exc:
                    lookup_errors[code] = str(exc)

        if not last_key or not components:
            break

    for lcsc in wanted - set(live_parts) - set(lookup_errors):
        lookup_errors[lcsc] = "not found in JLCPCB component API response"
    return live_parts, lookup_errors


def part_source_startup_errors(source: str) -> list[str]:
    if source == "jlcpcb-api":
        missing = [name for name in ("JLCPCB_APP_ID", "JLCPCB_API_KEY", "JLCPCB_API_SECRET") if not os.environ.get(name)]
        if missing:
            return [f"{source}: missing environment variable(s): {', '.join(missing)}"]
    return []


def main() -> int:
    args = parse_args()
    schematic_path, bom_path = find_board_files(args.board)
    startup_errors = require_csv_columns(bom_path, {"Comment", "Designator", "Footprint", "LCSC"})
    startup_errors += part_source_startup_errors(args.part_source)
    if startup_errors:
        for error in startup_errors:
            print(f"ERROR: {error}")
        print(f"FAIL: {len(startup_errors)} error(s), 0 warning(s)")
        return 1

    schematic_parts = parse_kicad_schematic(schematic_path)
    bom_rows = parse_bom_csv(bom_path)

    errors: list[str] = []
    warnings: list[str] = []
    seen_refs: set[str] = set()
    duplicate_refs: set[str] = set()
    try:
        live_parts, lookup_errors = fetch_live_parts(
            (row.lcsc for row in bom_rows if re.match(r"^C[0-9]+$", row.lcsc)),
            args.part_source,
            args.http_timeout,
            args.jobs,
        )
    except (TimeoutError, OSError, urllib.error.URLError, json.JSONDecodeError, ValueError) as exc:
        live_parts = {}
        lookup_errors = {
            row.lcsc: str(exc)
            for row in bom_rows
            if re.match(r"^C[0-9]+$", row.lcsc)
        }

    for row in bom_rows:
        if not row.refs:
            errors.append(f"BOM row has no designators: {row}")
            continue
        if not row.footprint:
            errors.append(f"{format_refs(row.refs)}: BOM row has blank footprint")
        if row.lcsc and not re.match(r"^C[0-9]+$", row.lcsc):
            errors.append(f"{format_refs(row.refs)}: invalid BOM LCSC code {row.lcsc!r}")

        if len(set(row.refs)) != len(row.refs):
            repeated = sorted({ref for ref in row.refs if row.refs.count(ref) > 1}, key=designator_key)
            errors.append(f"{format_refs(row.refs)}: repeated designator(s) in one BOM row: {format_refs(repeated)}")

        row_parts = []
        for ref in row.refs:
            if ref in seen_refs:
                duplicate_refs.add(ref)
            seen_refs.add(ref)
            part = schematic_parts.get(ref)
            if not part:
                errors.append(f"{ref}: present in BOM but not found in schematic")
                continue
            row_parts.append(part)

        if not row_parts:
            continue

        values = {part.value for part in row_parts}
        footprints = {part.footprint for part in row_parts}
        lcscs = {part.lcsc for part in row_parts}

        if len(values) != 1:
            errors.append(f"{format_refs(row.refs)}: grouped BOM row has mixed schematic values: {sorted(values)}")
        if len(footprints) != 1:
            errors.append(f"{format_refs(row.refs)}: grouped BOM row has mixed schematic footprints: {sorted(footprints)}")
        if len(lcscs) != 1:
            errors.append(f"{format_refs(row.refs)}: grouped BOM row has mixed schematic LCSC fields: {sorted(lcscs)}")

        first = row_parts[0]
        if normalized_value(row.comment) != normalized_value(first.value):
            errors.append(f"{format_refs(row.refs)}: BOM value {row.comment!r} != schematic value {first.value!r}")
        if row.footprint != first.footprint:
            errors.append(f"{format_refs(row.refs)}: BOM footprint {row.footprint!r} != schematic footprint {first.footprint!r}")
        if row.lcsc != first.lcsc:
            errors.append(f"{format_refs(row.refs)}: BOM LCSC {row.lcsc!r} != schematic LCSC {first.lcsc!r}")

        expected_package = footprint_package(row.footprint)
        if not row.lcsc:
            if matches_any(row.footprint, args.allow_missing_footprint):
                warnings.append(f"{format_refs(row.refs)}: blank LCSC allowed for footprint {row.footprint}")
            else:
                errors.append(f"{format_refs(row.refs)}: blank LCSC for assembled footprint {row.footprint}")
            continue

        if row.lcsc in lookup_errors:
            errors.append(f"{format_refs(row.refs)}: failed to query live part data for {row.lcsc}: {lookup_errors[row.lcsc]}")
            continue

        live_part = live_parts.get(row.lcsc)
        if not live_part:
            errors.append(f"{format_refs(row.refs)}: no live part data returned for {row.lcsc}")
            continue
        if not live_part.package:
            errors.append(f"{format_refs(row.refs)}: live part data for {row.lcsc} has blank package")
            continue

        if not compatible_package(expected_package, live_part.package):
            errors.append(
                f"{format_refs(row.refs)}: footprint {row.footprint} requires package {expected_package}; "
                f"live {row.lcsc} package is {live_part.package} ({live_part.source_url})"
            )

    if duplicate_refs:
        errors.append(f"Designator(s) appear in more than one BOM row: {format_refs(duplicate_refs)}")

    assembled_refs = {
        ref
        for ref, part in schematic_parts.items()
        if part.in_bom and part.on_board and not part.dnp and part.footprint
        and not matches_any(part.footprint, args.ignore_missing_bom_footprint)
    }
    missing_from_bom = sorted(assembled_refs - seen_refs, key=designator_key)
    if missing_from_bom:
        errors.append(f"Assembled schematic refs missing from BOM: {format_refs(missing_from_bom)}")

    for warning in sorted(warnings):
        print(f"WARNING: {warning}")
    for error in sorted(errors):
        print(f"ERROR: {error}")

    if errors:
        print(f"FAIL: {len(errors)} error(s), {len(warnings)} warning(s)")
        return 1

    print(f"OK: {args.board} BOM validated ({len(bom_rows)} rows, {len(warnings)} warning(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
