"""Invariants for the field dictionary.

The publish/verified split is the guardrail that keeps ungrounded labels off a
site about named real businesses, so it is worth asserting rather than trusting.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import field_dictionary as fd  # noqa: E402

VALID_TYPES = {"yn", "int", "money", "pct", "band", "text", "date"}


def main() -> int:
    failures = []
    coverage = fd.coverage()
    print(f"  fields: {coverage['total']}  verified: {coverage['verified']}  publishable: {coverage['publishable']}")

    leaks = [c for c, f in fd.FIELDS.items() if f.publish and not f.verified]
    if leaks:
        failures.append(f"unverified fields marked publishable: {leaks}")
    print(f"  [{'ok  ' if not leaks else 'FAIL'}] no unverified field is publishable")

    bad_labels = [c for c, f in fd.FIELDS.items() if not f.label.strip()]
    if bad_labels:
        failures.append(f"empty labels: {bad_labels}")
    print(f"  [{'ok  ' if not bad_labels else 'FAIL'}] every field has a label")

    bad_types = [(c, f.dtype) for c, f in fd.FIELDS.items() if f.dtype not in VALID_TYPES]
    if bad_types:
        failures.append(f"unknown dtypes: {bad_types}")
    print(f"  [{'ok  ' if not bad_types else 'FAIL'}] every dtype is known")

    if coverage["verified"] < 200:
        failures.append(f"only {coverage['verified']} verified fields")
    print(f"  [{'ok  ' if coverage['verified'] >= 200 else 'FAIL'}] at least 200 verified fields")

    # Every Item 11 flag must be publishable -- they are the core content.
    disclosure = [c for c in fd.FIELDS if c.startswith("11")]
    unpublishable = [c for c in disclosure if not fd.FIELDS[c].publish]
    if unpublishable:
        failures.append(f"disclosure fields not publishable: {unpublishable}")
    print(f"  [{'ok  ' if not unpublishable else 'FAIL'}] all {len(disclosure)} Item 11 fields publishable")

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nall dictionary tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
