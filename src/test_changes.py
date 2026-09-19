"""Tests for the change archive: content, determinism, rendering, and month runs.

Reuses the synthetic two-snapshot database from test_diff.
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import changes  # noqa: E402
import diff  # noqa: E402
import generate_site  # noqa: E402
from pipeline import consecutive_run  # noqa: E402
from test_diff import NEW, OLD, build  # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'ok  ' if ok else 'FAIL'}] {label}{'  -- ' + detail if detail and not ok else ''}")
    if not ok:
        failures.append(f"{label}: {detail}")


def main() -> int:
    conn = sqlite3.connect(":memory:")
    build(conn)
    diff.compute(conn, OLD, NEW)
    summary = changes.summarize(conn, OLD, NEW)

    print("-- summary content --")
    check("period is the newer month", summary["period"] == "2026-09", summary["period"])
    check("one registration (201)", [r["crd"] for r in summary["registered"]] == ["201"],
          str(summary["registered"]))
    check("one deregistration (200)", [r["crd"] for r in summary["deregistered"]] == ["200"],
          str(summary["deregistered"]))
    check("deregistered firm keeps its old name", summary["deregistered"][0]["name"] == "FIRM 200",
          str(summary["deregistered"]))
    flagged = {r["crd"]: r["codes"] for r in summary["newly_flagged"]}
    check("new disclosure flag on 100", flagged == {"100": ["11C(1)"]}, str(flagged))
    check("one disclosure cleared", summary["counts"]["cleared_flags"] == 1,
          str(summary["counts"]))
    check("only material RAUM moves, largest first",
          [m["crd"] for m in summary["movers"]] == ["102", "104"],
          str([m["crd"] for m in summary["movers"]]))
    check("rename counted as identity change",
          summary["identity"].get("primary_name") == 1, str(summary["identity"]))

    print("\n-- determinism --")
    again = changes.summarize(conn, OLD, NEW)
    check("summarize is repeatable", changes.dumps(summary) == changes.dumps(again))
    check("output has no timestamp-like field",
          "ingested_at" not in changes.dumps(summary) and "built_at" not in changes.dumps(summary))

    with tempfile.TemporaryDirectory() as tmp:
        path = changes.write(summary, Path(tmp))
        first = path.read_bytes()
        changes.write(summary, Path(tmp))
        check("rewriting produces identical bytes", path.read_bytes() == first)
        check("round-trips through load_all", changes.load_all(Path(tmp)) == [summary])

    print("\n-- rendering --")
    with tempfile.TemporaryDirectory() as tmp:
        site = generate_site.Site(out=Path(tmp), snapshot=NEW, previous=OLD)
        live = {
            crd: f"/firm/firm-{crd}-{crd}/"
            for crd in ("100", "102", "104", "201")  # 200 has deregistered: no page
        }
        html = generate_site.render_changes(site, summary, live)
        check("live firm is linked", "href='../../firm/firm-201-201/'" in html)
        check("deregistered firm is named", "Firm 200" in html)
        check("deregistered firm is not linked", "firm-200" not in html)
        check("reporting caveat present", "changes in what firms <strong>reported</strong>" in html)

    print("\n-- consecutive month runs --")
    available = {(2023, 4), (2023, 5), (2025, 12), (2026, 1), (2026, 2)}
    check("stops at a gap and crosses a year boundary",
          consecutive_run(available, (2026, 2)) == [(2025, 12), (2026, 1), (2026, 2)],
          str(consecutive_run(available, (2026, 2))))
    check("single month yields a single-item run",
          consecutive_run({(2026, 2)}, (2026, 2)) == [(2026, 2)])

    conn.close()
    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nall change-archive tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
