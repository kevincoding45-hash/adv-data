"""City name normalisation.

Firms type their own city into Form ADV, so one place arrives many ways:
"ST. LOUIS", "SAINT LOUIS", "ST LOUIS", "ST.LOUIS". Left alone this splits city
pages and the search authority that should accrue to one of them.

Design bias: under-merge rather than over-merge. Wrongly combining two real towns
puts firms on a page they do not belong on, which is worse than leaving a typo
on its own page. So there is no rule stripping generic suffixes like VILLAGE or
TOWN -- "Greenwood" and "Greenwood Village" stay distinct -- and the handful of
genuinely different names are listed explicitly in ALIASES.

The display name is not hand-maintained: it is whichever spelling the most firms
actually used, so "St. Louis" wins over "Saint Louis" on its own merits.
"""

from __future__ import annotations

import re
from collections import Counter

# Expanded only when they stand alone as a whole word.
ABBREVIATIONS = {
    "ST": "SAINT",
    "STE": "SAINTE",
    "FT": "FORT",
    "MT": "MOUNT",
    "N": "NORTH",
    "S": "SOUTH",
    "E": "EAST",
    "W": "WEST",
    "NO": "NORTH",
    "SO": "SOUTH",
}

# (state, fingerprint) -> fingerprint. Only for names that differ by more than
# punctuation or an abbreviation, evidenced by the data.
ALIASES: dict[tuple[str, str], str] = {
    ("NY", "NEWYORKCITY"): "NEWYORK",
    ("NY", "NYC"): "NEWYORK",
    ("DC", "WASHINGTONDC"): "WASHINGTON",
    ("OH", "WESTCHESTERTOWNSHIP"): "WESTCHESTER",
}

_PUNCT = re.compile(r"[^A-Z0-9]+")


def city_key(raw: str | None, state: str | None = None) -> str | None:
    """Fingerprint used to group spellings of the same city within one state."""
    if not raw or not raw.strip():
        return None
    words = _PUNCT.sub(" ", raw.upper()).split()
    if not words:
        return None
    expanded = [ABBREVIATIONS.get(word, word) for word in words]
    key = "".join(expanded)
    if state:
        key = ALIASES.get((state.upper(), key), key)
    return key or None


def canonical_names(rows: list[tuple[str, str, int]]) -> dict[tuple[str, str], str]:
    """Pick a display spelling per (state, key): the one most firms used.

    rows: (state, raw_city, firm_count)
    Ties break on the longer spelling, which is the unabbreviated one.
    """
    tally: dict[tuple[str, str], Counter] = {}
    for state, raw, n in rows:
        key = city_key(raw, state)
        if key is None:
            continue
        tally.setdefault((state, key), Counter())[raw.strip()] += n

    chosen: dict[tuple[str, str], str] = {}
    for group, counter in tally.items():
        chosen[group] = max(counter.items(), key=lambda kv: (kv[1], len(kv[0])))[0]
    return chosen
