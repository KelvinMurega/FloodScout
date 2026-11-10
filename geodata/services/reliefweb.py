"""Flood reports for Kenya from ReliefWeb, and the counties each one names.

ReliefWeb (OCHA) gathers situation reports, flash updates and news from
the Kenya Red Cross, NDMA, OCHA and others. Its API answers only an
appname ReliefWeb has approved (RELIEFWEB_APPNAME): request one at
https://apidoc.reliefweb.int/parameters#appname. Without one nothing is
fetched.

Which counties flooded is read from the report's title and text: a county
counts when its name appears there. That is coarse. A report that lists
counties for another reason (drought, displacement) still counts them, and
a flood nobody reported is not seen at all. The live record
(riskengine/live.py) says so beside every figure worked out from it.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

API_URL = "https://api.reliefweb.int/v2/reports"
REQUEST_TIMEOUT_SECONDS = 60
PAGE_SIZE = 200
# A hard stop, so a filter that matches far more than expected cannot keep
# the scheduler fetching.
MAX_PAGES = 10


class ReliefWebError(Exception):
    """The API could not be reached or refused the request."""


@dataclass(frozen=True)
class Report:
    id: int
    title: str
    url: str
    source: str
    published: dt.date
    text: str


def _query(since: dt.date, offset: int) -> dict:
    return {
        "offset": offset,
        "limit": PAGE_SIZE,
        "sort": ["date.original:asc"],
        "fields": {"include": ["title", "body", "url_alias", "url", "date.original", "source.shortname"]},
        "filter": {
            "operator": "AND",
            "conditions": [
                {"field": "primary_country.iso3", "value": "ken"},
                {"field": "disaster_type.name", "value": "Flood"},
                {"field": "date.original", "value": {"from": f"{since.isoformat()}T00:00:00+00:00"}},
            ],
        },
    }


def _post(appname: str, body: dict) -> dict:
    url = f"{API_URL}?{urllib.parse.urlencode({'appname': appname})}"
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        raise ReliefWebError(f"ReliefWeb answered {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ReliefWebError(f"ReliefWeb could not be read: {exc}") from exc


def _report(item: dict) -> Report:
    fields = item.get("fields", {})
    sources = fields.get("source") or []
    return Report(
        id=int(item["id"]),
        title=fields.get("title", ""),
        url=fields.get("url_alias") or fields.get("url") or "",
        source=", ".join(s.get("shortname", "") for s in sources if s.get("shortname")),
        published=dt.date.fromisoformat(fields["date"]["original"][:10]),
        text=f"{fields.get('title', '')}\n{fields.get('body', '')}",
    )


def fetch_reports(appname: str, since: dt.date) -> list[Report]:
    """Every Kenya flood report first published on or after `since`."""
    reports: list[Report] = []
    for page in range(MAX_PAGES):
        data = _post(appname, _query(since, page * PAGE_SIZE))
        items = data.get("data", [])
        reports += [_report(item) for item in items]
        if len(items) < PAGE_SIZE:
            break
    return reports


def _pattern(name: str) -> re.Pattern:
    # "Murang'a" also as "Muranga", "Elgeyo-Marakwet" also as "Elgeyo Marakwet".
    words = re.findall(r"[A-Za-z]+", name)
    return re.compile(r"\b" + r"[\s'’-]*".join(map(re.escape, words)) + r"\b", re.IGNORECASE)


def counties_named(text: str, counties: list[str]) -> list[str]:
    """The counties in `counties` whose name appears in `text`."""
    return [county for county in counties if _pattern(county).search(text)]
