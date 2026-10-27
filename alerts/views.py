"""The alerts API.

Open to everyone:
  GET  alerts/                    active alerts (?status=all|ended, ?region=, ?level=)
  GET  alerts/<id>/               one alert
  GET  alerts/<id>/cap/           it as a CAP 1.2 message
  GET  alerts/feed/               an Atom feed of the active alerts, linking to CAP
  POST alerts/subscribe/          ask for alerts; a code is sent to confirm
  POST alerts/confirm/            enter the code; alerts start
  GET  alerts/unsubscribe/<token>/  asks; POST stops, and deletes what was kept

System Administrator only:
  POST alerts/issue/              issue an alert by hand
  POST alerts/<id>/cancel/        cancel one, telling those who were told
  POST alerts/run/                run the rules now
  POST alerts/test/               send a test message to yourself
  GET  alerts/stats/              counts for the administrator's page
"""

from __future__ import annotations

import datetime as dt

from django.db.models import Count
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.html import escape
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes, throttle_classes
from rest_framework.exceptions import Throttled, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from accounts.activity import record
from accounts.models import ActivityLog
from accounts.permissions import IsAdministrator
from geodata.models import Region

from . import cap, channels, engine
from .models import LEVEL_RANK, Alert, AlertRun, Delivery, Level, Subscriber, new_code

MAX_CONFIRM_ATTEMPTS = 5
CODE_LIFETIME = dt.timedelta(hours=1)
MAX_REGIONS = 10
CODES_USED_UP = (
    "This email or phone has been sent all the codes it can get today. Try again tomorrow."
)


class AlertSerializer(serializers.ModelSerializer):
    region_name = serializers.CharField(source="region.name", read_only=True)
    county_name = serializers.CharField(source="region.county_name", read_only=True)
    region_level = serializers.CharField(source="region.level", read_only=True)
    level_label = serializers.CharField(source="get_level_display", read_only=True)
    issued_by = serializers.CharField(source="issued_by.username", default=None, read_only=True)
    point = serializers.SerializerMethodField()

    class Meta:
        model = Alert
        fields = [
            "id",
            "identifier",
            "region",
            "region_name",
            "county_name",
            "region_level",
            "level",
            "level_label",
            "status",
            "basis",
            "headline",
            "description",
            "instruction",
            "reasons",
            "times_usual",
            "onset",
            "expires",
            "version",
            "issued_at",
            "updated_at",
            "ended_at",
            "issued_by",
            "point",
        ]

    def get_point(self, alert) -> list[float]:
        """[lat, lon] of a point inside the region, for a marker."""
        point = alert.region.geom.point_on_surface
        return [round(point.y, 4), round(point.x, 4)]


def _sorted(alerts):
    """Most severe first, then newest."""
    return sorted(alerts, key=lambda a: (-LEVEL_RANK[a.level], -a.issued_at.timestamp()))


class AlertViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = AlertSerializer

    def get_queryset(self):
        qs = Alert.objects.select_related("region", "issued_by")
        # One alert is found whatever its status; lists default to the active.
        if self.action != "list":
            return qs
        params = self.request.query_params
        wanted = params.get("status", "active")
        if wanted == "active":
            qs = qs.filter(status=Alert.Status.ACTIVE)
        elif wanted == "ended":
            qs = qs.exclude(status=Alert.Status.ACTIVE)
        elif wanted != "all":
            raise ValidationError({"status": "Expected active, ended or all."})
        region = params.get("region")
        if region:
            if not region.isdigit():
                raise ValidationError({"region": "Expected a region id."})
            qs = qs.filter(region_id=int(region))
        level = params.get("level")
        if level:
            if level not in Level.values:
                raise ValidationError({"level": f"Expected one of {Level.values}."})
            qs = qs.filter(level=level)
        return qs

    def list(self, request, *args, **kwargs):
        alerts = _sorted(self.get_queryset())
        return Response(self.get_serializer(alerts, many=True).data)

    def _web(self, alert) -> str:
        return engine.alert_link(alert)

    @action(detail=True, methods=["get"], url_path="cap")
    def cap_message(self, request, pk=None):
        alert = self.get_object()
        return HttpResponse(
            cap.cap_xml(alert, self._web(alert)), content_type="application/cap+xml"
        )

    @action(detail=False, methods=["get"])
    def feed(self, request):
        alerts = _sorted(Alert.objects.filter(status=Alert.Status.ACTIVE).select_related("region"))
        body = cap.atom_feed(
            alerts,
            request.build_absolute_uri(),
            lambda a: request.build_absolute_uri(f"/api/alerts/{a.id}/cap/"),
        )
        return HttpResponse(body, content_type="application/atom+xml")

    @action(detail=False, methods=["post"], permission_classes=[IsAdministrator])
    def issue(self, request):
        region = _region(request.data.get("region"))
        level = request.data.get("level")
        if level not in Level.values:
            raise ValidationError({"level": f"Expected one of {Level.values}."})
        description = str(request.data.get("description") or "").strip()
        if len(description) < 10:
            raise ValidationError({"description": "Say what is happening, in a sentence or two."})
        alert = engine.issue_manual(
            region, level, description, request.user, str(request.data.get("instruction") or "")
        )
        record(request, ActivityLog.Action.ALERT_ISSUE, headline=alert.headline, alert=alert.id)
        return Response(self.get_serializer(alert).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], permission_classes=[IsAdministrator])
    def cancel(self, request, pk=None):
        alert = self.get_object()
        if alert.status != Alert.Status.ACTIVE:
            raise ValidationError({"detail": "Only an active alert can be cancelled."})
        sent = engine.cancel(alert)
        record(request, ActivityLog.Action.ALERT_CANCEL, headline=alert.headline, alert=alert.id)
        return Response({**self.get_serializer(alert).data, "messages_sent": sent.sent})

    @action(detail=False, methods=["post"], permission_classes=[IsAdministrator])
    def run(self, request):
        result = engine.run_alerts()
        record(
            request,
            ActivityLog.Action.ALERT_RUN,
            issued=result.issued,
            upgraded=result.upgraded,
            ended=result.ended,
        )
        return Response(_run_dict(result))

    @action(detail=False, methods=["post"], permission_classes=[IsAdministrator])
    def test(self, request):
        """Send a test message to an address or number the administrator gives."""
        email = str(request.data.get("email") or "").strip()
        phone = str(request.data.get("phone") or "").strip()
        if not email and not phone:
            raise ValidationError({"detail": "Give an email address or a phone number."})
        results = {}
        if email:
            results["email"] = vars(
                channels.send_email(
                    email,
                    "FloodScout test message",
                    "This is a test of FloodScout's alert email. Nothing is wrong.",
                )
            )
        if phone:
            try:
                number = channels.normalise_phone(phone)
            except channels.PhoneError as exc:
                raise ValidationError({"phone": str(exc)}) from exc
            results["sms"] = vars(
                channels.send_sms(number, "FloodScout test message. Nothing is wrong.")
            )
        record(request, ActivityLog.Action.ALERT_TEST, channels=list(results))
        return Response(results)

    @action(detail=False, methods=["get"], permission_classes=[IsAdministrator])
    def stats(self, request):
        since = timezone.now() - dt.timedelta(days=7)
        active = dict(
            Alert.objects.filter(status=Alert.Status.ACTIVE)
            .values_list("level")
            .annotate(n=Count("id"))
        )
        deliveries = [
            {"channel": row["channel"], "outcome": row["outcome"], "count": row["n"]}
            for row in Delivery.objects.filter(sent_at__gte=since)
            .values("channel", "outcome")
            .annotate(n=Count("id"))
            .order_by("channel", "outcome")
        ]
        last = AlertRun.objects.first()
        return Response(
            {
                "active": {level: active.get(level, 0) for level in Level.values},
                "subscribers": {
                    "confirmed": Subscriber.objects.filter(confirmed=True).count(),
                    "pending": Subscriber.objects.filter(confirmed=False).count(),
                    "with_email": Subscriber.objects.filter(confirmed=True)
                    .exclude(email="")
                    .count(),
                    "with_phone": Subscriber.objects.filter(confirmed=True)
                    .exclude(phone="")
                    .count(),
                },
                "deliveries_7d": deliveries,
                "sms_configured": channels.sms_configured(),
                "sms_sandbox": channels.sms_configured()
                and channels.sms_url().count("sandbox") > 0,
                "last_run": _run_dict(last) if last else None,
            }
        )


def _run_dict(run: AlertRun) -> dict:
    return {
        "started_at": run.started_at,
        "scores_for": run.scores_for,
        "issued": run.issued,
        "upgraded": run.upgraded,
        "ended": run.ended,
        "messages_sent": run.messages_sent,
        "messages_failed": run.messages_failed,
        "note": run.note,
    }


def _region(value) -> Region:
    if not str(value or "").isdigit():
        raise ValidationError({"region": "Expected a region id."})
    return get_object_or_404(Region, pk=int(value))


