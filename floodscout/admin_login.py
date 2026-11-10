"""The Django admin's sign-in, with the same limits as the API's.

DRF's rate limits do not reach the admin, so without this a superuser's
password could be guessed as fast as requests can be sent. Failures count
per client address and per account; the per-account count is the one the
API's sign-in uses (accounts/views.py), so guesses split between the two
doors still add up.
"""

from django.contrib import admin
from django.core.cache import cache
from django.http import HttpResponse
from rest_framework.throttling import BaseThrottle

from accounts.views import ObtainTokenView, _failures_key

WINDOW = ObtainTokenView.FAILURE_WINDOW
MAX_FAILURES = ObtainTokenView.MAX_FAILURES


def _address_key(request) -> str:
    # get_ident honours REST_FRAMEWORK["NUM_PROXIES"], like the API's limits.
    return f"admin-signin-failures:{BaseThrottle().get_ident(request)}"


def _count(key: str) -> None:
    cache.add(key, 0, WINDOW)
    cache.incr(key)


def limited_login(request, extra_context=None):
    if request.method != "POST":
        return admin.site.login(request, extra_context)
    keys = [_address_key(request), _failures_key(str(request.POST.get("username") or ""))]
    if any(cache.get(key, 0) >= MAX_FAILURES for key in keys):
        return HttpResponse(
            "Too many wrong passwords. Try again in 15 minutes.", status=429, content_type="text/plain"
        )
    response = admin.site.login(request, extra_context)
    # A good sign-in redirects; a bad one shows the form again.
    if response.status_code == 200:
        for key in keys:
            _count(key)
    else:
        cache.delete(keys[1])
    return response
