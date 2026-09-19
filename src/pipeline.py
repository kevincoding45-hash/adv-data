"""Run the whole pipeline: discover -> fetch -> ingest -> diff -> archive -> generate.

This is what CI calls, so the workflow file stays a thin wrapper and the same
command reproduces a build locally.

Each run asks the SEC what the newest month is, fetches that month and the one
before it, and rebuilds the database from scratch. The one piece of state that
must outlive a run is the month-over-month change history: it is written to
archive/changes/<YYYY-MM>.json (see changes.py) and committed, so change pages
accumulate instead of being replaced each month.

--backfill-from YYYY-MM rebuilds that archive for every consecutive month from
the given start to the newest, holding at most two months of field data at once.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import changes  # noqa: E402
import config  # noqa: E402
import diff  # noqa: E402
import fetch  # noqa: E402
import generate_site  # noqa: E402
import ingest  # noqa: E402

STATUS = config.ROOT / "status" / "latest.json"

Month = tuple[int, int]


def previous_month(year: int, month: int) -> Month:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def latest_available(index: dict[tuple[int, int, str], str]) -> Month:
    months = sorted(k[:2] for k in index if k[2] == "registered")
    if not months:
        raise SystemExit("the SEC index listed no registered-adviser files")
    return months[-1]


def consecutive_run(available: set[Month], latest: Month) -> list[Month]:
    """Months ending at `latest` with no calendar gap, oldest first.

    Only consecutive months are compared: diffing across a missing month would
    present two months of change as one.
    """
    run = [latest]
    while previous_month(*run[-1]) in available:
        run.append(previous_month(*run[-1]))
    return run[::-1]


def diff_and_archive(conn: sqlite3.Connection, previous: str, snapshot: str) -> dict[str, int]:
    counts = diff.compute(conn, previous, snapshot)
    path = changes.write(changes.summarize(conn, previous, snapshot))
    print(
        f"diff {previous} -> {snapshot}: "
        f"{counts['registered']:,} registered, {counts['deregistered']:,} deregistered, "
        f"{counts['identity']:,} identity, {counts['field']:,} field "
        f"-> {path.relative_to(config.ROOT)}"
    )
    return counts


def run(*, db: Path, out: Path, rebuild: bool = False) -> int:
    print("discovering available snapshots...")
    index = fetch.list_available()
    year, month = latest_available(index)
    prev_year, prev_month = previous_month(year, month)
    print(f"latest published month: {year}-{month:02d}")

    if rebuild and db.exists():
        db.unlink()

    csv_paths = []
    for y, m in ((prev_year, prev_month), (year, month)):
        if (y, m, "registered") not in index:
            print(f"  {y}-{m:02d} not published; skipping")
            continue
        csv_paths.append(fetch.fetch_month(y, m, index=index))

    if not csv_paths:
        raise SystemExit("no snapshots could be fetched")

    snapshots = [ingest.ingest(path, db) for path in csv_paths]

    counts: dict[str, int] = {}
    if len(snapshots) >= 2:
        conn = sqlite3.connect(db)
        counts = diff_and_archive(conn, snapshots[-2], snapshots[-1])
        conn.close()
    else:
        print("only one snapshot available; skipping diff")

    built = generate_site.generate(db, out)
    write_status(built, counts)
    print("pipeline complete")
    return 0


def backfill(start: Month, *, db: Path, out: Path) -> int:
    print("discovering available snapshots...")
    index = fetch.list_available()
    available = {k[:2] for k in index if k[2] == "registered"}
    months = [m for m in consecutive_run(available, latest_available(index)) if m >= start]
    if len(months) < 2:
        raise SystemExit(
            f"need at least two consecutive months from {start[0]}-{start[1]:02d}; "
            f"the unbroken run available ends at {months or 'nothing'}"
        )
    print(f"backfilling {months[0][0]}-{months[0][1]:02d} .. {months[-1][0]}-{months[-1][1]:02d} "
          f"({len(months) - 1} change pages)")

    if db.exists():
        db.unlink()

    previous: str | None = None
    counts: dict[str, int] = {}
    for y, m in months:
        snapshot = ingest.ingest(fetch.fetch_month(y, m, index=index), db)
        if previous:
            conn = sqlite3.connect(db)
            counts = diff_and_archive(conn, previous, snapshot)
            # The older month's field values are no longer needed; dropping them
            # keeps the database at two months regardless of how far back we go.
            conn.execute("DELETE FROM firm_fields WHERE snapshot_date = ?", (previous,))
            conn.commit()
            conn.close()
        previous = snapshot

    built = generate_site.generate(db, out)
    write_status(built, counts)
    print("backfill complete")
    return 0


def write_status(built: dict, counts: dict[str, int]) -> None:
    """Record what was built.

    Deliberately contains no timestamp: the file changes only when the SEC
    publishes a new month, so CI commits it about once a month. That commit is
    what keeps GitHub from disabling the scheduled workflow, which it does to
    public repos after 60 days without repository activity.
    """
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    payload = {**built, "changes": counts}
    STATUS.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"status -> {STATUS.relative_to(config.ROOT)}")


def parse_month(text: str) -> Month:
    year, month = text.split("-")
    return int(year), int(month)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=config.DB_PATH)
    parser.add_argument("--out", type=Path, default=config.ROOT / "site")
    parser.add_argument("--rebuild", action="store_true", help="discard any existing database first")
    parser.add_argument(
        "--backfill-from",
        type=parse_month,
        metavar="YYYY-MM",
        help="rebuild the change archive for every consecutive month from here to the newest",
    )
    args = parser.parse_args()

    try:
        if args.backfill_from:
            return backfill(args.backfill_from, db=args.db, out=args.out)
        return run(db=args.db, out=args.out, rebuild=args.rebuild)
    except config.ConfigError as exc:
        print(f"\nconfiguration error:\n{exc}", file=sys.stderr)
        return 2
    except ingest.QualityError as exc:
        print(f"\nQUALITY FAILURE -- refusing to publish\n{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
