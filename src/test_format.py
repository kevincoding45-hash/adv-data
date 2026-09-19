"""Tests for the display helpers in generate_site.

Firm names arrive from the SEC in all caps and go straight into page titles and
<h1>s, so the formatting rules are worth pinning down.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from generate_site import money, slugify, title_case  # noqa: E402

TITLE_CASES = [
    ("VANGUARD GROUP INC", "Vanguard Group Inc"),
    ("J.P. MORGAN INVESTMENT MANAGEMENT INC.", "J.P. Morgan Investment Management Inc."),
    ("VANGUARD CAPITAL MANAGEMENT, LLC", "Vanguard Capital Management, LLC"),
    ("CERBERUS CAPITAL MANAGEMENT, L.P.", "Cerberus Capital Management, L.P."),
    ("BANK OF AMERICA", "Bank of America"),
    ("MERRILL LYNCH, PIERCE, FENNER & SMITH INCORPORATED",
     "Merrill Lynch, Pierce, Fenner & Smith Incorporated"),
    ("U.S. BANCORP", "U.S. Bancorp"),
    ("NEW YORK", "New York"),
    ("", ""),
]

SLUG_CASES = [
    ("VANGUARD GROUP INC", "vanguard-group-inc"),
    ("J.P. MORGAN INVESTMENT MANAGEMENT INC.", "j-p-morgan-investment-management-inc"),
    ("  ---  ", "unnamed"),
    ("St. Louis", "st-louis"),
]

MONEY_CASES = [
    (None, "Not reported"),
    (0.0, "$0"),
    (1_500.0, "$1,500"),
    (2_400_000.0, "$2.4 million"),
    (3_120_000_000.0, "$3.12 billion"),
    (11_090_000_000_000.0, "$11.09 trillion"),
]


def main() -> int:
    failures = []
    for raw, expected in TITLE_CASES:
        actual = title_case(raw)
        ok = actual == expected
        print(f"  [{'ok  ' if ok else 'FAIL'}] title_case({raw!r}) -> {actual!r}")
        if not ok:
            failures.append(f"title_case({raw!r}): expected {expected!r}, got {actual!r}")

    print()
    for raw, expected in SLUG_CASES:
        actual = slugify(raw)
        ok = actual == expected
        print(f"  [{'ok  ' if ok else 'FAIL'}] slugify({raw!r}) -> {actual!r}")
        if not ok:
            failures.append(f"slugify({raw!r}): expected {expected!r}, got {actual!r}")

    print()
    for raw, expected in MONEY_CASES:
        actual = money(raw)
        ok = actual == expected
        print(f"  [{'ok  ' if ok else 'FAIL'}] money({raw!r}) -> {actual!r}")
        if not ok:
            failures.append(f"money({raw!r}): expected {expected!r}, got {actual!r}")

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nall formatting tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
