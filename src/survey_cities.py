"""Survey how inconsistently firms spell their own city, to ground the alias rules.

Groups raw city strings within a state by a loose fingerprint and prints the
collisions, so normalisation rules are written against real data rather than
guesswork.
"""

from __future__ import annotations

import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402

STOPWORDS = {"CITY", "TOWN", "VILLAGE", "BOROUGH", "TWP", "TOWNSHIP"}
PREFIX = {
    "ST": "SAINT", "STE": "SAINTE", "FT": "FORT", "MT": "MOUNT",
    "N": "NORTH", "S": "SOUTH", "E": "EAST", "W": "WEST",
}


def fingerprint(city: str) -> str:
    """Loose key: punctuation and spacing removed, common abbreviations expanded."""
    words = re.sub(r"[^A-Z0-9 ]+", " ", city.upper()).split()
    expanded = [PREFIX.get(w, w) for w in words]
    kept = [w for w in expanded if w not in STOPWORDS] or expanded
    return "".join(kept)


def main() -> int:
    conn = sqlite3.connect(config.DB_PATH)
    snapshot = conn.execute("SELECT MAX(snapshot_date) FROM snapshots").fetchone()[0]

    groups: dict[tuple[str, str], dict[str, int]] = defaultdict(dict)
    for state, city, n in conn.execute(
        """
        SELECT state, city, COUNT(*) FROM firms
        WHERE snapshot_date = ? AND city IS NOT NULL AND state IS NOT NULL
        GROUP BY state, city
        """,
        (snapshot,),
    ):
        groups[(state, fingerprint(city))][city] = n

    collisions = {k: v for k, v in groups.items() if len(v) > 1}
    merged_firms = sum(sum(v.values()) for v in collisions.values())

    print(f"snapshot {snapshot}")
    print(f"distinct (state, city) pairs : {sum(len(v) for v in groups.values()):,}")
    print(f"distinct after fingerprinting: {len(groups):,}")
    print(f"colliding groups             : {len(collisions):,}")
    print(f"firms in colliding groups    : {merged_firms:,}\n")

    ranked = sorted(collisions.items(), key=lambda kv: -sum(kv[1].values()))
    print("--- 30 largest collisions ---")
    for (state, _), variants in ranked[:30]:
        parts = ", ".join(
            f"{city} ({n})" for city, n in sorted(variants.items(), key=lambda kv: -kv[1])
        )
        print(f"  {state}: {parts}")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
