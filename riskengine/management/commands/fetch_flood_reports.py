"""Store the Kenya flood reports on ReliefWeb and the counties they name.

    python manage.py fetch_flood_reports              # from LOOKBACK_DAYS before the newest stored
    python manage.py fetch_flood_reports --since 2026-09-01

`refresh_scores` runs it every day. It does nothing until RELIEFWEB_APPNAME
is set, because ReliefWeb answers only appnames it has approved. Running it
twice does no harm: a report already stored is updated, not added again.

The first run starts from the first day with a county score, since earlier
reports have nothing to be compared with. Later runs go back LOOKBACK_DAYS
from the newest report stored, as reports are often added days after the
date they carry.
"""

from __future__ import annotations

import datetime as dt

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Max, Min
from django.utils import timezone

from geodata.models import Region
from geodata.services.reliefweb import ReliefWebError, counties_named, fetch_reports
from riskengine.models import ReportedFlood, RiskScore

LOOKBACK_DAYS = 30


def default_since(today: dt.date) -> dt.date:
    newest = ReportedFlood.objects.aggregate(newest=Max("published"))["newest"]
    if newest:
        return newest - dt.timedelta(days=LOOKBACK_DAYS)
    first = RiskScore.objects.aggregate(first=Min("prediction_date"))["first"]
    return timezone.localtime(first).date() if first else today - dt.timedelta(days=LOOKBACK_DAYS)


class Command(BaseCommand):
    help = "Fetch Kenya flood reports from ReliefWeb and match the counties they name."

    def add_arguments(self, parser):
        parser.add_argument(
            "--since",
            type=dt.date.fromisoformat,
            default=None,
            metavar="YYYY-MM-DD",
            help="Fetch reports published on or after this day.",
        )

    def handle(self, *args, **options):
        appname = settings.RELIEFWEB_APPNAME
        if not appname:
            self.stdout.write("Flood reports skipped: RELIEFWEB_APPNAME is not set.")
            return

        since = options["since"] or default_since(timezone.localdate())
        try:
            reports = fetch_reports(appname, since)
        except ReliefWebError as exc:
            raise CommandError(str(exc)) from exc

        counties = {
            region.name: region for region in Region.objects.filter(level=Region.Level.COUNTY)
        }
        names = sorted(counties)
        matched = 0
        for report in reports:
            for name in counties_named(report.text, names):
                ReportedFlood.objects.update_or_create(
                    reliefweb_id=report.id,
                    county=counties[name],
                    defaults={
                        "title": report.title[:300],
                        "url": report.url,
                        "source": report.source[:200],
                        "published": report.published,
                    },
                )
                matched += 1
        self.stdout.write(
            f"Flood reports since {since.isoformat()}: {len(reports)} on ReliefWeb, "
            f"{matched} report-county matches stored"
        )
