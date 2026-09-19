"""Download monthly Investment Adviser Information Reports from sec.gov.

The SEC's filenames are not constructible. Across 2026 alone the registered-adviser
file has been named:

    ia09012026-registered.zip    MMDDYYYY with a "-registered" suffix
    ia08032026_1.zip             MMDDYYYY with a revision suffix, no report suffix
    ia07012026.zip               MMDDYYYY, bare
    ia060126_0.zip               MMDDYY with a revision suffix
    ia050126.zip                 MMDDYY, bare
    ia010226.zip                 MMDDYY, in a different directory

...and February's exempt file is named "ia020226-exemptzip.zip". Guessing URLs
fails. So we scrape the SEC's own index page for links and map each to a month.

Archives go back to 2006 and are never rewritten, so any month can be re-fetched
on demand and the pipeline keeps no state between runs.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402

INDEX_URL = (
    "https://www.sec.gov/data-research/sec-markets-data/"
    "information-about-registered-investment-advisers-exempt-reporting-advisers"
)

REPORTS = ("registered", "exempt")

# href="...ia09012026-registered.zip"
LINK = re.compile(r'href="([^"]*?/ia\d{6,8}[^"]*?\.zip)"', re.IGNORECASE)
# leading date digits in the filename, after the "ia" prefix
FILE_DATE = re.compile(r"^ia(\d{8}|\d{6})", re.IGNORECASE)


def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": config.user_agent()})
    with urllib.request.urlopen(request, timeout=180) as response:
        return response.read()


def parse_filename(name: str) -> tuple[int, int, str, int] | None:
    """('ia08032026_1.zip') -> (year, month, report, revision) or None."""
    match = FILE_DATE.match(name)
    if not match:
        return None
    digits = match.group(1)
    month = int(digits[:2])
    if len(digits) == 8:
        year = int(digits[4:])
    else:
        year = 2000 + int(digits[4:6])
    if not 1 <= month <= 12 or not 2006 <= year <= date.today().year + 1:
        return None

    report = "exempt" if "exempt" in name.lower() else "registered"

    # "_0" / "_1" suffixes are SEC re-uploads; prefer the highest.
    revision_match = re.search(r"_(\d+)\.zip$", name, re.IGNORECASE)
    revision = int(revision_match.group(1)) if revision_match else 0
    return year, month, report, revision


def list_available() -> dict[tuple[int, int, str], str]:
    """Map (year, month, report) -> best URL, discovered from the SEC index page."""
    html = _get(INDEX_URL).decode("utf-8", errors="replace")
    best: dict[tuple[int, int, str], tuple[int, str]] = {}

    for href in LINK.findall(html):
        url = urllib.parse.urljoin(INDEX_URL, href)
        parsed = parse_filename(Path(urllib.parse.urlparse(url).path).name)
        if not parsed:
            continue
        year, month, report, revision = parsed
        key = (year, month, report)
        if key not in best or revision > best[key][0]:
            best[key] = (revision, url)

    return {key: url for key, (_, url) in best.items()}


def fetch_month(
    year: int,
    month: int,
    report: str = "registered",
    *,
    force: bool = False,
    index: dict[tuple[int, int, str], str] | None = None,
) -> Path:
    """Download and extract one month's CSV. Returns the path to the CSV."""
    config.ensure_dirs()
    target = config.RAW / f"ia{month:02d}{year}-{report}.csv"
    if target.exists() and not force:
        print(f"cached: {target.name}")
        return target

    available = index if index is not None else list_available()
    url = available.get((year, month, report))
    if not url:
        months = sorted(k for k in available if k[2] == report)
        raise FileNotFoundError(
            f"no {report} file listed for {year}-{month:02d}. "
            f"Available: {months[0][:2]} .. {months[-1][:2]} ({len(months)} months)"
            if months
            else f"no {report} files found on the index page"
        )

    try:
        payload = _get(url)
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            raise config.ConfigError(
                "sec.gov returned 403, which almost always means the User-Agent "
                f"was rejected. Current value: {config.user_agent()!r}"
            ) from exc
        raise

    archive = config.RAW / Path(urllib.parse.urlparse(url).path).name
    archive.write_bytes(payload)
    try:
        with zipfile.ZipFile(archive) as zf:
            names = [n for n in zf.namelist() if n.upper().endswith(".CSV")]
            if len(names) != 1:
                raise RuntimeError(f"expected one CSV in {archive.name}, found {names}")
            target.write_bytes(zf.read(names[0]))
    finally:
        archive.unlink(missing_ok=True)

    print(f"downloaded: {url}\n        -> {target.name} ({target.stat().st_size / 1e6:.1f} MB)")
    time.sleep(config.REQUEST_DELAY_SECONDS)
    return target


def main() -> int:
    today = date.today()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=today.year)
    parser.add_argument("--month", type=int, default=today.month)
    parser.add_argument("--report", choices=REPORTS, default="registered")
    parser.add_argument("--force", action="store_true", help="re-download even if cached")
    parser.add_argument("--list", action="store_true", help="list what the SEC index offers")
    args = parser.parse_args()

    try:
        if args.list:
            available = list_available()
            for key in sorted(available):
                year, month, report = key
                print(f"  {year}-{month:02d}  {report:<10} {available[key].split('/')[-1]}")
            print(f"\n{len(available)} files listed")
            return 0
        fetch_month(args.year, args.month, args.report, force=args.force)
    except config.ConfigError as exc:
        print(f"\nconfiguration error:\n{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
