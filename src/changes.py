"""Monthly change summaries, archived as JSON in the repository.

Each rebuild starts from scratch using only the two newest snapshots, so anything
derived from older months would vanish at the next rebuild -- including
/changes/<month>/ pages that search engines have already indexed. Recomputing
every past month instead would cost ~200 MB of database per snapshot and grow
without bound.

So each month's summary is written to archive/changes/<YYYY-MM>.json and
committed. Rebuilds render every archived month, so change pages accumulate.

Files are deterministic -- fully sorted, no timestamps -- so re-running a month
produces byte-identical output, and CI only commits when the SEC publishes
something new.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
import field_dictionary as fd  # noqa: E402

ARCHIVE = config.ROOT / "archive" / "changes"
MOVERS_LIMIT = 40

# Dictionary order is the form's own order, which reads naturally on a page.
_CODE_ORDER = {code: i for i, code in enumerate(fd.FIELDS)}


def _firm(crd, name, city, city_key, state) -> dict:
    return {"crd": crd, "name": name, "city": city, "city_key": city_key, "state": state}


def summarize(conn: sqlite3.Connection, previous: str, snapshot: str) -> dict:
    """Everything a change page needs, from the diffs and firms tables."""
    pair = (previous, snapshot)

    registered = [
        {**_firm(crd, name, city, key, state), "raum": raum}
        for crd, name, city, key, state, raum in conn.execute(
            """
            SELECT d.crd, f.primary_name, f.city, f.city_key, f.state, f.total_raum
            FROM diffs d JOIN firms f ON f.crd = d.crd AND f.snapshot_date = d.to_snapshot
            WHERE d.from_snapshot = ? AND d.to_snapshot = ? AND d.change_type = 'registered'
            """,
            pair,
        )
    ]
    registered.sort(key=lambda r: (-(r["raum"] or 0), r["crd"]))

    deregistered = [
        _firm(crd, name, city, key, state)
        for crd, name, city, key, state in conn.execute(
            """
            SELECT d.crd, COALESCE(f.primary_name, d.new_text), f.city, f.city_key, f.state
            FROM diffs d LEFT JOIN firms f ON f.crd = d.crd AND f.snapshot_date = d.from_snapshot
            WHERE d.from_snapshot = ? AND d.to_snapshot = ? AND d.change_type = 'deregistered'
            """,
            pair,
        )
    ]
    deregistered.sort(key=lambda r: ((r["name"] or "").upper(), r["crd"]))

    flagged: dict[str, dict] = {}
    for crd, name, city, key, state, code in conn.execute(
        """
        SELECT d.crd, f.primary_name, f.city, f.city_key, f.state, d.code
        FROM diffs d JOIN firms f ON f.crd = d.crd AND f.snapshot_date = d.to_snapshot
        WHERE d.from_snapshot = ? AND d.to_snapshot = ? AND d.change_type = 'field'
          AND d.code LIKE '11%' AND IFNULL(d.old_num, 0) = 0 AND d.new_num = 1
        """,
        pair,
    ):
        entry = flagged.setdefault(crd, {**_firm(crd, name, city, key, state), "codes": []})
        entry["codes"].append(code)
    newly_flagged = sorted(flagged.values(), key=lambda r: ((r["name"] or "").upper(), r["crd"]))
    for entry in newly_flagged:
        entry["codes"].sort(key=lambda c: _CODE_ORDER.get(c, 10_000))

    (cleared,) = conn.execute(
        """
        SELECT COUNT(*) FROM diffs
        WHERE from_snapshot = ? AND to_snapshot = ? AND change_type = 'field'
          AND code LIKE '11%' AND old_num = 1 AND IFNULL(new_num, 0) = 0
        """,
        pair,
    ).fetchone()

    movers = [
        {**_firm(crd, name, None, None, state), "old": old, "new": new, "delta": delta, "pct": pct}
        for crd, name, state, old, new, delta, pct in conn.execute(
            """
            SELECT f.crd, f.primary_name, f.state, d.old_num, d.new_num, d.delta, d.pct_change
            FROM diffs d JOIN firms f ON f.crd = d.crd AND f.snapshot_date = d.to_snapshot
            WHERE d.from_snapshot = ? AND d.to_snapshot = ? AND d.code = '5F(2)(c)'
              AND d.old_num IS NOT NULL AND d.new_num IS NOT NULL
            """,
            pair,
        )
    ]
    movers.sort(key=lambda r: (-abs(r["delta"]), r["crd"]))
    movers = movers[:MOVERS_LIMIT]

    identity = {
        code: n
        for code, n in conn.execute(
            """
            SELECT code, COUNT(*) FROM diffs
            WHERE from_snapshot = ? AND to_snapshot = ? AND change_type = 'identity'
            GROUP BY code ORDER BY code
            """,
            pair,
        )
    }

    return {
        "period": snapshot[:7],
        "from": previous,
        "to": snapshot,
        "counts": {
            "registered": len(registered),
            "deregistered": len(deregistered),
            "newly_flagged_firms": len(newly_flagged),
            "cleared_flags": cleared,
            "movers": len(movers),
        },
        "registered": registered,
        "deregistered": deregistered,
        "newly_flagged": newly_flagged,
        "movers": movers,
        "identity": identity,
    }


def dumps(summary: dict) -> str:
    return json.dumps(summary, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def write(summary: dict, archive: Path = ARCHIVE) -> Path:
    archive.mkdir(parents=True, exist_ok=True)
    path = archive / f"{summary['period']}.json"
    path.write_text(dumps(summary), encoding="utf-8")
    return path


def load_all(archive: Path = ARCHIVE) -> list[dict]:
    """Every archived month, oldest first."""
    if not archive.exists():
        return []
    return [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted(archive.glob("*.json"))
    ]
