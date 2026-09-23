"""Generate the static site from an ingested snapshot.

Standard library only -- no template engine, no build step, so the same command
runs locally and in GitHub Actions.

Page types:
    /                        overview and latest changes
    /firm/<slug>-<crd>/      one per registered adviser
    /state/<st>/             one per state, links every firm in it
    /city/<st>/<city>/       one per city with 5 or more firms
    /changes/<YYYY-MM>/      what moved between two snapshots
    /disclosures/            firms reporting disciplinary events
    /about/                  sources, method, corrections

Editorial rules enforced here, not left to judgement:
  - every page states its snapshot date and links to the SEC's own record
  - disclosure flags are presented as reported facts, never as conclusions
  - no field with verified=False or publish=False is ever rendered
"""

from __future__ import annotations

import argparse
import html
import itertools
import os
import re
import shutil
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import changes  # noqa: E402
import city as city_mod  # noqa: E402
import config  # noqa: E402
import field_dictionary as fd  # noqa: E402

SITE_NAME = "Adviser Record"
TAGLINE = "Public SEC data on registered investment advisers"
# Canonical origin for sitemap and <link rel="canonical">. SITE_BASE_URL (a GitHub
# Actions repository variable) overrides it, e.g. to preview on a pages.dev address.
# `or`, not a get() default: an unset Actions variable arrives as an empty string.
BASE_URL = (os.environ.get("SITE_BASE_URL") or "https://adviserrecord.com").rstrip("/")
IAPD_FIRM = "https://adviserinfo.sec.gov/firm/summary/{crd}"

# Shown on the privacy page. A constant rather than the build date, so rebuilds
# stay byte-identical and the date means "when the policy last changed".
PRIVACY_UPDATED = "2026-09-23"
CONTACT_EMAIL = "privacy@adviserrecord.com"

ASSETS = Path(__file__).resolve().parent / "assets"

# Field groups rendered on a firm page, in order.
FIRM_SECTIONS: list[tuple[str, list[str]]] = [
    ("Assets and accounts", ["5F(1)", "5F(2)(a)", "5F(2)(b)", "5F(2)(c)", "5F(2)(d)", "5F(2)(e)", "5F(2)(f)", "5F(3)"]),
    ("People", ["5A", "5B(1)", "5B(2)", "5B(3)", "5B(4)", "5B(5)", "5B(6)"]),
    ("Custody", ["9A(1)(a)", "9A(1)(b)", "9A(2)(a)", "9A(2)(b)", "9B(1)(a)", "9B(1)(b)", "9B(2)(a)", "9B(2)(b)", "9D(1)", "9D(2)", "9F"]),
]

CHECKBOX_GROUPS: list[tuple[str, str, dict[str, str]]] = [
    ("How the firm is compensated", "5E({})", fd.COMPENSATION),
    ("Advisory services offered", "5G({})", fd.ADVISORY_SERVICES),
    ("Other business activities", "6A({})", fd.OTHER_BUSINESS),
    ("Affiliated related persons", "7A({})", fd.RELATED_PERSONS),
]

CONFLICT_CODES = [
    "8A(1)", "8A(2)", "8A(3)", "8B(1)", "8B(2)", "8B(3)",
    "8D", "8E", "8F", "8G(1)", "8H(1)", "8H(2)", "8I",
]

DISCLOSURE_CODES = [c for c in fd.FIELDS if c.startswith("11") and c != "11"]


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def esc(value) -> str:
    return html.escape(str(value), quote=True) if value is not None else ""


def slugify(value: str, limit: int = 60) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return (text[:limit].rstrip("-")) or "unnamed"


def money(value) -> str:
    if value is None:
        return "Not reported"
    value = float(value)
    if value >= 1e12:
        return f"${value / 1e12:,.2f} trillion"
    if value >= 1e9:
        return f"${value / 1e9:,.2f} billion"
    if value >= 1e6:
        return f"${value / 1e6:,.1f} million"
    return f"${value:,.0f}"


def count(value) -> str:
    return "Not reported" if value is None else f"{int(value):,}"


# Firm names arrive from the SEC in all caps. These stay uppercase; everything
# else is title-cased, and dotted initials ("j.p.") are uppercased as a group.
ALWAYS_UPPER = {"LLC", "L.L.C.", "LP", "L.P.", "LLP", "PLC", "USA", "US", "U.S.", "NA", "N.A.", "ETF", "REIT"}
LOWER_WORDS = {"of", "and", "the", "for", "at", "in", "on", "a", "an", "de", "du"}
INITIALS = re.compile(r"(?:[a-z]\.){2,}$")


def title_case(value: str | None) -> str:
    if not value:
        return ""
    words = []
    for i, word in enumerate(value.lower().split()):
        if INITIALS.match(word):
            words.append(word.upper())
            continue
        core = word.strip(",.;:()&'\"")
        if not core:
            words.append(word)
            continue
        start = word.index(core)
        leading, trailing = word[:start], word[start + len(core):]
        if core.upper() in ALWAYS_UPPER:
            rendered = core.upper()
        elif i > 0 and core in LOWER_WORDS:
            rendered = core
        else:
            rendered = core.capitalize()
        words.append(leading + rendered + trailing)
    return " ".join(words)


