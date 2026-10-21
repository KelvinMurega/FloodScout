"""Write one line of the activity log (accounts.models.ActivityLog)."""

from __future__ import annotations

from .models import ActivityLog

# How many lines a list on the profile page shows.
PAGE = 50


def client_ip(request) -> str | None:
    """The address the request came from. Behind no proxy, REMOTE_ADDR."""
    return request.META.get("REMOTE_ADDR") if request is not None else None


def record(request, action: str, user=None, **detail) -> ActivityLog:
    """Log `action` by `user` (default: who made `request`), with `detail`."""
    if user is None and request is not None and request.user.is_authenticated:
        user = request.user
    return ActivityLog.objects.create(
        user=user, action=action, detail=detail, ip=client_ip(request)
    )
