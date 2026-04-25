"""SEC EDGAR realtime filings firehose.

The /cgi-bin/browse-edgar?action=getcurrent feed is updated every few
seconds. 8-K is the form to watch for material events; 424B5/S-1/S-3 flag
likely dilution which is critical for small-cap risk management.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

import feedparser

from ..config import CONFIG

EDGAR_RECENT = (
    "https://www.sec.gov/cgi-bin/browse-edgar?"
    "action=getcurrent&type={form}&company=&dateb=&owner=include&count=40&output=atom"
)

# Forms we care about for small-cap momentum + dilution detection
WATCH_FORMS = ["8-K", "424B5", "424B4", "424B3", "S-1", "S-3", "S-3/A", "FWP"]
DILUTIVE_FORMS = {"424B5", "424B4", "424B3", "S-1", "S-3", "S-3/A", "FWP"}


@dataclass
class Filing:
    cik: str
    company: str
    ticker: str | None
    form: str
    title: str
    link: str
    filed_at: datetime

    @property
    def is_dilutive(self) -> bool:
        return self.form in DILUTIVE_FORMS


_TICKER_RE = re.compile(r"\(([A-Z]{1,5})\)")


def _headers() -> dict:
    return {"User-Agent": CONFIG.sec_user_agent}


def _parse_entry(entry, form: str) -> Filing | None:
    title = entry.get("title", "")
    company_match = re.search(r" - (.+?) \(", title)
    company = company_match.group(1) if company_match else title
    ticker_match = _TICKER_RE.search(title)
    ticker = ticker_match.group(1) if ticker_match else None

    cik_match = re.search(r"CIK=(\d+)", entry.get("link", ""))
    cik = cik_match.group(1) if cik_match else ""

    updated = entry.get("updated") or entry.get("published") or ""
    try:
        filed_at = datetime.fromisoformat(updated.replace("Z", "+00:00"))
    except ValueError:
        filed_at = datetime.now(timezone.utc)

    return Filing(
        cik=cik,
        company=company,
        ticker=ticker,
        form=form,
        title=title,
        link=entry.get("link", ""),
        filed_at=filed_at,
    )


def fetch_recent_filings(forms: Iterable[str] = WATCH_FORMS) -> list[Filing]:
    out: list[Filing] = []
    for form in forms:
        url = EDGAR_RECENT.format(form=form)
        # feedparser accepts a request_headers kwarg in newer versions
        feed = feedparser.parse(url, request_headers=_headers())
        for entry in feed.entries:
            f = _parse_entry(entry, form)
            if f is not None:
                out.append(f)
    out.sort(key=lambda f: f.filed_at, reverse=True)
    return out


def filings_by_ticker(filings: list[Filing]) -> dict[str, list[Filing]]:
    by: dict[str, list[Filing]] = {}
    for f in filings:
        if not f.ticker:
            continue
        by.setdefault(f.ticker, []).append(f)
    return by