class SubscribeSerializer(serializers.Serializer):
    email = serializers.EmailField(required=False, allow_blank=True)
    phone = serializers.CharField(required=False, allow_blank=True, max_length=24)
    regions = serializers.ListField(
        child=serializers.IntegerField(), min_length=1, max_length=MAX_REGIONS
    )
    min_level = serializers.ChoiceField(choices=Level.choices, default=Level.WATCH)

    def validate_phone(self, value):
        if not value:
            return ""
        try:
            return channels.normalise_phone(value)
        except channels.PhoneError as exc:
            raise serializers.ValidationError(str(exc)) from exc

    def validate(self, data):
        if not data.get("email") and not data.get("phone"):
            raise serializers.ValidationError("Give an email address, a phone number, or both.")
        if data.get("phone") and not data.get("email") and not channels.sms_configured():
            raise serializers.ValidationError(
                "Text messages are not set up on this server yet, so the code cannot be sent "
                "to a phone. Give an email address as well."
            )
        found = Region.objects.filter(pk__in=data["regions"])
        if found.count() != len(set(data["regions"])):
            raise serializers.ValidationError({"regions": "One of these places does not exist."})
        data["region_objects"] = list(found)
        return data


def _send_code(subscriber: Subscriber) -> list[str]:
    """Send the confirmation code by every channel given; the channels it went by.

    Raises Throttled, sending nothing, when the email or phone has already
    had its codes for the day.
    """
    for to in (subscriber.email, subscriber.phone):
        if to and not channels.take_code_allowance(to):
            raise Throttled(detail=CODES_USED_UP)
    sent = []
    if subscriber.email:
        result = channels.send_email(
            subscriber.email,
            f"Your FloodScout code: {subscriber.confirm_code}",
            f"Your code is {subscriber.confirm_code}. Enter it on the FloodScout page to start "
            "receiving flood alerts. It works for an hour.\n\nIf you did not ask for this, "
            "ignore this message and nothing will be sent to you.",
        )
        if result.outcome == "sent":
            sent.append("email")
    if subscriber.phone:
        result = channels.send_sms(
            subscriber.phone,
            f"Your FloodScout code is {subscriber.confirm_code}. If you did not ask for flood "
            "alerts, ignore this.",
        )
        if result.outcome == "sent":
            sent.append("sms")
    return sent


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([ScopedRateThrottle])
def subscribe(request):
    data = SubscribeSerializer(data=request.data)
    data.is_valid(raise_exception=True)
    values = data.validated_data
    subscriber = Subscriber.objects.create(
        email=values.get("email", ""),
        phone=values.get("phone", ""),
        min_level=values["min_level"],
        user=request.user if request.user.is_authenticated else None,
    )
    subscriber.regions.set(values["region_objects"])
    try:
        sent = _send_code(subscriber)
    except Throttled:
        subscriber.delete()
        raise
    if not sent:
        subscriber.delete()
        return Response(
            {"detail": "The code could not be sent. Check the address or number and try again."},
            status=status.HTTP_502_BAD_GATEWAY,
        )
    return Response(
        {"subscription": subscriber.token, "code_sent_by": sent},
        status=status.HTTP_201_CREATED,
    )


subscribe.cls.throttle_scope = "alerts"


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([ScopedRateThrottle])
def confirm(request):
    token = str(request.data.get("subscription") or "")
    code = str(request.data.get("code") or "").strip()
    subscriber = Subscriber.objects.filter(token=token).first()
    if subscriber is None:
        raise ValidationError({"subscription": "This sign-up was not found. Start again."})
    if subscriber.confirmed:
        return Response(_subscriber_summary(subscriber))
    if (
        subscriber.confirm_attempts >= MAX_CONFIRM_ATTEMPTS
        or timezone.now() - subscriber.created_at > CODE_LIFETIME
    ):
        subscriber.delete()
        raise ValidationError({"code": "This code has expired. Start again."})
    if code != subscriber.confirm_code:
        subscriber.confirm_attempts += 1
        subscriber.save(update_fields=["confirm_attempts"])
        raise ValidationError({"code": "That code is not right."})
    subscriber.confirmed = True
    subscriber.confirmed_at = timezone.now()
    subscriber.save(update_fields=["confirmed", "confirmed_at"])
    _mark_verified(subscriber)
    # Anything already active for their places is sent straight away.
    _send_active(subscriber, subscriber.regions.all())
    return Response(_subscriber_summary(subscriber))


confirm.cls.throttle_scope = "alerts"


class MeSubscribeSerializer(serializers.Serializer):
    regions = serializers.ListField(
        child=serializers.IntegerField(), min_length=1, max_length=MAX_REGIONS
    )
    min_level = serializers.ChoiceField(choices=Level.choices, required=False)


def _mark_verified(subscriber: Subscriber) -> None:
    """A confirmed code proves the account's email and phone reach its owner."""
    user = subscriber.user
    if user is None:
        return
    fields = []
    if (
        subscriber.email
        and subscriber.email.lower() == user.email.lower()
        and not user.email_verified
    ):
        user.email_verified = True
        fields.append("email_verified")
    if subscriber.phone and subscriber.phone == user.phone and not user.phone_verified:
        user.phone_verified = True
        fields.append("phone_verified")
    if fields:
        user.save(update_fields=fields)
        from accounts.home import sync_home_subscription  # accounts imports alerts

        sync_home_subscription(user)


