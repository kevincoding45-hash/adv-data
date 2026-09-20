# adv-data

Pipeline for the SEC's monthly Form ADV bulk data (registered investment advisers).

Source data is public domain. From the SEC's site policy: *"Information presented
on sec.gov is considered public information and may be copied or further
distributed by users of the web site without the SEC's permission."* Pages built
from it must cite the SEC and show the snapshot date.

## Setup

```
python -m venv .venv
.venv\Scripts\python.exe -m pip install pypdf
```

`pypdf` is only needed to re-extract the Form ADV reference text; the pipeline
itself is standard library only.

sec.gov returns **403 for generic User-Agents**. Set a declared contact before
fetching anything:

```
$env:SEC_USER_AGENT = "Your Project Name contact@yourdomain.com"
```

In GitHub Actions, store this as a repository secret. The repo is public, so it
must never be committed.

## Usage

```
.venv\Scripts\python.exe src\fetch.py --list
.venv\Scripts\python.exe src\fetch.py --year 2026 --month 9
.venv\Scripts\python.exe src\ingest.py --year 2026 --month 9
.venv\Scripts\python.exe src\diff.py 2026-08-01 2026-09-01
.venv\Scripts\python.exe src\generate_site.py
.venv\Scripts\python.exe src\check_coverage.py data\raw\ia09012026-registered.csv
.venv\Scripts\python.exe src\test_diff.py
.venv\Scripts\python.exe src\test_format.py
.venv\Scripts\python.exe src\test_city.py
.venv\Scripts\python.exe src\test_dictionary.py
```

A full build is ~17,700 files / 119 MB in about 35 seconds.

## Automation

`src/pipeline.py` runs the whole thing: discover the newest published month,
fetch it and the month before, ingest both, diff, archive the change summary,
and generate. A full cold run takes about 85 seconds.

```
python src/pipeline.py --rebuild
```

### The change archive

The database is rebuilt from scratch every run from only the two newest months,
which keeps builds fast (two snapshots are already ~390 MB). The one thing that
must outlive a run is the month-over-month history, so each month's change
summary is written to `archive/changes/<YYYY-MM>.json` and committed by CI.
Every rebuild renders all archived months, so `/changes/` pages accumulate
instead of being replaced.

The files are deterministic -- sorted, no timestamps -- so re-running a month
produces identical bytes and CI only commits when the SEC publishes new data.

To rebuild the archive, for example after changing what a summary contains:

```
python src/pipeline.py --backfill-from 2025-12
```

This processes months one at a time and holds at most two months of field data,
so it can go back as far as the SEC's unbroken run of monthly files allows.
Archived pages link to a firm only if that firm has a page in the current build,
so firms that later deregister or rename never produce dead links.

Two workflows in `.github/workflows`:

- `tests.yml` -- runs on every push and pull request. Touches nothing external,
  needs no secrets.
- `build.yml` -- runs daily for the first seven days of each month (the SEC
  publishes on the first business day, which is not a fixed date) and on manual
  dispatch. Runs the tests, builds, checks the output looks sane, uploads the
  site as an artifact, and deploys to Cloudflare Pages.

Required repository secrets:

| Secret | Needed for |
|---|---|
| `SEC_USER_AGENT` | fetching from sec.gov (required) |
| `CLOUDFLARE_API_TOKEN` | deploying (optional) |
| `CLOUDFLARE_ACCOUNT_ID` | deploying (optional) |

The deploy step skips itself cleanly when the Cloudflare secrets are absent, so
the workflow is useful before an account exists -- the build still lands as a
downloadable artifact.

## Security

There is no server, database or user input in production -- the site is static
files on Cloudflare Pages -- so the attack surface is the build pipeline and the
response headers.

- `site/_headers` is generated on every build and read by Cloudflare Pages. It
  sets a strict Content-Security-Policy (`script-src 'none'`), HSTS, frame denial,
  a deny-all Permissions-Policy, and cross-origin isolation headers. Verified in a
  browser that `script-src 'none'` does not block the JSON-LD structured data,
  because a non-JavaScript script type is a data block, not a script.
- **Adding advertising will require relaxing the CSP** (`script-src`, `connect-src`,
  `img-src`, `frame-src`) or the adverts will be silently blocked.
- The SEC User-Agent contains a real email address and is never printed. Errors
  call `config.describe_user_agent()`, which redacts the local part, because CI
  logs on a public repository are world-readable.