@dataclass
class Site:
    out: Path
    snapshot: str
    previous: str | None
    # (state, city_key) -> the spelling most firms used, e.g. ("MO","SAINTLOUIS") -> "ST. LOUIS"
    city_labels: dict[tuple[str, str], str] = field(default_factory=dict)

    def city_label(self, state: str | None, city_key: str | None, fallback: str | None = None) -> str:
        if state and city_key:
            raw = self.city_labels.get((state, city_key))
            if raw:
                return title_case(raw)
        return title_case(fallback)

    def write(self, rel: str, content: str) -> None:
        path = self.out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def layout(
    site: Site,
    *,
    title: str,
    description: str,
    canonical: str,
    body: str,
    depth: int,
    jsonld: str = "",
    root: str | None = None,
) -> str:
    # The 404 page is served at any depth, so it passes root="/" to force
    # absolute asset and navigation links.
    root = root if root is not None else ("../" * depth if depth else "")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
{f'<link rel="canonical" href="{esc(BASE_URL + canonical)}">' if canonical else ""}
<link rel="stylesheet" href="{root}style.css">
{jsonld}
</head>
<body>
<header class="site"><div class="wrap">
  <a class="brand" href="{root or '/'}">{esc(SITE_NAME)}</a>
  <nav>
    <a href="{root}states/">States</a>
    <a href="{root}disclosures/">Disclosures</a>
    <a href="{root}changes/">Monthly changes</a>
    <a href="{root}about/">About the data</a>
  </nav>
</div></header>
<main><div class="wrap">
{body}
</div></main>
<footer class="site"><div class="wrap">
  <p>Built from the U.S. Securities and Exchange Commission's Form ADV bulk data,
     snapshot {esc(site.snapshot)}. Source data is public information.</p>
  <p>This site reports what firms disclosed on Form ADV. It does not evaluate,
     rate, or recommend any adviser, and nothing here is investment advice.
     A disclosure is not a finding of wrongdoing.</p>
  <p><a href="{root}about/">Method and corrections</a> &middot; <a href="{root}privacy/">Privacy</a></p>
