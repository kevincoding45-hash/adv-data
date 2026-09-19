"""Load a monthly Form ADV bulk CSV into SQLite, with typing and quality gates.

Writes three tables:
    snapshots    one row per ingested month
    firms        identity plus the headline metrics, one row per firm per month
    firm_fields  every mapped Form ADV field, long format, for diffing

The long format is what makes month-over-month change detection cheap: any field
can be compared without a schema migration.
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import city as city_mod  # noqa: E402
import config  # noqa: E402
import field_dictionary as fd  # noqa: E402

csv.field_size_limit(10_000_000)

# Headers carrying identity, as spelled in the bulk file.
IDENTITY = {
    "crd": "Organization CRD#",
    "sec_number": "SEC#",
    "legal_name": "Legal Name",
    "primary_name": "Primary Business Name",
    "city": "Main Office City",
    "state": "Main Office State",
    "postal": "Main Office Postal Code",
    "country": "Main Office Country",
    "phone": "Main Office Telephone Number",
    "website": "Website Address",
    "latest_filing_date": "Latest ADV Filing Date",
    "form_version": "Form Version",
}

# Headline metrics promoted onto `firms` so page generation avoids a join.
HEADLINE = {
    "total_raum": "5F(2)(c)",
    "discretionary_raum": "5F(2)(a)",
    "total_accounts": "5F(2)(f)",
    "total_employees": "5A",
    "advisory_employees": "5B(1)",
}

DISCLOSURE_FLAGS = [c for c in fd.FIELDS if c.startswith("11") and c != "11"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_date  TEXT PRIMARY KEY,
    source_file    TEXT NOT NULL,
    row_count      INTEGER NOT NULL,
    column_count   INTEGER NOT NULL,
    ingested_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS firms (
    snapshot_date          TEXT NOT NULL,
    crd                    TEXT NOT NULL,
    sec_number             TEXT,
    legal_name             TEXT,
    primary_name           TEXT,
    city                   TEXT,
    city_key               TEXT,
    state                  TEXT,
    postal                 TEXT,
    country                TEXT,
    phone                  TEXT,
    website                TEXT,
    latest_filing_date     TEXT,
    form_version           TEXT,
    total_raum             REAL,
    discretionary_raum     REAL,
    total_accounts         INTEGER,
    total_employees        INTEGER,
    advisory_employees     INTEGER,
    disclosure_flag_count  INTEGER NOT NULL DEFAULT 0,
    disclosure_event_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (snapshot_date, crd)
);
CREATE TABLE IF NOT EXISTS firm_fields (
    snapshot_date TEXT NOT NULL,
    crd           TEXT NOT NULL,
    code          TEXT NOT NULL,
    num_value     REAL,
    text_value    TEXT,
    PRIMARY KEY (snapshot_date, crd, code)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_firms_state ON firms(snapshot_date, state);
CREATE INDEX IF NOT EXISTS idx_firms_city  ON firms(snapshot_date, state, city_key);
CREATE INDEX IF NOT EXISTS idx_firms_raum  ON firms(snapshot_date, total_raum);
CREATE INDEX IF NOT EXISTS idx_fields_code ON firm_fields(snapshot_date, code);
"""


class QualityError(RuntimeError):
    """A snapshot failed validation and must not be published."""


# The SEC's bulk files are Windows-1252, not UTF-8 -- free-text fields carry
# non-breaking spaces and smart quotes that raise on a UTF-8 decode.
ENCODINGS = ("utf-8-sig", "cp1252")


