"""Warnings for places a person cares about besides home.

Someone with a home in Nairobi may want warnings for their child's school in
Nyando or their farm in Budalangi too. Each saved place is a label, a point
(from the map, an address search or the device's location) and the
sub-county the point falls in. Like the home (accounts/home.py), each keeps
one alerts subscription in step with it, for the sub-county and its county,
at the level chosen, by the account's verified email. So a person never
fills in an alerts form for them.

At most SavedPlace.LIMIT places per account.
"""

from __future__ import annotations

from django.contrib.gis.geos import Point
from django.db import transaction

from alerts import stopping
from alerts.models import Subscriber, Unsubscription
from geodata.models import Region

from .home import HOME_PRECISION, HomeError, alert_regions, region_for_point, sync_subscription
from .models import SavedPlace, User

LEVELS = ("advisory", "watch", "warning")


def _where(lat, lon, region_id) -> tuple[Point | None, Region]:
    point = region = None
    if lat is not None and lon is not None:
        try:
            lat, lon = round(float(lat), HOME_PRECISION), round(float(lon), HOME_PRECISION)
        except (TypeError, ValueError) as exc:
            raise HomeError("That location could not be read.") from exc
        point = Point(lon, lat, srid=4326)
        region = region_for_point(lon, lat)
    if region_id:
        region = Region.objects.filter(pk=region_id, level=Region.Level.SUB_COUNTY).first()
        if region is None:
            raise HomeError("Choose a sub-county from the list.")
    if region is None:
        raise HomeError("That spot is not inside a Kenyan sub-county FloodScout covers.")
    return point, region


def _label(label) -> str:
    label = str(label or "").strip()[:40]
    if not label:
        raise HomeError("Give the place a name, such as School or Farm.")
    return label


def _level(level) -> str:
    if level not in LEVELS:
        raise HomeError("Choose advisories, watches or warnings.")
    return level


@transaction.atomic
def add_place(user: User, *, label, address="", lat=None, lon=None, region_id=None, alert_level="watch") -> SavedPlace:
    if user.saved_places.count() >= SavedPlace.LIMIT:
        raise HomeError(f"You can save up to {SavedPlace.LIMIT} places. Remove one first.")
    point, region = _where(lat, lon, region_id)
    place = SavedPlace.objects.create(
        user=user,
        label=_label(label),
        address=str(address or "").strip()[:255],
        point=point,
        region=region,
        alert_level=_level(alert_level),
    )
    sync_place(place)
    return place


@transaction.atomic
def change_place(place: SavedPlace, data: dict) -> SavedPlace:
    if "label" in data:
        place.label = _label(data["label"])
    if "alert_level" in data:
        place.alert_level = _level(data["alert_level"])
    if "alerts_on" in data:
        place.alerts_on = bool(data["alerts_on"])
    if any(key in data for key in ("lat", "lon", "region")):
        place.point, place.region = _where(data.get("lat"), data.get("lon"), data.get("region"))
        if "address" in data:
            place.address = str(data["address"] or "").strip()[:255]
    place.save()
    sync_place(place)
    return place


@transaction.atomic
def remove_place(place: SavedPlace) -> None:
    for subscriber in Subscriber.objects.filter(saved_place=place):
        stopping.stop(subscriber, Unsubscription.How.PLACE_OFF)
    place.delete()


def sync_place(place: SavedPlace) -> Subscriber | None:
    return sync_subscription(
        place.user,
        Subscriber.objects.filter(saved_place=place).first(),
        alert_regions(place.region),
        on=place.alerts_on,
        level=place.alert_level,
        how=Unsubscription.How.PLACE_OFF,
        saved_place=place,
    )


def sync_places(user: User) -> None:
    """Bring every saved place's subscription in step, e.g. once the email is verified."""
    for place in user.saved_places.select_related("region__parent"):
        sync_place(place)


def summary(place: SavedPlace) -> dict:
    sub = Subscriber.objects.filter(saved_place=place).first()
    return {
        "id": place.pk,
        "label": place.label,
        "address": place.address,
        "lat": place.point.y if place.point else None,
        "lon": place.point.x if place.point else None,
        "region": place.region_id,
        "region_name": place.region.name,
        "county": place.region.parent_id,
        "county_name": place.region.county_name,
        "alerts_on": place.alerts_on,
        "alert_level": place.alert_level,
        "receiving": bool(sub and sub.confirmed),
    }
