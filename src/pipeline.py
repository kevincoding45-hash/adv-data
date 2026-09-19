"""Run the whole pipeline: discover -> fetch -> ingest -> diff -> generate.

This is what CI calls, so the workflow file stays a thin wrapper and the same
command reproduces a build locally.

The pipeline is stateless. It asks the SEC what the newest month is, fetches that
month and the one before it, and rebuilds from scratch. Nothing needs to persist
between runs, so a failed run never leaves partial state behind.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
import diff  # noqa: E402
import fetch  # noqa: E402
import generate_site  # noqa: E402
import ingest  # noqa: E402

STATUS = config.ROOT / "status" / "latest.json"


def previous_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def latest_available(index: dict[tuple[int, int, str], str]) -> tuple[int, int]:
    months = sorted(k[:2] for k in index if k[2] == "registered")
    if not months:
        raise SystemExit("the SEC index listed no registered-adviser files")
    return months[-1]


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

    snapshots = []
    for path in csv_paths:
        snapshots.append(ingest.ingest(path, db))

    counts: dict[str, int] = {}
    if len(snapshots) >= 2:
        conn = sqlite3.connect(db)
        counts = diff.compute(conn, snapshots[-2], snapshots[-1])
        print(
            f"diff {snapshots[-2]} -> {snapshots[-1]}: "
            f"{counts['registered']:,} registered, {counts['deregistered']:,} deregistered, "
            f"{counts['identity']:,} identity, {counts['field']:,} field"
        )
        conn.close()
    else:
        print("only one snapshot available; skipping diff")

    built = generate_site.generate(db, out)
    write_status(built, counts)
    print("pipeline complete")
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


def main() -> int:
    today = date.today()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=config.DB_PATH)
    parser.add_argument("--out", type=Path, default=config.ROOT / "site")
    parser.add_argument("--rebuild", action="store_true", help="discard any existing database first")
    args = parser.parse_args()

    try:
        return run(db=args.db, out=args.out, rebuild=args.rebuild)
    except config.ConfigError as exc:
        print(f"\nconfiguration error:\n{exc}", file=sys.stderr)
        return 2
    except ingest.QualityError as exc:
        print(f"\nQUALITY FAILURE -- refusing to publish\n{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