</div></footer>
</body>
</html>
"""


def yes_no_rows(values: dict[str, float | None], codes: list[str]) -> str:
    rows = []
    for code in codes:
        field = fd.FIELDS.get(code)
        if field is None or not field.publish:
            continue
        raw = values.get(code)
        if raw is None:
            continue
        if field.dtype == "yn":
            shown = "Yes" if raw == 1 else "No"
        elif field.dtype == "money":
            shown = money(raw)
        elif field.dtype in {"int", "pct"}:
            shown = count(raw) if field.dtype == "int" else f"{raw:g}%"
        else:
            continue
        rows.append(f"<tr><td>{esc(field.label)}</td><td class='num'>{esc(shown)}</td></tr>")
    if not rows:
        return ""
    return "<table><tbody>" + "".join(rows) + "</tbody></table>"


# --------------------------------------------------------------------------
# firm pages
# --------------------------------------------------------------------------
def firm_url(crd: str, name: str) -> str:
    return f"/firm/{slugify(name)}-{crd}/"


def render_firm(site: Site, firm: sqlite3.Row, values: dict[str, float | None]) -> str:
    name = title_case(firm["primary_name"]) or f"CRD {firm['crd']}"
    city = site.city_label(firm["state"], firm["city_key"], firm["city"])
    where = ", ".join(p for p in (city, firm["state"]) if p)
    crd = firm["crd"]

    flags = [c for c in DISCLOSURE_CODES if values.get(c) == 1]
    events = firm["disclosure_event_count"] or 0

    head = [
        f"<h1>{esc(name)}</h1>",
        f"<p class='sub'>Registered investment adviser{' &middot; ' + esc(where) if where else ''} &middot; CRD #{esc(crd)}</p>",
        f"<span class='asof'>Form ADV data as of {esc(site.snapshot)}</span>",
    ]

    stats = [
        ("Regulatory assets", money(firm["total_raum"])),
        ("Total accounts", count(firm["total_accounts"])),
        ("Employees", count(firm["total_employees"])),
        ("Disclosure events", f"{events:,}"),
    ]
    head.append(
        "<div class='stats'>"
        + "".join(
            f"<div class='stat'><div class='label'>{esc(l)}</div><div class='value'>{esc(v)}</div></div>"
            for l, v in stats
        )
        + "</div>"
    )

    contact = []
    if firm["website"]:
        url = firm["website"].strip()
        href = url if url.lower().startswith("http") else f"https://{url}"
        contact.append(f"<li>Website: <a href='{esc(href)}' rel='nofollow noopener'>{esc(url.lower())}</a></li>")
    if firm["phone"]:
        contact.append(f"<li>Telephone: {esc(firm['phone'])}</li>")
    if firm["sec_number"]:
        contact.append(f"<li>SEC file number: {esc(firm['sec_number'])}</li>")
    if firm["latest_filing_date"]:
        contact.append(f"<li>Most recent Form ADV filing: {esc(firm['latest_filing_date'])}</li>")
    contact.append(
        f"<li>SEC record: <a href='{IAPD_FIRM.format(crd=esc(crd))}' rel='nofollow noopener'>"
        f"View this firm on the SEC's IAPD site</a></li>"
    )
    body = ["".join(head), "<h2>Firm details</h2>", "<ul class='plain'>" + "".join(contact) + "</ul>"]

    # Disclosures first -- it is the reason most visitors arrive.
    body.append("<h2>Disciplinary disclosures</h2>")
    if flags:
        body.append(
            "<div class='notice'><strong>This firm reports "
            f"{len(flags)} disclosure {'question' if len(flags) == 1 else 'questions'} answered Yes</strong>"
            f"{f', covering {events:,} reported events' if events else ''}. "
            "Form ADV Item 11 covers charges and pending proceedings as well as findings, "
            "so a Yes answer is not itself a finding of wrongdoing. "
            "The underlying detail is filed with the SEC.</div>"
        )
        body.append("<ul class='plain'>" + "".join(
            f"<li>{esc(fd.FIELDS[c].label)}</li>" for c in flags
        ) + "</ul>")
    else:
        body.append(
            "<p class='muted'>This firm answered No to every Item 11 disciplinary "
            "question on its most recent Form ADV filing.</p>"
        )

    for heading, codes in FIRM_SECTIONS:
        table = yes_no_rows(values, codes)
        if table:
            body.append(f"<h2>{esc(heading)}</h2>{table}")

    for heading, pattern, options in CHECKBOX_GROUPS:
        checked = [
            label for key, label in options.items() if values.get(pattern.format(key)) == 1
        ]
        if checked:
            body.append(f"<h2>{esc(heading)}</h2><div class='tags'>" + "".join(
                f"<span class='tag'>{esc(c)}</span>" for c in checked
            ) + "</div>")

    conflicts = [
        fd.FIELDS[c].label for c in CONFLICT_CODES if values.get(c) == 1 and c in fd.FIELDS
    ]
    if conflicts:
        body.append(
            "<h2>Reported conflicts of interest</h2>"
            "<p class='sub'>Form ADV Items 8 asks advisers to disclose arrangements that "
            "may create a conflict with client interests. This firm answered Yes to:</p>"
            "<ul class='plain'>" + "".join(f"<li>{esc(c)}</li>" for c in conflicts) + "</ul>"
        )

    clients = []
    for letter, label in fd.CLIENT_TYPES.items():
        n = values.get(f"5D({letter})(1)")
        raum = values.get(f"5D({letter})(3)")
        if n or raum:
            clients.append(
                f"<tr><td>{esc(label)}</td><td class='num'>{count(n)}</td>"
                f"<td class='num'>{esc(money(raum))}</td></tr>"
            )
    if clients:
        body.append(
            "<h2>Client types</h2><table><thead><tr><th>Type of client</th>"
            "<th class='num'>Clients</th><th class='num'>Assets</th></tr></thead><tbody>"
            + "".join(clients) + "</tbody></table>"
        )

    jsonld = (
        '<script type="application/ld+json">'
        f'{{"@context":"https://schema.org","@type":"FinancialService",'
        f'"name":{_json_str(name)},"identifier":{_json_str("CRD " + crd)},'
        f'"url":{_json_str(BASE_URL + firm_url(crd, firm["primary_name"] or crd))}'
        + (f',"address":{{"@type":"PostalAddress","addressLocality":{_json_str(city)},'
           f'"addressRegion":{_json_str(firm["state"])}}}' if city and firm["state"] else "")
        + "}</script>"
    )

    return layout(
        site,
        title=f"{name} - Form ADV record, CRD {crd}",
        description=(
            f"SEC Form ADV data for {name}"
            + (f" of {where}" if where else "")
            + f": {money(firm['total_raum'])} in regulatory assets, "
            f"{len(flags)} disciplinary disclosure questions answered Yes."
        ),
        canonical=firm_url(crd, firm["primary_name"] or crd),
        body="\n".join(body),
        depth=3,
        jsonld=jsonld,
    )


def _json_str(value) -> str:
    import json

    return json.dumps("" if value is None else str(value))


# --------------------------------------------------------------------------
# generation
# --------------------------------------------------------------------------
def generate(db: Path, out: Path, *, limit: int | None = None) -> dict:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row

    snapshots = [r[0] for r in conn.execute("SELECT snapshot_date FROM snapshots ORDER BY snapshot_date")]
    if not snapshots:
        raise SystemExit("no snapshots ingested")
    snapshot = snapshots[-1]
    previous = snapshots[-2] if len(snapshots) > 1 else None
    site = Site(out=out, snapshot=snapshot, previous=previous)
    site.city_labels = city_mod.canonical_names(
        conn.execute(
            """
            SELECT state, city, COUNT(*) FROM firms
            WHERE snapshot_date = ? AND city IS NOT NULL AND state IS NOT NULL
            GROUP BY state, city
            """,
            (snapshot,),
        ).fetchall()
    )

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    shutil.copy(ASSETS / "style.css", out / "style.css")

    firms = {
        row["crd"]: row
        for row in conn.execute(
            "SELECT * FROM firms WHERE snapshot_date = ? ORDER BY crd", (snapshot,)
        )
    }
    print(f"snapshot {snapshot}: {len(firms):,} firms")

    urls: list[str] = ["/", "/states/", "/disclosures/", "/changes/", "/about/", "/privacy/"]
    live: dict[str, str] = {}  # crd -> firm page URL, for pages that link to firms
    written = 0

    # Stream field values grouped by firm so memory stays flat.
    cursor = conn.execute(
        "SELECT crd, code, num_value, text_value FROM firm_fields "
        "WHERE snapshot_date = ? ORDER BY crd",
        (snapshot,),
    )
    for crd, group in itertools.groupby(cursor, key=lambda r: r["crd"]):
        firm = firms.get(crd)
        values = {r["code"]: r["num_value"] for r in group}
        if firm is None:
            continue
        url = firm_url(crd, firm["primary_name"] or crd)
        site.write(url.strip("/") + "/index.html", render_firm(site, firm, values))
        urls.append(url)
        live[crd] = url
        written += 1
        if limit and written >= limit:
            break
        if written % 2500 == 0:
            print(f"  {written:,} firm pages")
    print(f"  {written:,} firm pages")

    urls += write_geography(site, conn, snapshot)
    urls += write_disclosures(site, conn, snapshot)
    change_pages = write_changes(site, live)
    urls += list(change_pages)
    write_home(site, conn, snapshot, previous)
    write_about(site)
    write_privacy(site)
    write_404(site)
    write_sitemap(site, urls, lastmod=change_pages)
    write_headers(site)

    print(f"  {len(urls):,} urls total -> {out}")
    conn.close()
    return {"snapshot": snapshot, "previous": previous, "firms": len(firms), "pages": len(set(urls))}


def write_geography(site: Site, conn: sqlite3.Connection, snapshot: str) -> list[str]:
    urls = []
    states = conn.execute(
        """
        SELECT state, COUNT(*) n, SUM(total_raum) raum,
               SUM(CASE WHEN disclosure_flag_count > 0 THEN 1 ELSE 0 END) flagged
        FROM firms WHERE snapshot_date = ? AND state IS NOT NULL
        GROUP BY state ORDER BY n DESC
        """,
        (snapshot,),
    ).fetchall()

    rows = "".join(
        f"<tr><td><a href='../state/{esc(s['state'].lower())}/'>{esc(s['state'])}</a></td>"
        f"<td class='num'>{s['n']:,}</td><td class='num'>{esc(money(s['raum']))}</td>"
        f"<td class='num'>{s['flagged']:,}</td></tr>"
        for s in states
    )
    site.write(
        "states/index.html",
        layout(
            site,
            title=f"Registered investment advisers by state - {SITE_NAME}",
            description="Count of SEC-registered investment advisers, assets under management, and disclosure counts for every U.S. state and territory.",
            canonical="/states/",
            depth=1,
            body=(
                "<h1>Advisers by state</h1>"
                f"<p class='sub'>{len(states)} states and territories.</p>"
                f"<span class='asof'>Form ADV data as of {esc(snapshot)}</span>"
                "<table><thead><tr><th>State</th><th class='num'>Firms</th>"
                "<th class='num'>Regulatory assets</th><th class='num'>With disclosures</th>"
                "</tr></thead><tbody>" + rows + "</tbody></table>"
            ),
        ),
    )

    for state in states:
        code = state["state"]
        cities = conn.execute(
            """
            SELECT city_key, COUNT(*) n FROM firms
            WHERE snapshot_date = ? AND state = ? AND city_key IS NOT NULL
            GROUP BY city_key HAVING COUNT(*) >= 5 ORDER BY n DESC
            """,
            (snapshot, code),
        ).fetchall()
        members = conn.execute(
            """
            SELECT crd, primary_name, city, city_key, total_raum, disclosure_flag_count
            FROM firms WHERE snapshot_date = ? AND state = ?
            ORDER BY primary_name
            """,
            (snapshot, code),
        ).fetchall()

        city_links = "".join(
            f"<a class='tag' href='../../city/{esc(code.lower())}/"
            f"{esc(slugify(site.city_label(code, c['city_key'])))}/'>"
            f"{esc(site.city_label(code, c['city_key']))} ({c['n']})</a>"
            for c in cities
        )
        body = [
            f"<h1>Investment advisers in {esc(code)}</h1>",
            f"<p class='sub'>{state['n']:,} SEC-registered firms &middot; "
            f"{money(state['raum'])} in regulatory assets &middot; "
            f"{state['flagged']:,} reporting a disciplinary disclosure.</p>",
            f"<span class='asof'>Form ADV data as of {esc(snapshot)}</span>",
        ]
        if city_links:
            body.append(f"<h2>Cities</h2><div class='tags'>{city_links}</div>")
        body.append(
            "<h2>All firms</h2><table><thead><tr><th>Firm</th><th>City</th>"
            "<th class='num'>Assets</th><th class='num'>Disclosures</th></tr></thead><tbody>"
            + "".join(
                f"<tr><td><a href='../../firm/{esc(slugify(m['primary_name'] or m['crd']))}-{esc(m['crd'])}/'>"
                f"{esc(title_case(m['primary_name']) or m['crd'])}</a></td>"
                f"<td>{esc(site.city_label(code, m['city_key'], m['city']))}</td>"
                f"<td class='num'>{esc(money(m['total_raum']))}</td>"
                f"<td class='num'>{m['disclosure_flag_count'] or 0}</td></tr>"
                for m in members
            )
            + "</tbody></table>"
        )
        site.write(
            f"state/{code.lower()}/index.html",
            layout(
                site,
                title=f"SEC-registered investment advisers in {code} - {SITE_NAME}",
                description=f"All {state['n']:,} SEC-registered investment advisers with a main office in {code}, with assets under management and disclosure counts from Form ADV.",
                canonical=f"/state/{code.lower()}/",
                depth=2,
                body="\n".join(body),
            ),
        )
        urls.append(f"/state/{code.lower()}/")

        for city in cities:
            members = conn.execute(
                """
                SELECT crd, primary_name, total_raum, total_employees, disclosure_flag_count
                FROM firms WHERE snapshot_date = ? AND state = ? AND city_key = ?
                ORDER BY total_raum DESC
                """,
                (snapshot, code, city["city_key"]),
            ).fetchall()
            pretty = site.city_label(code, city["city_key"])
            city_slug = slugify(pretty)
            site.write(
                f"city/{code.lower()}/{city_slug}/index.html",
                layout(
                    site,
                    title=f"Investment advisers in {pretty}, {code} - {SITE_NAME}",
                    description=f"{city['n']} SEC-registered investment advisers with a main office in {pretty}, {code}, ranked by regulatory assets under management.",
                    canonical=f"/city/{code.lower()}/{city_slug}/",
                    depth=3,
                    body=(
                        f"<h1>Investment advisers in {esc(pretty)}, {esc(code)}</h1>"
                        f"<p class='sub'>{city['n']} SEC-registered firms with a main office here.</p>"
                        f"<span class='asof'>Form ADV data as of {esc(snapshot)}</span>"
                        f"<p><a href='../../../state/{esc(code.lower())}/'>All advisers in {esc(code)}</a></p>"
                        "<table><thead><tr><th>Firm</th><th class='num'>Assets</th>"
                        "<th class='num'>Employees</th><th class='num'>Disclosures</th></tr></thead><tbody>"
                        + "".join(
                            f"<tr><td><a href='../../../firm/{esc(slugify(m['primary_name'] or m['crd']))}-{esc(m['crd'])}/'>"
                            f"{esc(title_case(m['primary_name']) or m['crd'])}</a></td>"
                            f"<td class='num'>{esc(money(m['total_raum']))}</td>"
                            f"<td class='num'>{count(m['total_employees'])}</td>"
                            f"<td class='num'>{m['disclosure_flag_count'] or 0}</td></tr>"
                            for m in members
                        )
                        + "</tbody></table>"
                    ),
                ),
            )
            urls.append(f"/city/{code.lower()}/{city_slug}/")

    return urls


def write_disclosures(site: Site, conn: sqlite3.Connection, snapshot: str) -> list[str]:
    rows = conn.execute(
        """
        SELECT crd, primary_name, city, city_key, state, total_raum, total_employees,
               disclosure_flag_count, disclosure_event_count
        FROM firms WHERE snapshot_date = ? AND disclosure_flag_count > 0
        ORDER BY disclosure_flag_count DESC, disclosure_event_count DESC
        """,
        (snapshot,),
    ).fetchall()

    body = [
        "<h1>Advisers reporting disciplinary disclosures</h1>",
        f"<p class='sub'>{len(rows):,} of the SEC-registered advisers in this snapshot "
        "answered Yes to at least one Form ADV Item 11 question.</p>",
        f"<span class='asof'>Form ADV data as of {esc(snapshot)}</span>",
        "<div class='notice'><strong>Read this before drawing conclusions.</strong> "
        "Item 11 covers criminal charges, pending proceedings, and regulatory findings "
        "against a firm <em>or any of its advisory affiliates</em> -- which includes every "
        "officer, director, and non-clerical employee. Large firms therefore accumulate "
        "disclosures simply by employing more people. Counts here are not a misconduct rate "
        "and are not comparable between firms of different sizes.</div>",
        "<table><thead><tr><th>Firm</th><th>Location</th><th class='num'>Questions</th>"
        "<th class='num'>Events</th><th class='num'>Employees</th></tr></thead><tbody>",
    ]
    for r in rows:
        where = ", ".join(
            p for p in (site.city_label(r["state"], r["city_key"], r["city"]), r["state"]) if p
        )
        body.append(
            f"<tr><td><a href='../firm/{esc(slugify(r['primary_name'] or r['crd']))}-{esc(r['crd'])}/'>"
            f"{esc(title_case(r['primary_name']) or r['crd'])}</a></td>"
            f"<td>{esc(where)}</td><td class='num'>{r['disclosure_flag_count']}</td>"
            f"<td class='num'>{r['disclosure_event_count']:,}</td>"
            f"<td class='num'>{count(r['total_employees'])}</td></tr>"
        )
    body.append("</tbody></table>")

    site.write(
        "disclosures/index.html",
        layout(
            site,
            title=f"Investment advisers reporting disciplinary disclosures - {SITE_NAME}",
            description=f"{len(rows):,} SEC-registered investment advisers reported at least one Form ADV Item 11 disciplinary disclosure.",
            canonical="/disclosures/",
            depth=1,
            body="\n".join(body),
        ),
    )
    return []


def firm_link(live: dict[str, str], crd: str, name: str | None, prefix: str) -> str:
    """Link to a firm's page if it exists in this build, otherwise plain text.

    Archived change pages mention firms that may since have deregistered or been
    renamed. Resolving through the current build's URLs avoids linking to pages
    that no longer exist or to a stale slug.
    """
    label = esc(title_case(name) or crd)
    url = live.get(crd)
    return f"<a href='{prefix}{esc(url.lstrip('/'))}'>{label}</a>" if url else label


def render_changes(site: Site, s: dict, live: dict[str, str]) -> str:
    """One month's change page, from an archived summary (see changes.py)."""
    period, previous, snapshot, counts = s["period"], s["from"], s["to"], s["counts"]
    prefix = "../../"

    def where(r: dict) -> str:
        return ", ".join(
            p for p in (site.city_label(r["state"], r.get("city_key"), r.get("city")), r["state"]) if p
        )

    body = [
        f"<h1>What changed in {esc(period)}</h1>",
        f"<p class='sub'>Comparing the SEC's {esc(previous)} and {esc(snapshot)} Form ADV "
        "snapshots.</p>",
        "<div class='stats'>"
        + "".join(
            f"<div class='stat'><div class='label'>{l}</div><div class='value'>{v:,}</div></div>"
            for l, v in [
                ("Newly registered", counts["registered"]),
                ("Deregistered", counts["deregistered"]),
                ("Newly disclosed", counts["newly_flagged_firms"]),
                ("Major asset moves", counts["movers"]),
            ]
        )
        + "</div>",
    ]

    if s["newly_flagged"]:
        body.append(
            "<h2>Firms newly reporting a disclosure</h2>"
            "<p class='sub'>These firms answered Yes this month to an Item 11 question they "
            "answered No to the month before. That can reflect a new event, or an amended "
            "filing of an older one.</p>"
        )
        for r in s["newly_flagged"]:
            place = where(r)
            body.append(
                f"<h3>{firm_link(live, r['crd'], r['name'], prefix)}"
                f"{' &middot; ' + esc(place) if place else ''}</h3>"
                "<ul class='plain'>"
                + "".join(f"<li>{esc(fd.label_for(code))}</li>" for code in r["codes"])
                + "</ul>"
            )

    if s["movers"]:
        body.append(
            "<h2>Largest reported changes in regulatory assets</h2>"
            "<div class='notice'>These are changes in what firms <strong>reported</strong>. "
            "A large move can reflect client flows, market moves, a restatement, or a "
            "reclassification between discretionary and non-discretionary assets. "
            "Do not read them as investor behaviour.</div>"
            "<table><thead><tr><th>Firm</th><th>State</th><th class='num'>Previous</th>"
            "<th class='num'>Current</th><th class='num'>Change</th></tr></thead><tbody>"
        )
        for m in s["movers"]:
            cls = "up" if m["delta"] > 0 else "down"
            pct = f"{m['pct']:+.0%}" if m["pct"] is not None else ""
            body.append(
                f"<tr><td>{firm_link(live, m['crd'], m['name'], prefix)}</td>"
                f"<td>{esc(m['state'] or '')}</td>"
                f"<td class='num'>{esc(money(m['old']))}</td>"
                f"<td class='num'>{esc(money(m['new']))}</td>"
                f"<td class='num {cls}'>{esc(pct)}</td></tr>"
            )
        body.append("</tbody></table>")

    if s["registered"]:
        body.append(
            "<h2>Newly registered advisers</h2><table><thead><tr><th>Firm</th>"
            "<th>Location</th><th class='num'>Assets</th></tr></thead><tbody>"
            + "".join(
                f"<tr><td>{firm_link(live, r['crd'], r['name'], prefix)}</td>"
                f"<td>{esc(where(r))}</td>"
                f"<td class='num'>{esc(money(r['raum']))}</td></tr>"
                for r in s["registered"]
            )
            + "</tbody></table>"
        )

    if s["deregistered"]:
        body.append(
            "<h2>No longer registered</h2>"
            "<p class='sub'>Present in the previous snapshot and absent from this one. "
            "Firms deregister for many ordinary reasons, including mergers and moving to "
            "state registration.</p><ul class='plain'>"
            + "".join(
                f"<li>{firm_link(live, r['crd'], r['name'], prefix)}"
                f"{' &middot; ' + esc(where(r)) if where(r) else ''}</li>"
                for r in s["deregistered"]
            )
            + "</ul>"
        )

    return layout(
        site,
        title=f"Investment adviser changes, {period} - {SITE_NAME}",
        description=(
            f"{counts['registered']} newly registered advisers, {counts['deregistered']} "
            f"deregistrations and {counts['newly_flagged_firms']} firms newly reporting a "
            f"disciplinary disclosure between {previous} and {snapshot}."
        ),
        canonical=f"/changes/{period}/",
        depth=2,
        body="\n".join(body),
    )


