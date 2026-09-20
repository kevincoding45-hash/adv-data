"""Shared configuration and paths."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RAW = DATA / "raw"
DB_PATH = DATA / "adv.sqlite"
REFERENCE = ROOT / "reference"

# The SEC publishes the monthly Investment Adviser Information Reports here.
# Note: data.gov lists these under a "dcm.sec.gov" host that does not resolve
# publicly -- www.sec.gov is the correct origin.
BULK_BASE = (
    "https://www.sec.gov/files/investment/data/other/"
    "information-about-registered-investment-advisers-exempt-reporting-advisers"
)

# sec.gov's WAF rejects generic User-Agents with a 403.  Their fair-access policy
# requires a declared name and a working contact address, e.g.
#     "Example Research contact@example.com"
USER_AGENT_ENV = "SEC_USER_AGENT"

# SEC fair access allows 10 requests/second; we make roughly one per month, but
# keep a courtesy delay for any loop that fetches several archives.
REQUEST_DELAY_SECONDS = 0.5


class ConfigError(RuntimeError):
    pass


def user_agent() -> str:
    """The User-Agent to send to sec.gov, or raise with instructions."""
    ua = os.environ.get(USER_AGENT_ENV, "").strip()
    if not ua:
        raise ConfigError(
            f"{USER_AGENT_ENV} is not set.\n"
            "sec.gov returns 403 for generic User-Agents. Set it to a declared\n"
            "name plus a working contact address, for example:\n"
            f'    $env:{USER_AGENT_ENV} = "Example Research contact@example.com"\n'
            "In GitHub Actions, store it as a repository secret and expose it as\n"
            "an environment variable -- never commit it, since the repo is public."
        )
    if "@" not in ua:
        raise ConfigError(
            f"{USER_AGENT_ENV} must include a contact email address. "
            f"Got {len(ua)} characters with no '@'."
        )
    return ua


def describe_user_agent() -> str:
    """A redacted description, safe to print.

    The User-Agent contains a real email address and CI logs on a public repo are
    world-readable, so the value itself must never reach a log.
    """
    ua = os.environ.get(USER_AGENT_ENV, "").strip()
    if not ua:
        return "unset"
    name, _, address = ua.rpartition(" ")
    domain = address.rpartition("@")[2] or "?"
    return f"name={name!r}, contact=<redacted>@{domain}"


def ensure_dirs() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    REFERENCE.mkdir(parents=True, exist_ok=True)
