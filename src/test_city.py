"""Tests for city normalisation, including the merges we deliberately refuse."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from city import canonical_names, city_key  # noqa: E402

# (state, spelling A, spelling B, should they merge)
PAIRS = [
    ("NY", "NEW YORK", "NEW YORK CITY", True),
    ("NY", "NEW YORK", "NEW  YORK", True),
    ("NY", "NEW YORK", "NEW YORK,", True),
    ("MO", "ST. LOUIS", "SAINT LOUIS", True),
    ("MO", "ST. LOUIS", "ST LOUIS", True),
    ("MO", "ST. LOUIS", "ST.LOUIS", True),
    ("FL", "FORT LAUDERDALE", "FT. LAUDERDALE", True),
    ("FL", "WEST PALM BEACH", "W. PALM BEACH", True),
    ("SC", "MOUNT PLEASANT", "MT PLEASANT", True),
    ("NC", "WINSTON-SALEM", "WINSTON SALEM", True),
    ("MO", "O'FALLON", "OFALLON", True),
    ("CA", "CARMEL BY THE SEA", "CARMEL-BY-THE-SEA", True),
    ("IL", "OAK BROOK", "OAKBROOK", True),
    ("DC", "WASHINGTON", "WASHINGTON, D.C.", True),
    ("OH", "WEST CHESTER", "WEST CHESTER TOWNSHIP", True),
    ("VT", "SOUTH BURLINGTON", "S BURLINGTON", True),
    # Deliberate non-merges: these are or could be distinct places.
    ("CO", "GREENWOOD", "GREENWOOD VILLAGE", False),
    ("NY", "GARDEN CITY", "GARDEN", False),
    ("IL", "OAK BROOK", "OAKBROOK TERRACE", False),
    ("MO", "KANSAS CITY", "KANSAS", False),
    ("CA", "SAN JOSE", "SAN JOSE HILLS", False),
]

BLANKS = ["", "   ", None]


def main() -> int:
    failures = []
    for state, a, b, should_merge in PAIRS:
        merged = city_key(a, state) == city_key(b, state)
        ok = merged == should_merge
        verb = "merges" if merged else "stays apart from"
        print(f"  [{'ok  ' if ok else 'FAIL'}] {state}: {a!r} {verb} {b!r}")
        if not ok:
            failures.append(f"{state}: {a!r} vs {b!r}: expected merge={should_merge}")

    print()
    for blank in BLANKS:
        ok = city_key(blank, "NY") is None
        print(f"  [{'ok  ' if ok else 'FAIL'}] blank {blank!r} -> None")
        if not ok:
            failures.append(f"blank {blank!r} did not return None")

    print()
    chosen = canonical_names(
        [
            ("MO", "ST. LOUIS", 63),
            ("MO", "SAINT LOUIS", 14),
            ("MO", "ST LOUIS", 11),
            ("NY", "NEW YORK", 1987),
            ("NY", "NEW YORK CITY", 11),
        ]
    )
    expectations = {
        ("MO", "SAINTLOUIS"): "ST. LOUIS",
        ("NY", "NEWYORK"): "NEW YORK",
    }
    for group, expected in expectations.items():
        actual = chosen.get(group)
        ok = actual == expected
        print(f"  [{'ok  ' if ok else 'FAIL'}] display name for {group} -> {actual!r}")
        if not ok:
            failures.append(f"display {group}: expected {expected!r}, got {actual!r}")

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nall city tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
