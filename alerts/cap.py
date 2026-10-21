"""Alerts as Common Alerting Protocol (CAP) 1.2 messages, and an Atom feed of them.

CAP is the OASIS standard (also ITU-T X.1303) that warning services use to
exchange alerts, and that radio, TV, phone and app systems read. Publishing
FloodScout's alerts as CAP means any of them can take FloodScout's alerts
without knowing anything about FloodScout.

Element order follows the CAP 1.2 schema. Times are given to the second
with their offset, as the standard requires. The area is the region's
outline as a CAP polygon ("lat,lon" pairs, first equal to last), simplified
and taken from its largest part.

`status` is CAP_STATUS from settings, "Exercise" by default: FloodScout is
not a warning authority, and "Exercise" tells a receiving system not to
treat its alerts as official.
"""

from __future__ import annotations

from xml.etree import ElementTree as ET

from django.conf import settings
from django.utils import timezone

from .models import Alert, Level

NS = "urn:oasis:names:tc:emergency:cap:1.2"
ATOM = "http://www.w3.org/2005/Atom"
SENDER = "floodscout@strathmore.edu"
SENDER_NAME = "FloodScout (Strathmore University research system)"

# Simplification tolerance for the area polygon, degrees (about 1 km).
POLYGON_TOLERANCE = 0.01

SEVERITY = {Level.ADVISORY: "Minor", Level.WATCH: "Moderate", Level.WARNING: "Severe"}
RESPONSE = {Level.ADVISORY: "Monitor", Level.WATCH: "Prepare", Level.WARNING: "Prepare"}


def cap_time(value) -> str:
    """2026-10-20T21:00:00+03:00: local time, to the second, with the offset."""
    return timezone.localtime(value).replace(microsecond=0).isoformat()


def urgency(alert: Alert) -> str:
    if alert.status != Alert.Status.ACTIVE:
        return "Past"
    return "Future" if alert.onset > timezone.localdate() else "Expected"


def certainty(alert: Alert) -> str:
    # A forecast of a chance is "Possible"; today's score is "Likely" only
    # in the sense CAP means it (p > ~50% that the condition holds), which
    # a model with these limits cannot claim for a flood itself.
    return "Possible"


def msg_type(alert: Alert) -> str:
    if alert.status == Alert.Status.CANCELLED:
        return "Cancel"
    return "Update" if alert.version > 1 else "Alert"


def polygon(region) -> str | None:
    """The region's largest part as a CAP polygon, or None."""
    geom = region.geom.simplify(POLYGON_TOLERANCE, preserve_topology=True)
    parts = list(geom) if geom.geom_type == "MultiPolygon" else [geom]
    if not parts:
        return None
    ring = max(parts, key=lambda part: part.area).exterior_ring.coords
    if len(ring) < 4:
        return None
    return " ".join(f"{lat:.4f},{lon:.4f}" for lon, lat in ring)


def _sub(parent, tag, text=None):
    node = ET.SubElement(parent, f"{{{NS}}}{tag}")
    if text is not None:
        node.text = str(text)
    return node


def cap_element(alert: Alert, web: str) -> ET.Element:
    root = ET.Element(f"{{{NS}}}alert")
    _sub(root, "identifier", f"floodscout-{alert.identifier}-v{alert.version}")
    _sub(root, "sender", SENDER)
    _sub(root, "sent", cap_time(alert.updated_at))
    _sub(root, "status", settings.CAP_STATUS)
    _sub(root, "msgType", msg_type(alert))
    _sub(root, "scope", "Public")
    if msg_type(alert) != "Alert":
        # The message this one updates or cancels: the first issue.
        _sub(
            root,
            "references",
            f"{SENDER},floodscout-{alert.identifier}-v1,{cap_time(alert.issued_at)}",
        )

    info = _sub(root, "info")
    _sub(info, "language", "en-KE")
    _sub(info, "category", "Met")
    _sub(info, "event", "Flood risk")
    _sub(info, "responseType", "None" if msg_type(alert) == "Cancel" else RESPONSE[alert.level])
    _sub(info, "urgency", urgency(alert))
    _sub(info, "severity", SEVERITY[alert.level])
    _sub(info, "certainty", certainty(alert))
    _sub(info, "onset", f"{alert.onset.isoformat()}T00:00:00+03:00")
    _sub(info, "expires", cap_time(alert.expires))
    _sub(info, "senderName", SENDER_NAME)
    _sub(info, "headline", alert.headline)
    _sub(info, "description", alert.description)
    _sub(info, "instruction", alert.instruction)
    _sub(info, "web", web)
    parameter = _sub(info, "parameter")
    _sub(parameter, "valueName", "basis")
    _sub(parameter, "value", alert.basis)
    if alert.times_usual is not None:
        parameter = _sub(info, "parameter")
        _sub(parameter, "valueName", "timesUsualChance")
        _sub(parameter, "value", f"{alert.times_usual:.1f}")

    area = _sub(info, "area")
    _sub(area, "areaDesc", str(alert.region))
    shape = polygon(alert.region)
    if shape:
        _sub(area, "polygon", shape)
    geocode = _sub(area, "geocode")
    _sub(geocode, "valueName", "FloodScoutRegionId")
    _sub(geocode, "value", alert.region_id)
    return root


def cap_xml(alert: Alert, web: str) -> bytes:
    ET.register_namespace("", NS)
    return ET.tostring(cap_element(alert, web), encoding="utf-8", xml_declaration=True)


def atom_feed(alerts, self_url: str, cap_url) -> bytes:
    """An Atom feed with one entry per alert, each linking to its CAP message."""
    ET.register_namespace("", ATOM)

    def sub(parent, tag, text=None, **attrs):
        node = ET.SubElement(parent, f"{{{ATOM}}}{tag}", attrs)
        if text is not None:
            node.text = str(text)
        return node

    feed = ET.Element(f"{{{ATOM}}}feed")
    sub(feed, "id", self_url)
    sub(feed, "title", "FloodScout flood alerts for Kenya")
    newest = max((a.updated_at for a in alerts), default=timezone.now())
    sub(feed, "updated", cap_time(newest))
    sub(feed, "author").append(_atom_name(SENDER_NAME))
    sub(feed, "link", rel="self", href=self_url)
    for alert in alerts:
        entry = sub(feed, "entry")
        sub(entry, "id", f"urn:uuid:{alert.identifier}")
        sub(entry, "title", alert.headline)
        sub(entry, "updated", cap_time(alert.updated_at))
        sub(entry, "summary", alert.description)
        sub(entry, "link", rel="alternate", type="application/cap+xml", href=cap_url(alert))
    return ET.tostring(feed, encoding="utf-8", xml_declaration=True)


def _atom_name(name: str) -> ET.Element:
    node = ET.Element(f"{{{ATOM}}}name")
    node.text = name
    return node
