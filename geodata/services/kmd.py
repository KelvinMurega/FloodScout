"""Official warnings from the Kenya Meteorological Department (KMD).

KMD publishes its warnings and advisories as CAP 1.2 alerts (the Common
Alerting Protocol), listed in an RSS feed. Each alert names the event
("Heavy rainfall"), how severe and how certain it is, when it starts and
ends, and the area it covers as one or more polygons. KMD writes a polygon
as "lat,lon" pairs, as CAP requires; GEOS wants (lon, lat).

    rss.xml  ->  links to each alert  ->  <cap:alert> documents

Nothing here touches the database; `fetch_kmd_warnings` stores what this
returns.
"""

from __future__ import annotations

import datetime as dt
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from django.contrib.gis.geos import GEOSGeometry, MultiPolygon, Polygon

RSS_URL = "https://meteo.go.ke/api/cap/rss.xml"
REQUEST_TIMEOUT_SECONDS = 60
USER_AGENT = "FloodScout/1.0 (flood risk dashboard)"
CAP = "{urn:oasis:names:tc:emergency:cap:1.2}"

# Events that bear on floods. Wind and waves are stored too, but neither the
# map nor the alerts use them.
FLOOD_EVENTS = ("rain", "flood", "storm")


class KmdError(Exception):
    """The feed or an alert could not be read."""


@dataclass
class CapAlert:
    identifier: str
    msg_type: str
    status: str
    sent: dt.datetime
    event: str
    severity: str
    urgency: str
    certainty: str
    headline: str
    description: str
    instruction: str
    onset: dt.datetime | None
    expires: dt.datetime | None
    web: str
    area: str
    geom: MultiPolygon | None
    # CAP "sender,identifier,sent" triples of the alerts this one updates or cancels.
    references: list[str] = field(default_factory=list)

    @property
    def flood_related(self) -> bool:
        return is_flood_related(self.event)


def is_flood_related(event: str) -> bool:
    return any(word in event.lower() for word in FLOOD_EVENTS)


def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return response.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        raise KmdError(f"KMD could not be read at {url}: {exc}") from exc


def alert_links(rss: bytes) -> list[str]:
    """The link to each alert in the feed, newest first as KMD lists them."""
    try:
        root = ET.fromstring(rss)
    except ET.ParseError as exc:
        raise KmdError(f"The KMD feed is not valid XML: {exc}") from exc
    return [
        link.text.strip()
        for link in root.iterfind("./channel/item/link")
        if link.text and link.text.strip()
    ]


def _time(text: str | None) -> dt.datetime | None:
    if not text:
        return None
    return dt.datetime.fromisoformat(text.strip())


def _polygon(text: str) -> Polygon | None:
    points = []
    for pair in text.split():
        lat, lon = (float(v) for v in pair.split(","))
        points.append((lon, lat))
    if len(points) < 3:
        return None
    if points[0] != points[-1]:
        points.append(points[0])
    if len(points) < 4:
        return None
    return Polygon(points, srid=4326)


def _multipolygon(polygons: list[Polygon]) -> MultiPolygon | None:
    """One valid MultiPolygon. Hand-drawn polygons often cross themselves."""
    parts = []
    for polygon in polygons:
        # make_valid leaves a valid shape as it is; asking `.valid` first
        # prints a GEOS notice for every hand-drawn crossing.
        geom = polygon.make_valid()
        if isinstance(geom, Polygon):
            parts.append(geom)
        else:  # MultiPolygon or GeometryCollection
            parts.extend(g for g in geom if isinstance(g, Polygon))
    parts = [p for p in parts if not p.empty and p.area > 0]
    if not parts:
        return None
    # Dissolve overlapping parts into one valid shape.
    merged = GEOSGeometry(MultiPolygon(parts, srid=4326).unary_union, srid=4326)
    if isinstance(merged, Polygon):
        merged = MultiPolygon(merged, srid=4326)
    return merged


def parse_alert(xml: bytes) -> CapAlert:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise KmdError(f"A KMD alert is not valid XML: {exc}") from exc

    def text(node, name, default=""):
        found = node.find(f"{CAP}{name}") if node is not None else None
        return (found.text or "").strip() if found is not None and found.text else default

    # KMD writes one <info> in English; take the first in English, or the first.
    infos = root.findall(f"{CAP}info")
    info = next((i for i in infos if text(i, "language", "en").startswith("en")), None)
    if info is None and infos:
        info = infos[0]
    polygons = []
    areas = []
    for area in info.findall(f"{CAP}area") if info is not None else []:
        areas.append(text(area, "areaDesc"))
        for polygon in area.findall(f"{CAP}polygon"):
            if polygon.text and (shape := _polygon(polygon.text)):
                polygons.append(shape)

    sent = _time(text(root, "sent"))
    if not text(root, "identifier") or sent is None:
        raise KmdError("A KMD alert has no identifier or sent time.")
    return CapAlert(
        identifier=text(root, "identifier"),
        msg_type=text(root, "msgType", "Alert"),
        status=text(root, "status", "Actual"),
        sent=sent,
        event=text(info, "event"),
        severity=text(info, "severity"),
        urgency=text(info, "urgency"),
        certainty=text(info, "certainty"),
        headline=text(info, "headline"),
        description=text(info, "description"),
        instruction=text(info, "instruction"),
        onset=_time(text(info, "onset")) or _time(text(info, "effective")) or sent,
        expires=_time(text(info, "expires")),
        web=text(info, "web"),
        area="; ".join(a for a in areas if a),
        geom=_multipolygon(polygons),
        references=text(root, "references").split(),
    )


def fetch_feed() -> list[str]:
    return alert_links(_get(RSS_URL))


def fetch_alert(url: str) -> CapAlert:
    return parse_alert(_get(url))
