"""Store the floods that happened, month by month, from news reports and GDACS.

    python manage.py fetch_observed_floods                    # last month and this one
    python manage.py fetch_observed_floods --month 2026-10    # one month
    python manage.py fetch_observed_floods --outlook-months   # every month with an outlook, to date

`refresh_scores` runs the first form every day, so a month's record keeps
growing while stories about it are still being written. Running it twice
does no harm: a record already stored is updated, not added again.

What is stored is what a past month's outlook is checked against on the
map (riskengine/outcome.py); see geodata/services/flood_news.py and
geodata/services/gdacs.py for how each source is read and where it falls
short.
"""

from __future__ import annotations

import calendar
import datetime as dt

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from geodata.models import Region
from geodata.services import flood_news, gdacs
from riskengine.models import ObservedFlood, RiskOutlook


def month_span(month: dt.date) -> tuple[dt.date, dt.date]:
    first = month.replace(day=1)
    return first, first.replace(day=calendar.monthrange(first.year, first.month)[1])


def previous_month(day: dt.date) -> dt.date:
    return (day.replace(day=1) - dt.timedelta(days=1)).replace(day=1)


def parse_month(text: str) -> dt.date:
    try:
        return dt.date.fromisoformat(f"{text[:7]}-01")
    except ValueError as exc:
        raise CommandError(f"Expected a month as YYYY-MM, not {text!r}.") from exc


class Command(BaseCommand):
    help = "Fetch the floods reported in Kenya, by month, from news reports and GDACS."

    def add_arguments(self, parser):
        parser.add_argument(
            "--month",
            action="append",
            type=parse_month,
            default=None,
            metavar="YYYY-MM",
            help="A month to fetch; repeat for more. Default: last month and this one.",
        )
        parser.add_argument(
            "--outlook-months",
            action="store_true",
            help="Every month an outlook is stored for, up to this one.",
        )

    def handle(self, *args, **options):
        today = timezone.localdate()
        months = set(options["month"] or [])
        if options["outlook_months"]:
            months |= set(
                RiskOutlook.objects.filter(month__lte=today)
                .values_list("month", flat=True)
                .distinct()
            )
        if not months:
            months = {previous_month(today), today.replace(day=1)}

        counties = {
            region.name: region for region in Region.objects.filter(level=Region.Level.COUNTY)
        }
        if not counties:
            raise CommandError("No counties in the database — run `seed_regions` first.")

        failures = []
        for month in sorted(months):
            first, last = month_span(month)
            last = min(last, today)
            if first > today:
                continue
            for name, step in (("News", self.news), ("GDACS", self.gdacs)):
                try:
                    count = step(first, last, counties)
                except (flood_news.FloodNewsError, gdacs.GdacsError) as exc:
                    failures.append(f"{name} {first:%B %Y}: {exc}")
                    continue
                self.stdout.write(f"{first:%B %Y}: {count} {name} record(s) naming a county")
        if failures:
            # Every month was tried; one failed source does not lose the rest.
            raise CommandError("; ".join(failures))

    def news(self, first, last, counties) -> int:
        names = sorted(counties)
        stored = 0
        for story in flood_news.fetch_month(first, last):
            if not flood_news.reports_damage(story.title):
                continue
            for name in flood_news.counties_in(story.title, names):
                ObservedFlood.objects.update_or_create(
                    source=ObservedFlood.Source.NEWS,
                    url=story.url[:1000],
                    county=counties[name],
                    defaults={
                        "date": story.published,
                        "title": story.title[:300],
                        "publisher": story.publisher[:200],
                    },
                )
                stored += 1
        return stored

    def gdacs(self, first, last, counties) -> int:
        stored = 0
        for event in gdacs.fetch_events(first, last):
            hit = [
                region
                for region in counties.values()
                if any(region.geom.intersects(area) for area in event.areas)
            ]
            for county in hit or [None]:
                ObservedFlood.objects.update_or_create(
                    source=ObservedFlood.Source.GDACS,
                    url=event.url[:1000],
                    county=county,
                    defaults={
                        "date": event.began,
                        "ended": event.ended,
                        "title": f"{event.alert} flood event: {event.title}"[:300],
                        "publisher": "GDACS",
                    },
                )
                stored += county is not None
        return stored