def write_changes(site: Site, live: dict[str, str]) -> dict[str, str]:
    """Render every archived month. Returns {url: lastmod} for the sitemap."""
    summaries = changes.load_all()
    pages: dict[str, str] = {}
    for s in summaries:
        site.write(f"changes/{s['period']}/index.html", render_changes(site, s, live))
        pages[f"/changes/{s['period']}/"] = s["to"]

    if summaries:
        items = "".join(
            f"<li><a href='{esc(s['period'])}/'>{esc(s['period'])}</a> &mdash; "
            f"{s['counts']['registered']:,} registered, {s['counts']['deregistered']:,} deregistered, "
            f"{s['counts']['newly_flagged_firms']:,} newly reporting a disclosure</li>"
            for s in reversed(summaries)
        )
        body = (
            "<h1>Monthly changes</h1>"
            "<p class='sub'>The SEC republishes Form ADV data every month. "
            "These pages record what moved.</p>"
            f"<ul class='plain'>{items}</ul>"
        )
    else:
        body = (
            "<h1>Monthly changes</h1><p class='muted'>No month-to-month comparisons "
            "have been published yet.</p>"
        )

    site.write(
        "changes/index.html",
        layout(
            site,
            title=f"Monthly changes - {SITE_NAME}",
            description="Month-by-month changes in SEC investment adviser registrations, disclosures, and assets.",
            canonical="/changes/",
            depth=1,
            body=body,
        ),
    )
    return pages


