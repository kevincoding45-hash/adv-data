"""Ad-hoc sanity queries against an ingested snapshot.

Not part of the pipeline -- a quick way to confirm a month looks right and to
see what the data can actually support on the site.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
import field_dictionary as fd  # noqa: E402


def main() -> int:
    conn = sqlite3.connect(config.DB_PATH)
    snapshot = conn.execute("SELECT MAX(snapshot_date) FROM snapshots").fetchone()[0]
    print(f"snapshot: {snapshot}\n")

    print("-- RAUM distribution --")
    for label, sql in (
        ("median", "SELECT total_raum FROM firms WHERE snapshot_date=? AND total_raum>0 ORDER BY total_raum LIMIT 1 OFFSET (SELECT COUNT(*)/2 FROM firms WHERE snapshot_date=? AND total_raum>0)"),
    ):
        value = conn.execute(sql, (snapshot, snapshot)).fetchone()[0]
        print(f"  {label}: ${value:,.0f}")
    total = conn.execute(
        "SELECT SUM(total_raum) FROM firms WHERE snapshot_date=?", (snapshot,)
    ).fetchone()[0]
    print(f"  industry total: ${total/1e12:,.1f} trillion\n")

    print("-- page inventory --")
    for label, sql in (
        ("firm pages", "SELECT COUNT(*) FROM firms WHERE snapshot_date=?"),
        ("state pages", "SELECT COUNT(DISTINCT state) FROM firms WHERE snapshot_date=? AND state IS NOT NULL"),
        ("city pages (>=5 firms)", "SELECT COUNT(*) FROM (SELECT city,state FROM firms WHERE snapshot_date=? AND city IS NOT NULL GROUP BY city,state HAVING COUNT(*)>=5)"),
        ("firms with a disclosure", "SELECT COUNT(*) FROM firms WHERE snapshot_date=? AND disclosure_flag_count>0"),
    ):
        print(f"  {label}: {conn.execute(sql, (snapshot,)).fetchone()[0]:,}")

    print("\n-- compensation mix (Item 5.E.) --")
    for code, label in fd.COMPENSATION.items():
        (n,) = conn.execute(
            "SELECT COUNT(*) FROM firm_fields WHERE snapshot_date=? AND code=? AND num_value=1",
            (snapshot, f"5E({code})"),
        ).fetchone()
        print(f"  {label:<48} {n:>6,}")

    print("\n-- fee-only: % of AUM and/or hourly/fixed, no commissions --")
    (fee_only,) = conn.execute(
        """
        SELECT COUNT(*) FROM firms f
        WHERE f.snapshot_date = ?
          AND EXISTS (SELECT 1 FROM firm_fields x WHERE x.snapshot_date=f.snapshot_date
                      AND x.crd=f.crd AND x.code='5E(1)' AND x.num_value=1)
          AND NOT EXISTS (SELECT 1 FROM firm_fields y WHERE y.snapshot_date=f.snapshot_date
                          AND y.crd=f.crd AND y.code='5E(5)' AND y.num_value=1)
        """,
        (snapshot,),
    ).fetchone()
    print(f"  {fee_only:,} firms")

    print("\n-- most-disclosed firms (event counts, not findings of wrongdoing) --")
    rows = conn.execute(
        """
        SELECT primary_name, state, disclosure_flag_count, disclosure_event_count, total_raum
        FROM firms WHERE snapshot_date=?
        ORDER BY disclosure_event_count DESC LIMIT 5
        """,
        (snapshot,),
    ).fetchall()
    for name, state, flags, events, raum in rows:
        raum_text = f"${raum/1e9:,.1f}B" if raum else "n/a"
        print(f"  {events:>4} events / {flags:>2} categories  {raum_text:>10}  {name} ({state})")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
