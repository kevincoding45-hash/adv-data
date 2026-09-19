"""Tests for diff materiality rules, run against a synthetic in-memory database.

Run: .venv\\Scripts\\python.exe src\\test_diff.py
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import diff  # noqa: E402
import ingest  # noqa: E402

OLD, NEW = "2026-08-01", "2026-09-01"

# crd, code, old value, new value, expected-to-be-reported
FIELD_CASES = [
    ("100", "11C(1)", 0.0, 1.0, True, "new disciplinary flag"),
    ("100", "11D(4)", 1.0, 0.0, True, "cleared disciplinary flag"),
    ("101", "5F(2)(c)", 100_000_000.0, 101_000_000.0, False, "RAUM +1% / +$1M is noise"),
    ("102", "5F(2)(c)", 100_000_000.0, 150_000_000.0, True, "RAUM +50% / +$50M is material"),
    ("103", "5F(2)(c)", 1_000_000_000.0, 1_005_000_000.0, False, "RAUM +0.5% / +$5M is noise"),
    ("104", "5F(2)(c)", 40_000_000.0, 60_000_000.0, True, "RAUM +50% / +$20M is material"),
    ("105", "5A", 40.0, 41.0, False, "employees +1 is noise"),
    ("106", "5A", 40.0, 60.0, True, "employees +50% / +20 is material"),
    ("107", "5A", 4.0, 9.0, True, "employees 4 -> 9 more than doubles a small firm"),
    ("109", "5A", 400.0, 404.0, False, "employees +4 on a large base fails the absolute floor"),
    ("108", "7B", 0.0, 1.0, False, "unverified field is never published"),
]


def build(conn: sqlite3.Connection) -> None:
    conn.executescript(ingest.SCHEMA)
    crds = sorted({crd for crd, *_ in FIELD_CASES} | {"200", "201", "300"})

    for snap in (OLD, NEW):
        conn.execute(
            "INSERT INTO snapshots VALUES (?,?,?,?,?)",
            (snap, f"{snap}.csv", len(crds), 448, "2026-09-17T00:00:00"),
        )

    def firm(snap: str, crd: str, name: str) -> tuple:
        return (
            snap, crd, f"801-{crd}", f"{name} LLC", name, "AUSTIN", "AUSTIN", "TX", "78701",
            "United States", "512-555-0100", f"https://{crd}.example", "2026-07-01",
            "Form ADV", 1.0e8, 5.0e7, 100, 40, 20, 0, 0,
        )

    placeholders = "(" + ",".join("?" * 21) + ")"
    for crd in crds:
        if crd != "201":  # 201 registers only in the new snapshot
            conn.execute(f"INSERT INTO firms VALUES {placeholders}", firm(OLD, crd, f"FIRM {crd}"))
        if crd != "200":  # 200 deregisters after the old snapshot
            name = "FIRM 300 RENAMED" if crd == "300" else f"FIRM {crd}"
            conn.execute(f"INSERT INTO firms VALUES {placeholders}", firm(NEW, crd, name))

    for crd, code, old_value, new_value, _, _ in FIELD_CASES:
        conn.execute("INSERT INTO firm_fields VALUES (?,?,?,?,?)", (OLD, crd, code, old_value, None))
        conn.execute("INSERT INTO firm_fields VALUES (?,?,?,?,?)", (NEW, crd, code, new_value, None))
    conn.commit()


def main() -> int:
    conn = sqlite3.connect(":memory:")
    build(conn)
    diff.compute(conn, OLD, NEW)

    reported = {
        (crd, code)
        for crd, code in conn.execute(
            "SELECT crd, code FROM diffs WHERE change_type='field'"
        )
    }

    failures: list[str] = []
    for crd, code, old_value, new_value, expected, why in FIELD_CASES:
        actual = (crd, code) in reported
        status = "ok  " if actual == expected else "FAIL"
        if actual != expected:
            failures.append(f"{code} for {crd}: expected reported={expected}, got {actual} ({why})")
        print(f"  [{status}] {code:<10} {old_value:>15,.0f} -> {new_value:<15,.0f}  {why}")

    checks = {
        "registered": ("SELECT COUNT(*) FROM diffs WHERE change_type='registered'", 1),
        "deregistered": ("SELECT COUNT(*) FROM diffs WHERE change_type='deregistered'", 1),
        "identity (rename)": (
            "SELECT COUNT(*) FROM diffs WHERE change_type='identity' AND code='primary_name'",
            1,
        ),
    }
    print()
    for label, (sql, expected_count) in checks.items():
        actual_count = conn.execute(sql).fetchone()[0]
        ok = actual_count == expected_count
        print(f"  [{'ok  ' if ok else 'FAIL'}] {label}: {actual_count} (expected {expected_count})")
        if not ok:
            failures.append(f"{label}: expected {expected_count}, got {actual_count}")

    conn.close()
    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nall diff tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
