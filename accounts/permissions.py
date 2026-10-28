from rest_framework.permissions import BasePermission


class IsAdministrator(BasePermission):
    """Allows only the System Administrator actor (Use Case Diagram, 4.3.1).

    An anonymous request is refused with 401 and a signed-in End User with
    403; DRF picks the status from whether the request was authenticated.
    """

    message = "Only a System Administrator can do this."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_administrator())


class CanSeeAnalytics(BasePermission):
    """System Administrators and Analysts: reading the model and system analytics."""

    message = "Only an Analyst or a System Administrator can see this."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.can_see_analytics())