def _send_active(subscriber: Subscriber, regions) -> int:
    """Send what is already in force for `regions`; how many alerts that was."""
    sent = 0
    for alert in Alert.objects.filter(status=Alert.Status.ACTIVE, region__in=regions):
        if subscriber.wants(alert.level):
            engine.deliver(alert, subscriber)
            sent += 1
    return sent


@api_view(["POST"])
@permission_classes([IsAuthenticated])
@throttle_classes([ScopedRateThrottle])
def subscribe_me(request):
    """A signed-in user's "Get alerts": their profile's email and phone, no form.

    The account has one subscription, and each press adds places to it.
    When its email and phone are verified (by Google or GitHub, or by an
    earlier code) it starts at once; otherwise a code goes to them first,
    once, and confirming it marks them verified for every later press. SMS
    is used only once it is set up on the server.
    """
    data = MeSubscribeSerializer(data=request.data)
    data.is_valid(raise_exception=True)
    wanted = data.validated_data
    regions = list(Region.objects.filter(pk__in=wanted["regions"]))
    if len(regions) != len(set(wanted["regions"])):
        raise ValidationError({"regions": "One of these places does not exist."})

    user = request.user
    email = user.email
    phone = user.phone if channels.sms_configured() else ""
    if not email and not phone:
        message = (
            "Text messages are not set up on this server yet. Add an email to your profile."
            if user.phone
            else "Add an email or a phone number to your profile first."
        )
        raise ValidationError({"detail": message})

    subscriber = (
        Subscriber.objects.filter(user=user, is_home=False)
        .order_by("-confirmed", "-created_at")
        .first()
    )
    if subscriber is None:
        subscriber = Subscriber(user=user)
    contacts_changed = subscriber.email != email or subscriber.phone != phone
    subscriber.email, subscriber.phone = email, phone
    if "min_level" in wanted:
        subscriber.min_level = wanted["min_level"]
    trusted = (not email or user.email_verified) and (not phone or user.phone_verified)
    if trusted:
        if not subscriber.confirmed:
            subscriber.confirmed, subscriber.confirmed_at = True, timezone.now()
    elif contacts_changed or not subscriber.confirmed:
        subscriber.confirmed = False
        subscriber.confirm_code = new_code()
        subscriber.confirm_attempts = 0
        if subscriber.pk:
            subscriber.created_at = timezone.now()  # the code's hour starts now
    subscriber.save()
    subscriber.regions.add(*regions)

    if subscriber.confirmed:
        sent = _send_active(subscriber, regions)
        return Response({**_subscriber_summary(subscriber), "alerts_sent_now": sent})

    sent_by = _send_code(subscriber)
    if not sent_by:
        return Response(
            {"detail": "The code could not be sent. Check the email and phone on your profile."},
            status=status.HTTP_502_BAD_GATEWAY,
        )
    return Response(
        {"confirmed": False, "subscription": subscriber.token, "code_sent_by": sent_by},
        status=status.HTTP_201_CREATED,
    )


subscribe_me.cls.throttle_scope = "alerts"


def _subscriber_summary(subscriber: Subscriber) -> dict:
    return {
        "confirmed": subscriber.confirmed,
        "email": subscriber.email,
        "phone": subscriber.phone,
        "min_level": subscriber.min_level,
        "regions": [str(region) for region in subscriber.regions.all()],
        "unsubscribe": engine.unsubscribe_link(subscriber),
    }


def _page(body: str) -> HttpResponse:
    return HttpResponse(
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<meta name='robots' content='noindex'>"
        "<title>FloodScout alerts</title></head>"
        "<body style='font-family:system-ui,sans-serif;max-width:36rem;margin:3rem auto;padding:0 1rem'>"
        f"<h1>FloodScout alerts</h1>{body}</body></html>"
    )


@api_view(["GET", "POST"])
@permission_classes([AllowAny])
def unsubscribe(request, token: str):
    """Stop the alerts and delete the subscription. A plain page, for an email link.

    GET only asks; the button POSTs. Mail scanners and link previews open
    every link in an email, and must not stop someone's flood warnings.
    """
    subscriber = Subscriber.objects.filter(token=token).first()
    if subscriber is None:
        return _page(
            "<p>This link has already been used, or the subscription no longer exists.</p>"
        )
    who = escape(subscriber.email or subscriber.phone)
    if request.method == "GET":
        return _page(
            f"<p>Stop all FloodScout alerts to {who}? What was kept for them will be "
            "deleted.</p><form method='post'>"
            "<button type='submit' style='font:inherit;padding:.5rem 1rem'>"
            "Stop the alerts</button></form>"
        )
    subscriber.delete()
    return _page(f"<p>{who} will get no more FloodScout alerts. What was kept has been deleted.</p>")
