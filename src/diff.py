"""Month-over-month change detection between two ingested snapshots.

This is the part a competitor cannot clone by downloading the file once: the
interesting content is not the current state, it is what moved -- firms newly
flagging a disciplinary disclosure, assets swinging, advisers deregistering,
firms rebranding or relocating.

Not every difference is worth publishing. A firm reporting 41 employees instead
of 40 is noise. Materiality thresholds live in MATERIALITY below, and only
material rows are written to the `diffs` table.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
import field_dictionary as fd  # noqa: E402

SCHEMA = """
CREATE TABLE IF NOT EXISTS diffs (
    from_snapshot TEXT NOT NULL,
    to_snapshot   TEXT NOT NULL,
    crd           TEXT NOT NULL,
    change_type   TEXT NOT NULL,
    code          TEXT,
    old_num       REAL,
    new_num       REAL,
    old_text      TEXT,
    new_text      TEXT,
    delta         REAL,
    pct_change    REAL
);
CREATE INDEX IF NOT EXISTS idx_diffs_pair ON diffs(from_snapshot, to_snapshot, change_type);
CREATE INDEX IF NOT EXISTS idx_diffs_crd  ON diffs(from_snapshot, to_snapshot, crd);
"""


@dataclass(frozen=True)
class Threshold:
    """A change is material if it clears the absolute floor AND the percentage."""

    min_abs: float = 0.0
    min_pct: float = 0.0


# Per-type materiality. Checkboxes and dates are always material; numbers must
# move enough to be worth a sentence.
MATERIALITY: dict[str, Threshold] = {
    "yn": Threshold(),
    "date": Threshold(),
    "text": Threshold(),
    "band": Threshold(),
    "money": Threshold(min_abs=10_000_000, min_pct=0.25),
    "int": Threshold(min_abs=5, min_pct=0.20),
    "pct": Threshold(min_abs=5, min_pct=0.0),
}

# Identity fields on `firms` worth reporting when they change.
IDENTITY_TRACKED = {
    "primary_name": "Primary business name",
    "legal_name": "Legal name",
    # Keyed, not raw: a firm correcting "ST LOUIS" to "St. Louis" has not moved.
    "city_key": "Main office city",
    "state": "Main office state",
    "website": "Website address",
}

DISCLOSURE_CODES = {c for c in fd.FIELDS if c.startswith("11")}


def is_material(dtype: str, old: float | None, new: float | None) -> bool:
    rule = MATERIALITY.get(dtype, Threshold())
    if old is None or new is None:
        return True
    delta = abs(new - old)
    if delta == 0:
        return False
    if delta < rule.min_abs:
        return False
    if rule.min_pct and old:
        if delta / abs(old) < rule.min_pct:
            return False
    return True


def compute(conn: sqlite3.Connection, old_snap: str, new_snap: str) -> dict[str, int]:
    conn.executescript(SCHEMA)
    conn.execute(
        "DELETE FROM diffs WHERE from_snapshot = ? AND to_snapshot = ?",
        (old_snap, new_snap),
    )

    for snap in (old_snap, new_snap):
        if not conn.execute(
            "SELECT 1 FROM snapshots WHERE snapshot_date = ?", (snap,)
        ).fetchone():
            raise SystemExit(f"snapshot {snap} has not been ingested")

    counts = {"registered": 0, "deregistered": 0, "identity": 0, "field": 0}
    rows: list[tuple] = []

    # --- firms that appeared or disappeared -------------------------------
    for crd, name in conn.execute(
        """
        SELECT n.crd, n.primary_name FROM firms n
        WHERE n.snapshot_date = ?
          AND NOT EXISTS (SELECT 1 FROM firms o WHERE o.snapshot_date = ? AND o.crd = n.crd)
        """,
        (new_snap, old_snap),
    ):
        rows.append((old_snap, new_snap, crd, "registered", None, None, None, None, name, None, None))
        counts["registered"] += 1

    for crd, name in conn.execute(
        """
        SELECT o.crd, o.primary_name FROM firms o
        WHERE o.snapshot_date = ?
          AND NOT EXISTS (SELECT 1 FROM firms n WHERE n.snapshot_date = ? AND n.crd = o.crd)
        """,
        (old_snap, new_snap),
    ):
        rows.append((old_snap, new_snap, crd, "deregistered", None, None, None, None, name, None, None))
        counts["deregistered"] += 1

    # --- identity changes for continuing firms ----------------------------
    for column, label in IDENTITY_TRACKED.items():
        for crd, old_value, new_value in conn.execute(
            f"""
            SELECT o.crd, o.{column}, n.{column}
            FROM firms o JOIN firms n ON n.crd = o.crd
            WHERE o.snapshot_date = ? AND n.snapshot_date = ?
              AND IFNULL(o.{column}, '') <> IFNULL(n.{column}, '')
            """,
            (old_snap, new_snap),
        ):
            rows.append(
                (old_snap, new_snap, crd, "identity", column, None, None, old_value, new_value, None, None)
            )
            counts["identity"] += 1

    # --- field-level changes ----------------------------------------------
    # firm_fields is keyed (snapshot_date, crd, code) WITHOUT ROWID, so this
    # join is an index scan on both sides.
    query = """
        SELECT COALESCE(o.crd, n.crd)   AS crd,
               COALESCE(o.code, n.code) AS code,
               o.num_value, n.num_value, o.text_value, n.text_value
        FROM       (SELECT crd, code, num_value, text_value FROM firm_fields WHERE snapshot_date = :old) o
        LEFT JOIN  (SELECT crd, code, num_value, text_value FROM firm_fields WHERE snapshot_date = :new) n
               ON n.crd = o.crd AND n.code = o.code
        WHERE IFNULL(o.num_value, -1e18) <> IFNULL(n.num_value, -1e18)
           OR IFNULL(o.text_value, '')   <> IFNULL(n.text_value, '')

        UNION ALL

        SELECT n.crd, n.code, NULL, n.num_value, NULL, n.text_value
        FROM       (SELECT crd, code, num_value, text_value FROM firm_fields WHERE snapshot_date = :new) n
        LEFT JOIN  (SELECT crd, code FROM firm_fields WHERE snapshot_date = :old) o
               ON o.crd = n.crd AND o.code = n.code
        WHERE o.crd IS NULL
    """

    for crd, code, old_num, new_num, old_text, new_text in conn.execute(
        query, {"old": old_snap, "new": new_snap}
    ):
        field = fd.FIELDS.get(code)
        if field is None or not field.publish:
            continue
        if old_num is not None or new_num is not None:
            if not is_material(field.dtype, old_num, new_num):
                continue
            delta = (new_num - old_num) if (old_num is not None and new_num is not None) else None
            pct = (delta / abs(old_num)) if (delta is not None and old_num) else None
        else:
            delta = pct = None
        rows.append(
            (old_snap, new_snap, crd, "field", code, old_num, new_num, old_text, new_text, delta, pct)
        )
        counts["field"] += 1

    conn.executemany(
        "INSERT INTO diffs VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows
    )
    conn.commit()
    return counts


def report(conn: sqlite3.Connection, old_snap: str, new_snap: str, limit: int = 8) -> None:
    def scalar(sql: str, params: tuple) -> int:
        return conn.execute(sql, params).fetchone()[0]

    pair = (old_snap, new_snap)
    print(f"\n=== {old_snap} -> {new_snap} ===\n")

    print("-- firm population --")
    for kind in ("registered", "deregistered"):
        n = scalar(
            "SELECT COUNT(*) FROM diffs WHERE from_snapshot=? AND to_snapshot=? AND change_type=?",
            pair + (kind,),
        )
        print(f"  {kind:<14} {n:>6,}")

    print("\n-- newly reported disclosures (N -> Y) --")
    newly = conn.execute(
        """
        SELECT d.crd, f.primary_name, f.state, d.code
        FROM diffs d JOIN firms f ON f.crd = d.crd AND f.snapshot_date = ?
        WHERE d.from_snapshot=? AND d.to_snapshot=? AND d.change_type='field'
          AND d.code LIKE '11%' AND IFNULL(d.old_num,0)=0 AND d.new_num=1
        ORDER BY f.primary_name
        """,
        (new_snap,) + pair,
    ).fetchall()
    affected = len({row[0] for row in newly})
    print(f"  {len(newly):,} new disclosure flags across {affected:,} firms")
    for crd, name, state, code in newly[:limit]:
        print(f"    {name} ({state}): {fd.label_for(code)}")

    print("\n-- disclosures no longer reported (Y -> N) --")
    cleared = scalar(
        """
        SELECT COUNT(*) FROM diffs WHERE from_snapshot=? AND to_snapshot=?
          AND change_type='field' AND code LIKE '11%' AND old_num=1 AND IFNULL(new_num,0)=0
        """,
        pair,
    )
    print(f"  {cleared:,}")

    print("\n-- largest asset moves (total regulatory AUM) --")
    movers = conn.execute(
        """
        SELECT f.primary_name, f.state, d.old_num, d.new_num, d.delta, d.pct_change
        FROM diffs d JOIN firms f ON f.crd = d.crd AND f.snapshot_date = ?
        WHERE d.from_snapshot=? AND d.to_snapshot=? AND d.code='5F(2)(c)'
          AND d.old_num IS NOT NULL AND d.new_num IS NOT NULL
        ORDER BY ABS(d.delta) DESC LIMIT ?
        """,
        (new_snap,) + pair + (limit,),
    ).fetchall()
    for name, state, old, new, delta, pct in movers:
        arrow = "+" if delta > 0 else ""
        pct_text = f"{pct:+.0%}" if pct is not None else "n/a"
        print(f"    {name} ({state}): ${old/1e9:,.1f}B -> ${new/1e9:,.1f}B  ({arrow}{delta/1e9:,.1f}B, {pct_text})")

    print("\n-- rebrands and relocations --")
    for column, label in IDENTITY_TRACKED.items():
        n = scalar(
            "SELECT COUNT(*) FROM diffs WHERE from_snapshot=? AND to_snapshot=? AND change_type='identity' AND code=?",
            pair + (column,),
        )
        print(f"  {label:<24} {n:>6,}")

    print("\n-- most-changed fields --")
    for code, n in conn.execute(
        """
        SELECT code, COUNT(*) AS n FROM diffs
        WHERE from_snapshot=? AND to_snapshot=? AND change_type='field'
        GROUP BY code ORDER BY n DESC LIMIT ?
        """,
        pair + (limit,),
    ):
        print(f"  {n:>6,}  {code}  {fd.label_for(code)[:60]}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("old_snapshot")
    parser.add_argument("new_snapshot")
    parser.add_argument("--db", type=Path, default=config.DB_PATH)
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    counts = compute(conn, args.old_snapshot, args.new_snapshot)
    print(
        f"changes: {counts['registered']:,} registered, "
        f"{counts['deregistered']:,} deregistered, "
        f"{counts['identity']:,} identity, {counts['field']:,} field"
    )
    report(conn, args.old_snapshot, args.new_snapshot)
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
