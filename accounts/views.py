from django.contrib.auth.models import update_last_login
from django.db import transaction
from django.db.models import Count, Q
from rest_framework import mixins, status, viewsets
from rest_framework.authtoken.models import Token
from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.exceptions import ValidationError
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from alerts.models import Subscriber

from .activity import PAGE, record
from .models import ActivityLog, User
from .permissions import IsAdministrator
from .serializers import (
    ActivitySerializer,
    MeSerializer,
    PasswordSerializer,
    RegisterSerializer,
    UserAdminSerializer,
)


def signed_in(user, token) -> dict:
    """What the dashboard is told about the person who just signed in."""
    return {
        "token": token.key,
        "username": user.get_username(),
        "first_name": user.first_name,
        "last_name": user.last_name,
        "email": user.email,
        "role": user.role,
        "is_administrator": user.is_administrator(),
    }


class ObtainTokenView(ObtainAuthToken):
    """POST a username and password, get back a token and the user's role.

    The role is returned so the dashboard knows whether to offer the
    administrator's actions. It is a convenience only: every protected
    endpoint checks the role itself (accounts/permissions.py).
    """

    # Slows password guessing; the rate is DEFAULT_THROTTLE_RATES["login"].
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request, *args, **kwargs):
        serializer = self.serializer_class(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        token, _created = Token.objects.get_or_create(user=user)
        update_last_login(None, user)
        record(request, ActivityLog.Action.SIGN_IN, user=user)
        return Response(signed_in(user, token))


class RevokeTokenView(APIView):
    """POST with a token to delete it, so signing out ends it on the server too."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        Token.objects.filter(user=request.user).delete()
        record(request, ActivityLog.Action.SIGN_OUT)
        return Response(status=status.HTTP_204_NO_CONTENT)


class RegisterView(APIView):
    """Create an End User account and sign in to it.

    Only ever an End User: a System Administrator role is given by another
    administrator. Throttled like sign-in.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        data = RegisterSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        user = User.objects.create_user(
            values["username"], email=values.get("email", ""), password=values["password"]
        )
        token = Token.objects.create(user=user)
        update_last_login(None, user)
        record(request, ActivityLog.Action.REGISTER, user=user)
        return Response(signed_in(user, token), status=status.HTTP_201_CREATED)


class MeView(APIView):
    """GET the signed-in user's profile; PATCH their own details."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(MeSerializer(request.user).data)

    def patch(self, request):
        data = MeSerializer(request.user, data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        changed = [
            name
            for name, value in data.validated_data.items()
            if getattr(request.user, name) != value
        ]
        data.save()
        if changed:
            record(request, ActivityLog.Action.PROFILE_UPDATE, fields=changed)
        return Response(data.data)


class PasswordView(APIView):
    """Change the password. Every token is replaced, so other sign-ins end."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        data = PasswordSerializer(data=request.data, context={"request": request})
        data.is_valid(raise_exception=True)
        user = request.user
        with transaction.atomic():
            user.set_password(data.validated_data["new_password"])
            user.save(update_fields=["password"])
            Token.objects.filter(user=user).delete()
            token = Token.objects.create(user=user)
        record(request, ActivityLog.Action.PASSWORD_CHANGE)
        return Response({"token": token.key})


class MyActivityView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        rows = ActivityLog.objects.filter(user=request.user)[:PAGE]
        return Response(ActivitySerializer(rows, many=True).data)


def my_subscriptions(user):
    """Alert subscriptions made while signed in, or to this account's email."""
    mine = Q(user=user)
    if user.email:
        mine |= Q(email__iexact=user.email)
    return Subscriber.objects.filter(mine).prefetch_related("regions").distinct()


class MySubscriptionsView(APIView):
    """GET the signed-in user's alert subscriptions."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(
            [
                {
                    "id": sub.id,
                    "email": sub.email,
                    "phone": sub.phone,
                    "min_level": sub.min_level,
                    "confirmed": sub.confirmed,
                    "regions": [str(region) for region in sub.regions.all()],
                    "created_at": sub.created_at,
                }
                for sub in my_subscriptions(request.user)
            ]
        )


class MySubscriptionView(APIView):
    """DELETE one of the signed-in user's alert subscriptions."""

    permission_classes = [IsAuthenticated]

    def delete(self, request, pk):
        sub = get_object_or_404(my_subscriptions(request.user), pk=pk)
        regions = [str(region) for region in sub.regions.all()]
        sub.delete()
        record(request, ActivityLog.Action.SUBSCRIPTION_REMOVE, regions=regions)
        return Response(status=status.HTTP_204_NO_CONTENT)


class UserAdminViewSet(mixins.ListModelMixin, mixins.UpdateModelMixin, viewsets.GenericViewSet):
    """System Administrator only: every account, and its role and on/off switch.

    An administrator cannot change their own role or switch themselves off,
    so the system can never be left with nobody to run it by accident.
    """

    permission_classes = [IsAdministrator]
    serializer_class = UserAdminSerializer
    http_method_names = ["get", "patch"]

    def get_queryset(self):
        return User.objects.annotate(subscriptions=Count("alert_subscriptions")).order_by(
            "-is_active", "username"
        )

    def perform_update(self, serializer):
        user = serializer.instance
        if user == self.request.user:
            raise ValidationError(
                {"detail": "You cannot change your own role or switch yourself off."}
            )
        if user.is_superuser and serializer.validated_data.get("role") == User.Role.END_USER:
            raise ValidationError({"role": "A superuser is always an administrator."})
        before = {"role": user.role, "is_active": user.is_active}
        serializer.save()
        if before["role"] != user.role:
            record(
                self.request, ActivityLog.Action.ROLE_CHANGE, username=user.username, role=user.role
            )
        if before["is_active"] != user.is_active:
            if not user.is_active:
                # Switched off: end their sign-ins too.
                Token.objects.filter(user=user).delete()
            record(
                self.request,
                ActivityLog.Action.ACCOUNT_SWITCH,
                username=user.username,
                is_active=user.is_active,
            )


class ActivityAdminView(APIView):
    """System Administrator only: everyone's activity, newest first."""

    permission_classes = [IsAdministrator]

    def get(self, request):
        rows = ActivityLog.objects.select_related("user")[: PAGE * 4]
        return Response(ActivitySerializer(rows, many=True).data)