def write_home(site: Site, conn: sqlite3.Connection, snapshot: str, previous: str | None) -> None:
    totals = conn.execute(
        """
        SELECT COUNT(*) firms, SUM(total_raum) raum,
               SUM(CASE WHEN disclosure_flag_count > 0 THEN 1 ELSE 0 END) flagged,
               COUNT(DISTINCT state) states
        FROM firms WHERE snapshot_date = ?
        """,
        (snapshot,),
    ).fetchone()

    largest = conn.execute(
        """
        SELECT crd, primary_name, state, total_raum FROM firms
        WHERE snapshot_date = ? AND total_raum IS NOT NULL
        ORDER BY total_raum DESC LIMIT 10
        """,
        (snapshot,),
    ).fetchall()

    body = [
        f"<h1>{esc(SITE_NAME)}</h1>",
        f"<p class='sub'>{esc(TAGLINE)}. Every figure on this site comes from Form ADV, "
        "the registration document advisers file with the SEC, republished monthly.</p>",
        f"<span class='asof'>Form ADV data as of {esc(snapshot)}</span>",
        "<div class='stats'>"
        + "".join(
            f"<div class='stat'><div class='label'>{l}</div><div class='value'>{v}</div></div>"
            for l, v in [
                ("Registered advisers", f"{totals['firms']:,}"),
                ("States covered", f"{totals['states']}"),
                ("Reporting a disclosure", f"{totals['flagged']:,}"),
            ]
        )
        + "</div>",
        "<h2>Start here</h2><ul class='plain'>"
        "<li><a href='states/'>Browse advisers by state</a></li>"
        "<li><a href='disclosures/'>Advisers reporting disciplinary disclosures</a></li>"
        "<li><a href='changes/'>What changed this month</a></li>"
        "<li><a href='about/'>Where this data comes from</a></li>"
        "</ul>",
        "<h2>Largest advisers by regulatory assets</h2><table><thead><tr><th>Firm</th>"
        "<th>State</th><th class='num'>Regulatory assets</th></tr></thead><tbody>"
        + "".join(
            f"<tr><td><a href='firm/{esc(slugify(r['primary_name'] or r['crd']))}-{esc(r['crd'])}/'>"
            f"{esc(title_case(r['primary_name']) or r['crd'])}</a></td>"
            f"<td>{esc(r['state'] or '')}</td>"
            f"<td class='num'>{esc(money(r['total_raum']))}</td></tr>"
            for r in largest
        )
        + "</tbody></table>"
        "<p class='muted'>Regulatory assets under management are summed as reported. "
        "Totals across firms double-count assets where one adviser sub-advises another.</p>",
    ]
    site.write(
        "index.html",
        layout(
            site,
            title=f"{SITE_NAME} - {TAGLINE}",
            description=f"Searchable Form ADV data for {totals['firms']:,} SEC-registered investment advisers: assets under management, fee structures, conflicts of interest, and disciplinary disclosures.",
            canonical="/",
            depth=0,
            body="\n".join(body),
        ),
    )


