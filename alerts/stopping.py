"""Stopping someone's alerts, the one way every path does it.

Three things stop alerts: the unsubscribe link in a message, removing a
subscription on the profile, and turning off warnings for where one lives.
Each deletes the subscription, as the unsubscribe page promises. Before it
goes, `stop` keeps a small Unsubscription record for the administrator (who,
with the contact mostly hidden, which places, how many alerts had arrived)
and keeps the subscription's deliveries with the link to it removed, so the
totals of what was sent stay true.

Kept this small on purpose (Kenya's Data Protection Act, 2019: no more than
the purpose needs). The record cannot be used to message the person again.
"""

from __future__ import annotations

from django.apps import apps
from django.contrib.auth import get_user_model

from .models import Delivery, Subscriber, Unsubscription


def mask_email(email: str) -> str:
    """kevomurega@gmail.com -> k***@gmail.com."""
    local, at, domain = email.partition("@")
    if not at:
        return "***"
    return f"{local[:1]}***@{domain}"


def mask_phone(phone: str) -> str:
    """+254712345678 -> +2547****678."""
    if len(phone) <= 8:
        return "****"
    return f"{phone[:5]}****{phone[-3:]}"


def masked_contact(subscriber: Subscriber) -> str:
    parts = []
    if subscriber.email:
        parts.append(mask_email(subscriber.email))
    if subscriber.phone:
        parts.append(mask_phone(subscriber.phone))
    return ", ".join(parts)


def stop(subscriber: Subscriber, how: str) -> Unsubscription:
    """Record that `subscriber` stopped their alerts, keep the delivery totals, and delete them.

    When the person stops an automatic subscription themselves (by the link
    in an email, or on their profile), the profile remembers it, or the next
    sync would bring it back: home warnings or the saved place's warnings are
    turned off. A stop made by the sync itself (HOME_OFF, PLACE_OFF) already
    follows the profile.
    """
    if how not in (Unsubscription.How.HOME_OFF, Unsubscription.How.PLACE_OFF):
        if subscriber.is_home and subscriber.user_id:
            get_user_model().objects.filter(pk=subscriber.user_id).update(home_alerts=False)
        if subscriber.saved_place_id:
            apps.get_model("accounts", "SavedPlace").objects.filter(
                pk=subscriber.saved_place_id
            ).update(alerts_on=False)
    deliveries = Delivery.objects.filter(subscriber=subscriber)
    received = deliveries.filter(kind="alert", outcome=Delivery.Outcome.SENT).count()
    record = Unsubscription.objects.create(
        how=how,
        user=subscriber.user,
        username=subscriber.user.username if subscriber.user else "",
        contact=masked_contact(subscriber),
        places=[str(region) for region in subscriber.regions.all()],
        alerts_received=received,
        subscribed_at=subscriber.confirmed_at or subscriber.created_at,
    )
    deliveries.update(subscriber=None, detail="")
    subscriber.delete()
    return record
