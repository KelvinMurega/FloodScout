"""What happened in a month the outlook covered, county by county.

A month's outlook stays stored after the month is over. This sets it beside
the floods the public record holds for that month, so the map can show
both: the chance FloodScout gave each county, and whether a flood was
reported there.

The record is every ObservedFlood (news reports and GDACS events, from
`fetch_observed_floods`) and every ReliefWeb report (ReportedFlood, from
`fetch_flood_reports`) dated in the month. The Kenya Meteorological
Department's flood warnings in force that month are given too, as context:
a warning is not a flood. "No flood reported" means none was found, not
that none happened.
"""

from __future__ import annotations

import calendar
import datetime as dt

from django.db.models import Q

from geodata.models import KmdWarning, Region
from riskengine.models import ObservedFlood, ReportedFlood, RiskOutlook, RiskScore

SOURCES = [
    {
        "key": "news",
        "name": "News reports",
        "about": "Headlines from Kenyan and international outlets, found through Google News, "
        "that report flood damage and name a county.",
        "url": "https://news.google.com/",
    },
    {
        "key": "reliefweb",
        "name": "ReliefWeb",
        "about": "OCHA's situation reports and flash updates from the Kenya Red Cross, NDMA and others.",
        "url": "https://reliefweb.int/country/ken",
    },
    {
        "key": "gdacs",
        "name": "GDACS",
        "about": "Flood events the Global Disaster Alert and Coordination System recorded for Kenya.",
        "url": "https://www.gdacs.org/",
    },
    {
        "key": "kmd",
        "name": "KMD warnings",
        "about": "The Kenya Meteorological Department's flood warnings in force that month. "
        "A warning, not a flood.",
        "url": "https://meteo.go.ke/",
    },
]


def _span(month: dt.date) -> tuple[dt.date, dt.date]:
    first = month.replace(day=1)
    return first, first.replace(day=calendar.monthrange(first.year, first.month)[1])


def _record(source: str, date: dt.date, title: str, url: str, publisher: str = "") -> dict:
    return {
        "source": source,
        "date": date.isoformat(),
        "title": title,
        "url": url,
        "publisher": publisher,
    }


def month_outcome(month: dt.date, today: dt.date) -> dict:
    """The outlook for `month` beside the floods recorded in it, per county."""
    first, last = _span(month)
    outlook = {
        row.region_id: row
        for row in RiskOutlook.objects.filter(
            month=first, region__level=Region.Level.COUNTY
        ).select_related("region")
    }
    counties = {region.id: region for region in Region.objects.filter(level=Region.Level.COUNTY)}
    records: dict[int, list[dict]] = {region_id: [] for region_id in counties}
    national = []

    in_month = Q(date__range=(first, last)) | Q(
        source=ObservedFlood.Source.GDACS, date__lte=last, ended__gte=first
    )
    for flood in ObservedFlood.objects.filter(in_month).order_by("date"):
        record = _record(flood.source, flood.date, flood.title, flood.url, flood.publisher)
        if flood.county_id is None:
            national.append(record)
        elif flood.county_id in records:
            records[flood.county_id].append(record)
    for report in ReportedFlood.objects.filter(published__range=(first, last)).order_by(
        "published"
    ):
        if report.county_id in records:
            records[report.county_id].append(
                _record("reliefweb", report.published, report.title, report.url, report.source)
            )

    warned: dict[int, int] = {}
    warnings = KmdWarning.objects.filter(
        flood_related=True,
        onset__date__lte=last,
    ).filter(Q(expires__isnull=True, onset__date__gte=first) | Q(expires__date__gte=first))
    for warning in warnings.prefetch_related("regions"):
        # A warning reaching three sub-counties of one county counts once there.
        reached = {
            region.id if region.level == Region.Level.COUNTY else region.parent_id
            for region in warning.regions.all()
        }
        for county_id in reached - {None}:
            warned[county_id] = warned.get(county_id, 0) + 1

    # 1 is the county the outlook gave the highest chance.
    ranked = sorted(outlook.values(), key=lambda row: -row.flood_probability)
    rank = {row.region_id: n for n, row in enumerate(ranked, start=1)}

    rows = []
    for region_id, region in counties.items():
        row = outlook.get(region_id)
        found = sorted(records[region_id], key=lambda record: record["date"])
        rows.append(
            {
                "region": region_id,
                "region_name": region.name,
                "risk_category": row.risk_category if row else None,
                "predicted_value": row.flood_probability if row else None,
                "rank": rank.get(region_id),
                "flooded": bool(found),
                "records": found,
                "kmd_warnings": warned.get(region_id, 0),
            }
        )
    rows.sort(key=lambda r: (not r["flooded"], r["rank"] or len(rows) + 1, r["region_name"]))

    issued = max((row.issued_on for row in outlook.values()), default=None)
    counted = {source["key"]: 0 for source in SOURCES}
    for r in rows:
        for record in r["records"]:
            counted[record["source"]] += 1
    counted["gdacs"] += len(national)
    counted["kmd"] = sum(warned.values())

    # In an El Niño year nearly every county is High or above, so the split
    # by level and the ranks say more than "flagged or not".
    by_category = {
        category: {
            "rated": sum(r["risk_category"] == category for r in rows),
            "flooded": sum(r["risk_category"] == category and r["flooded"] for r in rows),
        }
        for category in RiskScore.RiskCategory.values
    }

    return {
        "month": first.isoformat(),
        "complete": last < today,
        "issued_on": issued.isoformat() if issued else None,
        "has_outlook": bool(outlook),
        "summary": {
            "counties": len(outlook),
            "flooded": sum(r["flooded"] for r in rows),
            "by_category": by_category,
            "flooded_ranks": sorted(r["rank"] for r in rows if r["flooded"] and r["rank"]),
        },
        "sources": [{**source, "records": counted[source["key"]]} for source in SOURCES],
        "national": national,
        "counties": rows,
    }
