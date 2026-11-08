"""Confirm that an account's email (or phone) really reaches its owner.

A six-digit code is sent:

  - when an account is made with an email (registration);
  - when someone signs in while their email is still unconfirmed;
  - when the email or phone on the profile changes;
  - when they ask for it again from the profile.

Entering it sets User.email_verified (or phone_verified). From then on
alerts reach that contact without any further code, including the warnings
for where they live (accounts/home.py). An email Google or GitHub vouch for
is verified at sign-in and gets no code at all.

A new code is not sent more than once a minute (or five times a day to one
address, alerts.channels.CODES_PER_DAY), a code lasts an hour, and five
wrong tries void it; asking for a new code does not reset them.
"""

from __future__ import annotations

import datetime as dt

from django.conf import settings
from django.utils import timezone

from alerts import channels
from alerts.models import new_code

from .models import ContactCode, User

CODE_LIFETIME = dt.timedelta(hours=1)
RESEND_AFTER = dt.timedelta(minutes=1)
MAX_ATTEMPTS = 5


class VerificationError(Exception):
    """Shown to the person as it is."""


def destination(user: User, channel: str) -> str:
    return user.email if channel == ContactCode.Channel.EMAIL else user.phone


def is_verified(user: User, channel: str) -> bool:
    return user.email_verified if channel == ContactCode.Channel.EMAIL else user.phone_verified


def send_code(user: User, channel: str = ContactCode.Channel.EMAIL, *, force: bool = False) -> str:
    """Send a code to the user's email or phone; "sent", "recent" or "verified".

    "recent" means one went out less than a minute ago and was not sent
    again (unless `force` and the destination changed).
    """
    to = destination(user, channel)
    if not to:
        raise VerificationError(
            "Add an email to your profile first."
            if channel == ContactCode.Channel.EMAIL
            else "Add a phone number to your profile first."
        )
    if is_verified(user, channel):
        return "verified"
    if channel == ContactCode.Channel.PHONE and not channels.sms_configured():
        raise VerificationError("Text messages are not set up on this server yet.")

    existing = ContactCode.objects.filter(user=user, channel=channel).first()
    if (
        existing
        and existing.destination == to
        and timezone.now() - existing.created_at < RESEND_AFTER
        and not force
    ):
        return "recent"
    if not channels.take_code_allowance(to):
        raise VerificationError(
            "This email or phone has been sent all the codes it can get today. Try again tomorrow."
        )
    # A new code to the same place keeps the wrong tries already used, so
    # asking again does not buy five more guesses. A used-up code starts
    # afresh; the daily limit above bounds the guesses to 25 a day.
    attempts = 0
    if existing and existing.destination == to and existing.attempts < MAX_ATTEMPTS:
        attempts = existing.attempts
    if existing:
        existing.delete()
    code = ContactCode.objects.create(
        user=user, channel=channel, destination=to, code=new_code(), attempts=attempts
    )

    if channel == ContactCode.Channel.EMAIL:
        result = channels.send_email(
            to,
            f"Your FloodScout code: {code.code}",
            f"Hello {user.first_name or user.username},\n\n"
            f"Your FloodScout confirmation code is {code.code}.\n\n"
            "Enter it on FloodScout to confirm this email. Flood warnings for where you live "
            "will then come to this address without you having to ask. The code works for an "
            "hour.\n\nIf you did not make a FloodScout account, ignore this email.\n\n"
            f"{settings.EMAIL_DASHBOARD_URL.rstrip('/')}/#/profile",
        )
    else:
        result = channels.send_sms(
            to,
            f"Your FloodScout code is {code.code}. Enter it on FloodScout to confirm this number.",
        )
    if result.outcome != "sent":
        code.delete()
        raise VerificationError("The code could not be sent. Check the address or number.")
    return "sent"


def confirm(user: User, channel: str, entered: str) -> None:
    """Check `entered` against the code sent; mark the contact verified."""
    code = ContactCode.objects.filter(user=user, channel=channel).first()
    if code is None:
        raise VerificationError("There is no code waiting. Ask for a new one.")
    if code.destination != destination(user, channel):
        code.delete()
        raise VerificationError("Your email or phone changed since this code. Ask for a new one.")
    if timezone.now() - code.created_at > CODE_LIFETIME or code.attempts >= MAX_ATTEMPTS:
        code.delete()
        raise VerificationError("This code has expired. Ask for a new one.")
    if (entered or "").strip() != code.code:
        code.attempts += 1
        code.save(update_fields=["attempts"])
        raise VerificationError("That code is not right.")
    code.delete()
    field = "email_verified" if channel == ContactCode.Channel.EMAIL else "phone_verified"
    setattr(user, field, True)
    user.save(update_fields=[field])