def write_about(site: Site) -> None:
    site.write(
        "about/index.html",
        layout(
            site,
            title=f"About the data - {SITE_NAME}",
            description="Where this site's data comes from, how often it updates, what it does not cover, and how to request a correction.",
            canonical="/about/",
            depth=1,
            body=f"""
<h1>About the data</h1>
<span class='asof'>Current snapshot: {esc(site.snapshot)}</span>

<h2>Source</h2>
<p>Every figure here is taken from the Investment Adviser Information Reports that the
U.S. Securities and Exchange Commission publishes each month, derived from Form ADV
Part 1A. The SEC states that information on sec.gov is public information and may be
copied or further distributed without its permission. This site is not affiliated with,
endorsed by, or connected to the SEC.</p>

<h2>How often it updates</h2>
<p>The SEC republishes the data on the first business day of each month. This site
regenerates from that file. Each page shows the snapshot it was built from.</p>

<h2>What the data does not tell you</h2>
<ul class='plain'>
<li>The SEC does not verify Form ADV. In its own words, neither the SEC nor state
    securities authorities have approved the information filed, and its accuracy is
    not guaranteed. Figures are what the firm reported.</li>
<li>Item 11 disclosure questions cover charges and pending proceedings as well as
    findings. A Yes answer is not a finding of wrongdoing.</li>
<li>Item 11 covers the firm <em>and its advisory affiliates</em> -- every officer,
    director, and non-clerical employee. Firms with more staff accumulate more
    disclosures without that implying a higher rate of misconduct.</li>
<li>This site covers firms only. Individual adviser representatives are a separate
    SEC dataset not included here.</li>
<li>The narrative detail behind each disclosure is filed with the SEC in the firm's
    brochure and Disclosure Reporting Pages, and is not reproduced here.</li>
</ul>

<h2>This is not advice</h2>
<p>Nothing on this site is investment, legal, or tax advice, and nothing here is a
recommendation, rating, or endorsement of any firm. It is a reformatting of a public
regulatory filing. Verify anything that matters against the SEC's own record, which
every firm page links to.</p>

<h2>Corrections</h2>
<p>If a page misstates what your firm filed, the fastest fix is at the source: an
amended Form ADV flows through to this site at the next monthly rebuild. If the error
is ours -- a mismatched record, a mangled name, a misread field -- we will correct it
promptly. Include the firm's CRD number and the page address.</p>
""",
        ),
    )


