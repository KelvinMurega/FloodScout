"""Flood events in Kenya from GDACS, the Global Disaster Alert and Coordination System.

GDACS (the United Nations and the European Commission) records the floods
large enough to warrant international attention, from GloFAS and partner
reports, with the dates they began and ended. Its API needs no key.

It records few Kenyan floods (three from 2018 to 2026, all Orange) and
places many of them with a single point for the whole country. A county is
named only where GDACS gives an outline of the flooded area; otherwise the
event stands for Kenya as a whole.
"""

from __future__ import annotations

import datetime as dt
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from django.contrib.gis.geos import GEOSException, GEOSGeometry

EVENTS_URL = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"
GEOMETRY_URL = "https://www.gdacs.org/gdacsapi/api/polygons/getgeometry"
REQUEST_TIMEOUT_SECONDS = 60


class GdacsError(Exception):
    """GDACS could not be read."""


@dataclass(frozen=True)
class Event:
    id: int
    episode: int
    title: str
    alert: str
    began: dt.date
    ended: dt.date
    url: str
    # Outlines of the flooded area, where GDACS gives any.
    areas: list = field(default_factory=list, hash=False, compare=False)


def _get(url: str) -> dict:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        raise GdacsError(f"GDACS answered {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise GdacsError(f"GDACS could not be read: {exc}") from exc
    # No events is an empty body, not an empty list.
    if not body.strip():
        return {"features": []}
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise GdacsError(f"GDACS sent something that is not JSON: {exc}") from exc


def areas_of(collection: dict) -> list[GEOSGeometry]:
    """The polygons in a GDACS geometry collection; points and lines are not areas."""
    areas = []
    for feature in collection.get("features", []):
        geometry = feature.get("geometry") or {}
        if geometry.get("type") not in ("Polygon", "MultiPolygon"):
            continue
        try:
            areas.append(GEOSGeometry(json.dumps(geometry), srid=4326))
        except (GEOSException, ValueError):
            continue
    return areas


def event_of(feature: dict) -> Event:
    props = feature["properties"]
    return Event(
        id=int(props["eventid"]),
        episode=int(props.get("episodeid") or 1),
        title=props.get("htmldescription") or props.get("name") or "Flood in Kenya",
        alert=props.get("alertlevel", ""),
        began=dt.date.fromisoformat(props["fromdate"][:10]),
        ended=dt.date.fromisoformat(props["todate"][:10]),
        url=(props.get("url") or {}).get("report", ""),
    )


def fetch_events(first: dt.date, last: dt.date) -> list[Event]:
    """Kenya's flood events that were under way at any time from `first` to `last`."""
    params = {
        "eventlist": "FL",
        "country": "Kenya",
        # An event that began before the month and ran into it counts.
        "fromdate": (first - dt.timedelta(days=90)).isoformat(),
        "todate": last.isoformat(),
    }
    data = _get(f"{EVENTS_URL}?{urllib.parse.urlencode(params)}")
    events = []
    for feature in data.get("features", []):
        event = event_of(feature)
        if event.ended < first or event.began > last:
            continue
        geometry = _get(
            f"{GEOMETRY_URL}?"
            + urllib.parse.urlencode(
                {"eventtype": "FL", "eventid": event.id, "episodeid": event.episode}
            )
        )
        event.areas.extend(areas_of(geometry))
        events.append(event)
    return events