def detect_encoding(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ENCODINGS:
        try:
            raw.decode(encoding)
            return encoding
        except UnicodeDecodeError:
            continue
    raise QualityError(f"{path.name} decodes under none of {ENCODINGS}")


def parse_money(raw: str) -> float | None:
    """'1,234,567.00' -> 1234567.0 ; '.00' -> 0.0 ; '' -> None."""
    text = raw.strip().replace(",", "").replace("$", "")
    if not text or text in {".", "-"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_int(raw: str) -> int | None:
    text = raw.strip().replace(",", "")
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def parse_yn(raw: str) -> bool | None:
    text = raw.strip().upper()
    if text in {"Y", "YES", "TRUE", "1"}:
        return True
    if text in {"N", "NO", "FALSE", "0"}:
        return False
    return None


def parse_date(raw: str) -> str | None:
    text = raw.strip()
    if not text:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse_value(raw: str, dtype: str) -> tuple[float | None, str | None]:
    """Return (num_value, text_value) for storage in firm_fields."""
    if raw is None:
        return None, None
    if dtype == "money":
        return parse_money(raw), None
    if dtype == "int":
        value = parse_int(raw)
        return (float(value) if value is not None else None), None
    if dtype == "pct":
        return parse_money(raw), None
    if dtype == "yn":
        flag = parse_yn(raw)
        return (None if flag is None else float(flag)), None
    if dtype == "date":
        return None, parse_date(raw)
    text = raw.strip()
    return None, (text or None)


def snapshot_date_from_name(path: Path) -> str:
    """'ia092026-registered.csv' or 'ia09012026-registered.csv' -> '2026-09-01'."""
    stem = path.stem
    digits = "".join(ch for ch in stem.split("-")[0] if ch.isdigit())
    if len(digits) == 8:  # MMDDYYYY
        return f"{digits[4:]}-{digits[:2]}-{digits[2:4]}"
    if len(digits) == 6:  # MMYYYY
        return f"{digits[2:]}-{digits[:2]}-01"
    raise ValueError(f"cannot derive snapshot date from {path.name!r}")


def ingest(csv_path: Path, db_path: Path, *, replace: bool = False) -> str:
    snapshot = snapshot_date_from_name(csv_path)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)

    existing = conn.execute(
        "SELECT row_count FROM snapshots WHERE snapshot_date = ?", (snapshot,)
    ).fetchone()
    if existing and not replace:
        print(f"snapshot {snapshot} already ingested ({existing[0]} rows); use --replace")
        conn.close()
        return snapshot
    if existing:
        for table in ("firm_fields", "firms", "snapshots"):
            conn.execute(f"DELETE FROM {table} WHERE snapshot_date = ?", (snapshot,))

    encoding = detect_encoding(csv_path)
    with csv_path.open("r", encoding=encoding, newline="") as fh:
        reader = csv.reader(fh)
        header = [h.strip() for h in next(reader)]
        index = {name: i for i, name in enumerate(header)}

        mapped = [(name, fd.FIELDS[name]) for name in header if name in fd.FIELDS]
        missing_identity = [h for h in IDENTITY.values() if h not in index]
        if missing_identity:
            raise QualityError(f"identity columns absent from file: {missing_identity}")

        firm_rows: list[tuple] = []
        field_rows: list[tuple] = []
        seen_crd: set[str] = set()
        duplicates = 0

        for row in reader:
            if len(row) < len(header):
                row = row + [""] * (len(header) - len(row))

            crd = row[index[IDENTITY["crd"]]].strip()
            if not crd:
                continue
            if crd in seen_crd:
                duplicates += 1
                continue
            seen_crd.add(crd)

            flag_count = 0
            event_count = 0
            for code in DISCLOSURE_FLAGS:
                if code in index and parse_yn(row[index[code]]):
                    flag_count += 1
                count_col = fd.disclosure_count_column(code)
                if count_col in index:
                    event_count += parse_int(row[index[count_col]]) or 0

            identity = {
                key: (row[index[col]].strip() or None) for key, col in IDENTITY.items()
            }
            identity["latest_filing_date"] = parse_date(
                row[index[IDENTITY["latest_filing_date"]]]
            )

            headline = {}
            for key, code in HEADLINE.items():
                raw = row[index[code]] if code in index else ""
                headline[key] = (
                    parse_money(raw) if "raum" in key else parse_int(raw)
                )

            firm_rows.append(
                (
                    snapshot,
                    crd,
                    identity["sec_number"],
                    identity["legal_name"],
                    identity["primary_name"],
                    identity["city"],
                    city_mod.city_key(identity["city"], identity["state"]),
                    identity["state"],
                    identity["postal"],
                    identity["country"],
                    identity["phone"],
                    identity["website"],
                    identity["latest_filing_date"],
                    identity["form_version"],
                    headline["total_raum"],
                    headline["discretionary_raum"],
                    headline["total_accounts"],
                    headline["total_employees"],
                    headline["advisory_employees"],
                    flag_count,
                    event_count,
                )
            )

            for name, field in mapped:
                num, text = parse_value(row[index[name]], field.dtype)
                if num is None and text is None:
                    continue
                field_rows.append((snapshot, crd, field.code, num, text))

    conn.executemany(
        "INSERT INTO firms VALUES (" + ",".join("?" * 21) + ")", firm_rows
    )
    conn.executemany("INSERT INTO firm_fields VALUES (?,?,?,?,?)", field_rows)
    conn.execute(
        "INSERT INTO snapshots VALUES (?,?,?,?,?)",
        (snapshot, csv_path.name, len(firm_rows), len(header), datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()

    print(f"snapshot {snapshot}: {len(firm_rows):,} firms, {len(field_rows):,} field values")
    if duplicates:
        print(f"  note: skipped {duplicates} duplicate CRD rows")

    validate(conn, snapshot)
    conn.close()
    return snapshot


def validate(conn: sqlite3.Connection, snapshot: str) -> None:
    """Fail loudly rather than publish a broken month."""
    problems: list[str] = []

    (firms,) = conn.execute(
        "SELECT COUNT(*) FROM firms WHERE snapshot_date = ?", (snapshot,)
    ).fetchone()
    if not 10_000 <= firms <= 30_000:
        problems.append(f"firm count {firms} outside expected 10,000-30,000")

    (named,) = conn.execute(
        "SELECT COUNT(*) FROM firms WHERE snapshot_date = ? AND primary_name IS NOT NULL",
        (snapshot,),
    ).fetchone()
    if firms and named / firms < 0.99:
        problems.append(f"only {named}/{firms} firms have a primary name")

    (with_raum,) = conn.execute(
        "SELECT COUNT(*) FROM firms WHERE snapshot_date = ? AND total_raum > 0",
        (snapshot,),
    ).fetchone()
    if firms and with_raum / firms < 0.70:
        problems.append(f"only {with_raum}/{firms} firms report positive RAUM")

    (flagged,) = conn.execute(
        "SELECT COUNT(*) FROM firms WHERE snapshot_date = ? AND disclosure_flag_count > 0",
        (snapshot,),
    ).fetchone()
    rate = flagged / firms if firms else 0
    if not 0.05 <= rate <= 0.25:
        problems.append(f"disclosure rate {rate:.1%} outside expected 5%-25%")

    (states,) = conn.execute(
        "SELECT COUNT(DISTINCT state) FROM firms WHERE snapshot_date = ? AND state IS NOT NULL",
        (snapshot,),
    ).fetchone()
    if states < 40:
        problems.append(f"only {states} distinct states present")

    print(
        f"  validation: {firms:,} firms | {with_raum:,} with RAUM | "
        f"{flagged:,} with a disclosure ({rate:.1%}) | {states} states"
    )
    if problems:
        raise QualityError("snapshot failed validation:\n  - " + "\n  - ".join(problems))
    print("  validation: PASS")


def main() -> int:
    today = date.today()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", nargs="?", type=Path, help="path to a bulk CSV")
    parser.add_argument("--year", type=int, default=today.year)
    parser.add_argument("--month", type=int, default=today.month)
    parser.add_argument("--db", type=Path, default=config.DB_PATH)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()

    csv_path = args.csv
    if csv_path is None:
        import fetch

        csv_path = fetch.fetch_month(args.year, args.month)

    try:
        ingest(csv_path, args.db, replace=args.replace)
    except QualityError as exc:
        print(f"\nQUALITY FAILURE\n{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
