"""Warnings for where a person lives, without them having to ask.

The profile holds where the person lives: an address and a point, from the
address search below or from their phone's location, and the sub-county
that point falls in. `sync_home_subscription` keeps one alerts subscription
(Subscriber.is_home) in step with it:

  places    the home sub-county and its county, since alerts are issued for
            both
  level     the person's choice, watches and warnings by default
  contact   the account's email once it is verified (accounts/
            verification.py); alerts go by email only, and the
            subscription waits until the email is verified

So every alert the rules issue for that sub-county or county, from today's
score or from the 7-day forecast, reaches them by itself (alerts/engine.py
marks those messages "where you live"). Turning automatic warnings off, or
clearing the home, removes the subscription.

Address search goes to Nominatim, OpenStreetMap's free geocoder, from the
server: limited to Kenya, one request a second at most and cached, with a
User-Agent naming FloodScout, as its usage policy asks.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from django.contrib.gis.geos import Point
from django.core.cache import cache
from django.utils import timezone

from alerts import stopping
from alerts.models import Subscriber, Unsubscription
from geodata.models import Region

from .models import User

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_TIMEOUT = 15
NOMINATIM_SPACING = 1.0  # seconds between requests, from the usage policy
USER_AGENT = "FloodScout/1.0 (Strathmore University flood early-warning project)"
# Decimal places kept for the home point: 3 is about 110 m.
HOME_PRECISION = 3

_last_call = 0.0
_lock = threading.Lock()


class HomeError(Exception):
    """Shown to the person as it is."""


def region_for_point(lon: float, lat: float) -> Region | None:
    """The sub-county the point lies in, or None outside Kenya's sub-counties."""
    point = Point(lon, lat, srid=4326)
    return (
        Region.objects.filter(level=Region.Level.SUB_COUNTY, geom__contains=point)
        .select_related("parent")
        .first()
    )


def geocode(query: str) -> list[dict]:
    """Places in Kenya matching `query`: [{label, lat, lon}], best first."""
    query = " ".join((query or "").split())
    if len(query) < 3:
        raise HomeError("Type at least three letters of the place.")
    key = f"nominatim:{query.lower()}"
    cached = cache.get(key)
    if cached is not None:
        return cached

    global _last_call
    with _lock:
        wait = NOMINATIM_SPACING - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()
        params = urllib.parse.urlencode(
            {"q": query, "format": "jsonv2", "countrycodes": "ke", "limit": 5, "addressdetails": 0}
        )
        request = urllib.request.Request(
            f"{NOMINATIM_URL}?{params}", headers={"User-Agent": USER_AGENT, "Accept-Language": "en"}
        )
        try:
            with urllib.request.urlopen(request, timeout=NOMINATIM_TIMEOUT) as response:
                rows = json.loads(response.read())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise HomeError("The address search could not be reached. Try again.") from exc

    places = [
        {"label": row.get("display_name", ""), "lat": float(row["lat"]), "lon": float(row["lon"])}
        for row in rows
        if "lat" in row and "lon" in row
    ]
    cache.set(key, places, 60 * 60 * 24)
    return places


def set_home(user: User, *, address: str = "", lat=None, lon=None, region_id=None) -> User:
    """Record where `user` lives, from a point or a chosen sub-county."""
    region = None
    point = None
    if lat is not None and lon is not None:
        try:
            lat, lon = round(float(lat), HOME_PRECISION), round(float(lon), HOME_PRECISION)
        except (TypeError, ValueError) as exc:
            raise HomeError("That location could not be read.") from exc
        point = Point(lon, lat, srid=4326)
        region = region_for_point(lon, lat)
    if region_id:
        chosen = Region.objects.filter(pk=region_id, level=Region.Level.SUB_COUNTY).first()
        if chosen is None:
            raise HomeError("Choose a sub-county from the list.")
        region = chosen
    if region is None:
        raise HomeError(
            "That place is not inside a Kenyan sub-county FloodScout covers. Choose your "
            "sub-county from the list instead."
        )
    user.home_address = (address or "").strip()[:255]
    user.home_point = point
    user.home_region = region
    user.save(update_fields=["home_address", "home_point", "home_region"])
    sync_home_subscription(user)
    return user


def clear_home(user: User) -> None:
    user.home_address, user.home_point, user.home_region = "", None, None
    user.save(update_fields=["home_address", "home_point", "home_region"])
    sync_home_subscription(user)


def alert_regions(region: Region | None) -> list[Region]:
    """A sub-county and its county: alerts are issued for both."""
    if region is None:
        return []
    places = [region]
    if region.parent_id:
        places.append(region.parent)
    return places


def home_places(user: User) -> list[Region]:
    """The sub-county and its county."""
    return alert_regions(user.home_region)


def verified_email(user: User) -> str:
    """The email the alerts may go to: the account's, once verified."""
    return user.email if user.email and user.email_verified else ""


def sync_subscription(
    user: User, existing: Subscriber | None, places: list[Region], *, on: bool, level: str, how: str, **identity
) -> Subscriber | None:
    """Make one automatic subscription match what the profile says; None when there is none.

    Shared by the home and the saved places (accounts/places.py). `identity`
    marks a new row as the one it is (is_home=True, or saved_place=...).
    A newly confirmed subscription, or one whose places changed, is sent
    whatever alert is already in force there, so someone who saves a place
    during a flood hears of it.
    """
    if not places or not on:
        if existing:
            stopping.stop(existing, how)
        return None

    email = verified_email(user)
    subscriber = existing or Subscriber(user=user, **identity)
    was_confirmed = bool(existing and existing.confirmed)
    subscriber.email, subscriber.phone = email, ""
    subscriber.min_level = level
    subscriber.confirmed = bool(email)
    if subscriber.confirmed and not subscriber.confirmed_at:
        subscriber.confirmed_at = timezone.now()
    subscriber.save()
    old = set(subscriber.regions.values_list("pk", flat=True))
    subscriber.regions.set(places)

    if subscriber.confirmed and (not was_confirmed or old != {p.pk for p in places}):
        # Imported here: the alerts engine imports from riskengine, which is
        # heavy, and accounts is loaded early.
        from alerts.engine import deliver
        from alerts.models import Alert

        for alert in Alert.objects.filter(status=Alert.Status.ACTIVE, region__in=places):
            if subscriber.wants(alert.level):
                deliver(alert, subscriber)
    return subscriber


def sync_home_subscription(user: User) -> Subscriber | None:
    """Make the user's home subscription, and their saved places', match their profile.

    Called wherever the home or the contact details change, so a newly
    verified email starts the saved places' alerts too.
    """
    from .places import sync_places

    subscriber = sync_subscription(
        user,
        Subscriber.objects.filter(user=user, is_home=True).first(),
        home_places(user),
        on=user.home_alerts,
        level=user.home_alert_level,
        how=Unsubscription.How.HOME_OFF,
        is_home=True,
    )
    sync_places(user)
    return subscriber


def home_summary(user: User) -> dict:
    """The home as the profile page shows it."""
    region = user.home_region
    sub = Subscriber.objects.filter(user=user, is_home=True).first()
    email = verified_email(user)
    return {
        "address": user.home_address,
        "lat": user.home_point.y if user.home_point else None,
        "lon": user.home_point.x if user.home_point else None,
        "region": region.pk if region else None,
        "region_name": region.name if region else None,
        "county": region.parent_id if region else None,
        "county_name": region.county_name if region else None,
        "alerts_on": user.home_alerts,
        "alert_level": user.home_alert_level,
        "receiving": bool(sub and sub.confirmed),
        "by_email": email,
        "email_waiting": bool(user.email and not user.email_verified),
    }
