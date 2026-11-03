"""How a message reaches a person: email, and SMS through Africa's Talking.

Email goes through Django's mail settings. With no SMTP server configured,
messages are written as files under data/outbox/ (see settings.py), so the
whole flow can be shown and checked without sending anything.

Alerts go by email only. SMS is used just to confirm the phone number on a
profile (accounts/verification.py), through Africa's Talking, the SMS
gateway most Kenyan services use.
Its REST call, as its own SDK makes it:

    POST https://api.africastalking.com/version1/messaging
         (https://api.sandbox.africastalking.com/... for the sandbox)
    headers  apiKey: <key>, Accept: application/json
    form     username, to, message, and from when a sender ID is set

With AT_USERNAME "sandbox" the message appears in the Africa's Talking
simulator instead of on a phone, which is how it is demonstrated. With no
AT_API_KEY, SMS is skipped and the reason recorded; it never fails a run.
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache
from django.core.mail import send_mail

SMS_TIMEOUT_SECONDS = 30
SMS_MAX_CHARS = 160

# Confirmation codes one email or phone can be sent in a day, by every route
# (alert sign-up, the profile's "Get alerts", confirming a contact). The
# per-address rate limits stop one sender; this stops many senders, or one
# with many addresses, from flooding a stranger's inbox or running up SMS.
CODES_PER_DAY = 5
CODE_DAY_SECONDS = 24 * 60 * 60


@dataclass
class Result:
    outcome: str  # "sent", "failed" or "skipped"
    detail: str = ""


class PhoneError(ValueError):
    """Not a Kenyan mobile number."""


def normalise_phone(raw: str) -> str:
    """A Kenyan mobile number as +2547XXXXXXXX or +2541XXXXXXXX.

    Accepts the ways people write them: 0712 345 678, 712345678,
    254712345678, +254 712-345-678.
    """
    digits = re.sub(r"[^\d+]", "", raw or "")
    digits = digits.removeprefix("+")
    if digits.startswith("254"):
        digits = digits[3:]
    elif digits.startswith("0"):
        digits = digits[1:]
    if not re.fullmatch(r"[17]\d{8}", digits):
        raise PhoneError("Enter a Kenyan mobile number, for example 0712 345 678.")
    return f"+254{digits}"


def take_code_allowance(to: str) -> bool:
    """Count one code to `to`; False once it has had CODES_PER_DAY in a day."""
    key = "codes-sent:" + hashlib.sha256(to.strip().lower().encode()).hexdigest()
    cache.add(key, 0, CODE_DAY_SECONDS)
    try:
        sent = cache.incr(key)
    except ValueError:  # expired between add and incr
        cache.set(key, 1, CODE_DAY_SECONDS)
        sent = 1
    return sent <= CODES_PER_DAY


def send_email(to: str, subject: str, body: str) -> Result:
    try:
        sent = send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [to], fail_silently=False)
    except Exception as exc:  # noqa: BLE001  (any mail backend error is a failed delivery)
        return Result("failed", str(exc)[:300])
    return Result("sent" if sent else "failed", "" if sent else "The mail backend sent nothing.")


def sms_configured() -> bool:
    return bool(settings.AT_API_KEY and settings.AT_USERNAME)


def sms_url() -> str:
    domain = (
        "sandbox.africastalking.com" if settings.AT_USERNAME == "sandbox" else "africastalking.com"
    )
    return f"https://api.{domain}/version1/messaging"


def send_sms(to: str, message: str) -> Result:
    if not sms_configured():
        return Result("skipped", "SMS is not set up: set AT_USERNAME and AT_API_KEY.")
    data = {"username": settings.AT_USERNAME, "to": to, "message": message[:SMS_MAX_CHARS]}
    if settings.AT_SENDER_ID:
        data["from"] = settings.AT_SENDER_ID
    request = urllib.request.Request(
        sms_url(),
        data=urllib.parse.urlencode(data).encode(),
        headers={"apiKey": settings.AT_API_KEY, "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=SMS_TIMEOUT_SECONDS) as response:
            body = json.loads(response.read() or b"{}")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return Result("failed", f"Africa's Talking: {exc}"[:300])

    recipients = (body.get("SMSMessageData") or {}).get("Recipients") or []
    if not recipients:
        message = (body.get("SMSMessageData") or {}).get("Message", "no recipients")
        return Result("failed", f"Africa's Talking: {message}"[:300])
    first = recipients[0]
    # 100 Processed, 101 Sent, 102 Queued are successes; anything else is not.
    ok = first.get("statusCode") in (100, 101, 102)
    return Result(
        "sent" if ok else "failed", f"{first.get('status')} {first.get('messageId', '')}"[:300]
    )
