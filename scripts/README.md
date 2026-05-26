# PCB BOM scripts

These scripts are intentionally Python-stdlib only so they can run on a fresh
workstation before fab release.

## Validate a board BOM

Run from this directory:

```sh
python3 validate_bom.py ../controls_board --strict-catalog
python3 validate_bom.py ../gliss_sensor_board --strict-catalog
python3 validate_bom.py ../mcu_board --strict-catalog
```

`--strict-catalog` is accepted only for backward compatibility and no longer
does anything. The common-parts list is advisory; it is not a source of truth
for validation.

The validator compares `fab/bom.csv` against the board schematic fields and live
part metadata. It fails on:

- BOM value, footprint, or LCSC mismatches against the schematic
- grouped BOM rows whose refs do not share the same value, footprint, or LCSC
- repeated designators in a BOM row or across multiple BOM rows
- populated parts missing from the BOM
- blank LCSC fields, except for allowed connector/test-point/mechanical
  footprints
- LCSC codes that cannot be queried from the live part source
- package mismatches, such as a 0201 footprint assigned to a catalog part
  whose live package is 0402

`--allow-missing-footprint` controls which footprints may have a blank LCSC
field. `--ignore-missing-bom-footprint` is separate and controls which
schematic footprints may be absent from `fab/bom.csv`; by default this is only
test points and mounting holes.

The preferred live source is `--part-source jlcpcb-api`, which calls JLCPCB's
component-info API and verifies each C-number's package field. Export
`JLCPCB_APP_ID`, `JLCPCB_API_KEY`, and `JLCPCB_API_SECRET` before running it.

While JLCPCB API access is pending, `--part-source jlcpcb-web` can verify
packages from public JLCPCB part-detail pages. Treat that as an interim check,
not a replacement for the official API.

## Refresh the reviewed common-parts catalog

```sh
python3 collect_common_parts.py ../controls_board ../gliss_sensor_board ../mcu_board --out common_parts.csv
```

This collects the current generated BOMs into `common_parts.csv`. Treat the
result as a review artifact, not an external truth source. The catalog should be
checked before committing because it is the allow-list used by
`validate_bom.py --strict-catalog`.

## Propose missing LCSC fields

```sh
python3 populate_lcsc.py ../controls_board
```

This prints a CSV proposal based on matching schematic value plus footprint
package against `common_parts.csv`. It does not edit schematics.

## Online lookup

No API keys or secrets belong in this repository. When the JLCPCB/LCSC online
lookup is added, it should read credentials from environment variables and cache
only non-secret part metadata.