- The deploy step is not `continue-on-error`: a failed deploy fails the run. Only
  a genuinely missing Cloudflare secret skips, so the workflow still works before
  Cloudflare is connected.
- `GITHUB_TOKEN` is scoped to `contents: write`, used solely for the monthly
  commit. `tests.yml` needs no secrets, so pull requests from forks cannot reach
  them.
- Optional hardening not done: pinning `actions/*` and `wrangler` to exact
  commit SHAs / versions. They are first-party publishers, and an unverified pin
  risks breaking deploys; worth doing once a pinned version has been tested.

## Known issues

- **Cloudflare Pages caps free deployments at 20,000 files** and the current
  build is 17,705. Adding individual adviser representatives would exceed it;
  that would need sharding, a different host, or client-side rendering. The
  build workflow warns above 19,500.
- Title-casing all-caps source names cannot recover internal capitals, so
  "RUBINBROWN" renders as "Rubinbrown" and "ACR" as "Acr". An exceptions list
  would fix the common ones.
- Per-category disclosure counts (`Count of 11X disclosures`) are aggregated into
  a firm total at ingest but not stored per category, so firm pages show the
  total rather than a count beside each question.

`ingest.py` refuses to write a snapshot that fails validation, so a broken month
fails loudly instead of quietly publishing wrong data about real firms.

## Layout

| Path | Purpose |
|---|---|
| `src/field_dictionary.py` | Form ADV item code to human label, type, and publish flag |
| `src/fetch.py` | Download and unzip a monthly archive |
| `src/ingest.py` | CSV to SQLite, with typing and quality gates |
| `src/pipeline.py` | Orchestrates the whole run; what CI calls |
| `src/changes.py` | Monthly change summaries, archived as committed JSON |
| `src/test_changes.py` | Tests for the archive: content, determinism, links, month runs |
| `archive/changes/` | One committed JSON file per month of changes |
| `src/generate_site.py` | Static site generation, stdlib only |
| `src/city.py` | City name normalisation and canonical display names |
| `src/assets/style.css` | Single stylesheet, light and dark |
| `src/test_format.py` | Tests for name, slug, and currency formatting |
| `src/diff.py` | Month-over-month change detection with materiality thresholds |
| `src/test_diff.py` | Tests for the materiality rules |
| `src/check_coverage.py` | Verify the dictionary still covers every column |
| `src/extract_form_text.py` | Re-extract Form ADV Part 1A text from the SEC PDF |
| `reference/formadv-part1a.txt` | Source of truth for every field label |

## Data notes

- **Filenames are not constructible.** The SEC has used `MMDDYYYY` and `MMDDYY`,
  with and without a `-registered` suffix, with `_0`/`_1`/`_2` revision suffixes,
  across two different directories -- and at least one typo (`ia020226-exemptzip.zip`).
  `fetch.py` scrapes the SEC's index page for links rather than building URLs.
- The index lists 337 files from 2006-06 to the present, with a gap between
  2023-06 and 2025-11 that is probably in the FOIA archive.
- Files are **Windows-1252**, not UTF-8.
- The header contains embedded newlines; a real CSV parser is required.
- Columns are bare Form ADV item codes (`5F(2)(c)` = total regulatory AUM).
  Every label in the dictionary is taken verbatim from Form ADV Part 1A and
  carries `verified=True`. Anything `verified=False` must not be published.
- Each Item 11 disciplinary flag is paired with a `Count of <code> disclosures`
  column, so event counts are available, not just yes/no.
- Item 5.D. has no "fewer than 5 clients" column for client categories (d), (e)
  and (f) -- the form does not offer it.
- The file covers **firms only**. The ~300k individual adviser representatives
  are a separate IAPD dataset.
- Item 11 flags are booleans; the narrative behind each event lives in the Part 2
  brochures (380-660 MB of PDFs per month), not here.

## Editorial rules

This data describes named real businesses and, by extension, real people.

- Present facts, never recommendations. "Reports 3 regulatory disclosures" is a
  fact. "Avoid this firm" is advice, and out of scope.
- Every page shows its snapshot date and links to the firm's IAPD record.
- Provide a correction path and act on it.
- A disclosure flag is not a finding of wrongdoing. Several Item 11 questions
  cover *charges* and *pending proceedings*, not outcomes.
