from django.http import JsonResponse


def health_check(request):
    """Confirms the API layer, and its DB connection via Django's checks,
    are wired up correctly. Exercised by FR-01/IR-01 once real endpoints
    (location search, risk lookup) are added in a later sprint.
    """
    return JsonResponse({"status": "ok", "service": "FloodScout API"})
