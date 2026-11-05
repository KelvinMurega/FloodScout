"""Store the warnings in the Kenya Meteorological Department's CAP feed.

    python manage.py fetch_kmd_warnings

The scheduler (`refresh_scores --every-hours 24`) runs this every hour.
Each alert is read once: one already stored is skipped, since a CAP alert
never changes after it is sent. KMD changes a warning by sending a new
alert that references the old one: an Update replaces it, a Cancel
withdraws it. Both mark the old one cancelled here.

Only Actual alerts are kept; Exercise, Test and System messages are not
warnings. Each kept alert is matched to the counties and sub-counties it
covers:

- When KMD's area text names counties ("Kwale, Mombasa, Kilifi"), those
  counties and their sub-counties. The text is what a reader of the
  warning sees, and KMD sometimes reuses a polygon far larger than the
  counties it names: the coast advisory of 24 October 2026 named six
  counties over an outline of most of Kenya.
- Otherwise ("Coast, Western, Northern ..."), the regions the polygons
  reach, after shrinking them by INSET_DEG so a county that only touches
  an edge is not counted.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from geodata.models import KmdWarning, Region
from geodata.services import kmd
from geodata.services.reliefweb import counties_named

# About 2 km: a county touching a warning's edge is not inside it.
INSET_DEG = 0.02


def _referenced(references: list[str]) -> list[str]:
    """The identifiers in CAP's "sender,identifier,sent" triples."""
    return [parts[1] for ref in references if len(parts := ref.split(",")) >= 2]


class Command(BaseCommand):
    help = "Fetch the Kenya Meteorological Department's warnings and the regions they cover."

    def handle(self, *args, **options):
        try:
            links = kmd.fetch_feed()
        except kmd.KmdError as exc:
            raise CommandError(str(exc)) from exc

        known = set(KmdWarning.objects.values_list("url", flat=True))
        stored = failed = 0
        for url in links:
            if url in known:
                continue
            try:
                alert = kmd.fetch_alert(url)
            except kmd.KmdError as exc:
                # One unreadable alert must not hide the others.
                self.stderr.write(self.style.WARNING(f"Skipped {url}: {exc}"))
                failed += 1
                continue
            if alert.status != "Actual":
                continue
            self.store(url, alert)
            stored += 1

        in_force = KmdWarning.in_force().filter(flood_related=True).count()
        self.stdout.write(
            f"KMD: {len(links)} alerts in the feed, {stored} new, {failed} unreadable; "
            f"{in_force} flood-related in force"
        )

    @transaction.atomic
    def store(self, url: str, alert: kmd.CapAlert) -> KmdWarning:
        if alert.msg_type in ("Update", "Cancel"):
            KmdWarning.objects.filter(identifier__in=_referenced(alert.references)).update(
                cancelled=True
            )
        warning, _ = KmdWarning.objects.update_or_create(
            identifier=alert.identifier,
            defaults={
                "url": url,
                "msg_type": alert.msg_type,
                "sent": alert.sent,
                "event": alert.event[:100],
                "severity": alert.severity[:20],
                "urgency": alert.urgency[:20],
                "certainty": alert.certainty[:20],
                "headline": alert.headline,
                "description": alert.description,
                "instruction": alert.instruction,
                "area": alert.area,
                "web": alert.web[:500],
                "onset": alert.onset,
                "expires": alert.expires,
                "flood_related": alert.flood_related,
                # A Cancel is not itself a warning in force.
                "cancelled": alert.msg_type == "Cancel",
                "geom": alert.geom,
            },
        )
        warning.regions.set(covered(alert))
        return warning


def covered(alert: kmd.CapAlert):
    """The regions a warning covers: see the module docstring."""
    counties = list(
        Region.objects.filter(level=Region.Level.COUNTY).values_list("name", flat=True)
    )
    named = counties_named(alert.area, counties)
    if named:
        return Region.objects.filter(county_name__in=named)
    if alert.geom is None:
        return Region.objects.none()
    inner = alert.geom.buffer(-INSET_DEG)
    return Region.objects.filter(geom__intersects=alert.geom if inner.empty else inner)