def write_privacy(site: Site) -> None:
    site.write(
        "privacy/index.html",
        layout(
            site,
            title=f"Privacy policy - {SITE_NAME}",
            description="What this site does and does not collect about visitors, where its adviser data comes from, and how to ask for a correction.",
            canonical="/privacy/",
            depth=1,
            body=f"""
<h1>Privacy policy</h1>
<span class='asof'>Last updated {esc(PRIVACY_UPDATED)}</span>

<p>This is a plain-language summary of how this site handles information. It is
short because the site does very little: it is a set of static pages built from
a public government dataset.</p>

<h2>What we collect from you</h2>
<p><strong>Nothing that you type.</strong> There are no accounts, no sign-ups, no
comments, no contact forms and no search box that reaches us. We do not ask you
for any information, so there is none for us to store.</p>

<h2>Cookies</h2>
<p><strong>This site sets no cookies of its own, runs no analytics of its own,
and carries no advertising.</strong> The only script we put on a page is a block
of structured data that search engines read; it does not execute and collects
nothing.</p>
<p>If advertising is added in future, this page will be updated to say so before
any adverts appear, because advertising networks generally set their own cookies.</p>

<h2>Our host's analytics</h2>
<p>Cloudflare, which hosts this site, adds its own analytics tag to pages sent to
web browsers. Cloudflare Web Analytics is cookieless: it sets no cookies, does not
fingerprint your device, and does not follow you between sites. It counts page
views and basic technical details such as the page requested and the browser type.</p>
<p>This site's content security policy currently blocks that script from running,
so in practice it collects nothing. We describe it here because the tag is present
in the page, and saying so is more accurate than claiming there is nothing there.
If that changes, this page changes with it.</p>

<h2>Server logs</h2>
<p>The site is hosted on Cloudflare Pages. Like any web host, Cloudflare records
requests to the site, which can include your IP address, the page you asked for,
your browser's user-agent string and the time of the request. This is ordinary
technical logging, used to deliver pages and to protect against attacks. We do
not use those logs to build any profile of you and we do not combine them with
anything else.</p>

<h2>Information about investment advisers</h2>
<p>The adviser records on this site are <strong>public regulatory filings</strong>,
republished from the monthly Form ADV data that the U.S. Securities and Exchange
Commission publishes. They are not collected from visitors and they are not
bought from data brokers. The SEC states that information on sec.gov is public
information that may be copied and further distributed.</p>
<p>A firm page can name a person, in that a firm's own name may contain one, and
disciplinary disclosures concern a firm and its advisory affiliates. We publish
only what the firm itself reported to the SEC, we show the date of the filing
snapshot, and every firm page links to the SEC's own record.</p>

<h2>Correcting or removing a record</h2>
<p>If a page misstates what your firm filed, the fastest fix is at the source: an
amended Form ADV flows through to this site at the next monthly rebuild, normally
within a month.</p>
<p>If the mistake is ours -- a mismatched record, a mangled name, a misread field --
write to <a href="mailto:{esc(CONTACT_EMAIL)}">{esc(CONTACT_EMAIL)}</a> with the
firm's CRD number and the address of the page, and we will correct it.</p>
<p>We cannot remove a disclosure that the SEC's own data reports, because this site
reflects that data rather than being a separate record of it. Requests of that kind
belong with the SEC.</p>

<h2>Your rights</h2>
<p>If you are in the UK, the European Economic Area or California, you have rights
over personal information relating to you, including asking what is held and asking
for it to be corrected. For a visitor the practical answer is that we hold nothing
beyond the host's technical logs described above. For adviser records, write to
<a href="mailto:{esc(CONTACT_EMAIL)}">{esc(CONTACT_EMAIL)}</a> and we will respond.</p>
<p>We do not sell personal information and never have.</p>

<h2>Children</h2>
<p>This site is not directed at children and contains nothing aimed at them.</p>

<h2>Changes to this policy</h2>
<p>If this policy changes, the date at the top changes with it. Material changes,
such as introducing advertising or analytics, will be described here before they
take effect.</p>

<h2>Contact</h2>
<p><a href="mailto:{esc(CONTACT_EMAIL)}">{esc(CONTACT_EMAIL)}</a></p>
""",
        ),
    )


