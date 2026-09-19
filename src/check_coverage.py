"""Report how well the field dictionary covers the bulk file's headers.

Run after any change to field_dictionary.py.  A header that looks like a bare
Form ADV item code but has no mapping is a gap we must close before that column
can appear on the site.
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import field_dictionary as fd  # noqa: E402

ITEM_CODE = re.compile(r"^\d+[A-Za-z]?(\([^)]*\))*$")


def read_header(csv_path: Path) -> list[str]:
    from ingest import detect_encoding

    with csv_path.open("r", encoding=detect_encoding(csv_path), newline="") as fh:
        return next(csv.reader(fh))


def main(csv_path: Path) -> int:
    header = read_header(csv_path)
    coded = [h for h in header if ITEM_CODE.match(h.strip())]
    counts = [h for h in header if h.strip().startswith("Count of ")]
    plain = [h for h in header if h not in coded and h not in counts]

    mapped = [h for h in coded if fd.lookup(h)]
    unmapped = [h for h in coded if not fd.lookup(h)]
    unverified = [h for h in coded if (f := fd.lookup(h)) and not f.verified]

    print(f"columns in file      : {len(header)}")
    print(f"  bare item codes    : {len(coded)}")
    print(f"  disclosure counts  : {len(counts)}")
    print(f"  already plain text : {len(plain)}")
    print()
    print(f"dictionary entries   : {fd.coverage()['total']}")
    print(f"codes mapped         : {len(mapped)} / {len(coded)}")
    print(f"  of which unverified: {len(unverified)}")
    print(f"codes UNMAPPED       : {len(unmapped)}")

    if unmapped:
        print("\n--- unmapped codes ---")
        for h in unmapped:
            print(f"  {h}")

    # Dictionary entries that do not correspond to any column in this file.
    orphans = [c for c in fd.FIELDS if c not in {h.strip() for h in header}]
    if orphans:
        print(f"\n--- {len(orphans)} dictionary entries with no column in this file ---")
        for c in orphans:
            print(f"  {c}")

    return 1 if unmapped else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: check_coverage.py <path to bulk CSV>", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(main(Path(sys.argv[1])))