def write_headers(site: Site) -> None:
    """Cloudflare Pages reads this file and sends these headers with every response.

    The site has no executable JavaScript, no forms and no third-party resources,
    so the policy can be close to as strict as CSP allows. Verified in a browser:
    script-src 'none' does not block the JSON-LD structured-data blocks, because a
    non-JavaScript script type is a data block rather than a script to execute.

    NOTE: adding advertising will require relaxing script-src, connect-src,
    img-src and frame-src for the ad network, or the adverts will be blocked.
    """
    policy = "; ".join(
        [
            "default-src 'self'",
            "script-src 'none'",
            "style-src 'self'",
            "img-src 'self' data:",
            "font-src 'self'",
            "connect-src 'none'",
            "object-src 'none'",
            "frame-src 'none'",
            "frame-ancestors 'none'",
            "base-uri 'none'",
            "form-action 'none'",
            "upgrade-insecure-requests",
        ]
    )
    permissions = ", ".join(
        f"{feature}=()"
        for feature in (
            "accelerometer", "autoplay", "camera", "display-capture", "geolocation",
            "gyroscope", "magnetometer", "microphone", "midi", "payment",
            "screen-wake-lock", "usb", "xr-spatial-tracking",
        )
    )
    # No `preload` token: preloading HSTS is effectively irreversible for months.
    lines = [
        "/*",
        f"  Content-Security-Policy: {policy}",
        "  Strict-Transport-Security: max-age=31536000; includeSubDomains",
        "  X-Frame-Options: DENY",
        "  X-Content-Type-Options: nosniff",
        "  Referrer-Policy: strict-origin-when-cross-origin",
        f"  Permissions-Policy: {permissions}",
        "  Cross-Origin-Opener-Policy: same-origin",
        "  Cross-Origin-Resource-Policy: same-origin",
    ]
    site.write("_headers", "\n".join(lines) + "\n")


def write_404(site: Site) -> None:
    """Cloudflare Pages serves this with a real 404 status for unmatched paths.

    Without it, Pages falls back to index.html and every nonexistent URL answers
    200 with the home page. Search engines treat that as a soft 404, and an
    unbounded space of URLs all returning the same page wastes crawl budget that
    should go to real firm pages.

    Deliberately not in the sitemap, and not linked from anywhere.
    """
    site.write(
        "404.html",
        layout(
            site,
            title=f"Page not found - {SITE_NAME}",
            description="This page does not exist.",
            canonical="",  # a 404 should not claim to be the canonical of anything
            depth=0,
            root="/",
            body="""
<h1>Page not found</h1>
<p class='sub'>That address does not match anything on this site.</p>
<p>Firms come and go from the SEC's register, so a page that existed in an
earlier monthly snapshot may no longer be here.</p>
<ul class='plain'>
  <li><a href="/states/">Browse advisers by state</a></li>
  <li><a href="/disclosures/">Advisers reporting disciplinary disclosures</a></li>
  <li><a href="/changes/">What changed each month</a></li>
  <li><a href="/about/">About the data</a></li>
</ul>
<p class='muted'>To look up a firm directly, the SEC's own search is at
<a href="https://adviserinfo.sec.gov/" rel="nofollow noopener">adviserinfo.sec.gov</a>.</p>
""",
        ),
    )


def write_sitemap(site: Site, urls: list[str], lastmod: dict[str, str] | None = None) -> None:
    # Sitemaps cap at 50,000 URLs; split if we ever exceed that. Archived change
    # pages keep their own month as lastmod; everything else is the snapshot.
    lastmod = lastmod or {}
    entries = "".join(
        f"<url><loc>{esc(BASE_URL + u)}</loc><lastmod>{esc(lastmod.get(u, site.snapshot))}</lastmod></url>"
        for u in dict.fromkeys(urls)
    )
    site.write(
        "sitemap.xml",
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + entries
        + "</urlset>",
    )
    site.write("robots.txt", f"User-agent: *\nAllow: /\n\nSitemap: {BASE_URL}/sitemap.xml\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=config.DB_PATH)
    parser.add_argument("--out", type=Path, default=config.ROOT / "site")
    parser.add_argument("--limit", type=int, help="only render N firm pages (for a quick check)")
    args = parser.parse_args()
    generate(args.db, args.out, limit=args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
