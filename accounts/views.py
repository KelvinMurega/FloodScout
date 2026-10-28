from urllib.parse import quote

from django.conf import settings
from django.contrib.auth import password_validation
from django.contrib.auth.models import update_last_login
from django.contrib.auth.tokens import default_token_generator
from django.core.cache import cache
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponseRedirect
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from rest_framework import mixins, status, viewsets
from rest_framework.authtoken.models import Token
from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.exceptions import Throttled, ValidationError
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from alerts import stopping
from alerts.models import Subscriber, Unsubscription

from . import home, social, verification
from .activity import PAGE, record
from .models import ActivityLog, ContactCode, OAuthState, SocialAccount, User
from .permissions import IsAdministrator
from .serializers import (
    ActivitySerializer,
    EmailOrUsernameTokenSerializer,
    MeSerializer,
    PasswordSerializer,
    RegisterSerializer,
    UserAdminSerializer,
)


def email_check(user) -> dict:
    """Send a confirmation code when the user's email is not yet confirmed.

    What the page needs to ask for it: {"email_verified", "code_sent"}.
    A failure to send is not an error here; the profile offers to resend.
    """
    if not user.email or user.email_verified:
        return {"email_verified": bool(user.email) and user.email_verified, "code_sent": False}
    try:
        outcome = verification.send_code(user, ContactCode.Channel.EMAIL)
    except verification.VerificationError:
        outcome = "failed"
    return {"email_verified": False, "code_sent": outcome in ("sent", "recent")}


def signed_in(user, token) -> dict:
    """What the dashboard is told about the person who just signed in."""
    return {
        "token": token.key,
        "username": user.get_username(),
        "first_name": user.first_name,
        "last_name": user.last_name,
        "email": user.email,
        "phone": user.phone,
        "role": user.role,
        "is_administrator": user.is_administrator(),
        "can_see_analytics": user.can_see_analytics(),
        "home_region": user.home_region_id,
        "home_region_name": str(user.home_region) if user.home_region else None,
    }


def _failures_key(name: str) -> str:
    """The cache key counting failed sign-ins for the account `name` points at."""
    name = name.strip().lower()
    if "@" in name:
        matches = list(User.objects.filter(email__iexact=name).values_list("username", flat=True)[:2])
        if len(matches) == 1:
            name = matches[0].lower()
    return f"signin-failures:{name}"


class ObtainTokenView(ObtainAuthToken):
    """POST a username and password, get back a token and the user's role.

    The role is returned so the dashboard knows whether to offer the
    administrator's actions. It is a convenience only: every protected
    endpoint checks the role itself (accounts/permissions.py).
    """

    # Slows password guessing; the rate is DEFAULT_THROTTLE_RATES["login"].
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"
    serializer_class = EmailOrUsernameTokenSerializer

    # The rate above is per address. This is per account, so guessing one
    # person's password from many addresses stops too.
    MAX_FAILURES = 10
    FAILURE_WINDOW = 15 * 60  # seconds

    def post(self, request, *args, **kwargs):
        key = _failures_key(str(request.data.get("username") or ""))
        if cache.get(key, 0) >= self.MAX_FAILURES:
            raise Throttled(
                wait=self.FAILURE_WINDOW,
                detail="Too many wrong passwords for this account. Try again in 15 minutes, "
                "or reset the password by email.",
            )
        serializer = self.serializer_class(data=request.data, context={"request": request})
        if not serializer.is_valid():
            cache.add(key, 0, self.FAILURE_WINDOW)
            cache.incr(key)
            raise ValidationError(serializer.errors)
        cache.delete(key)
        user = serializer.validated_data["user"]
        token, _created = Token.objects.get_or_create(user=user)
        update_last_login(None, user)
        record(request, ActivityLog.Action.SIGN_IN, user=user)
        return Response({**signed_in(user, token), **email_check(user)})


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
        return Response(
            {**signed_in(user, token), **email_check(user)}, status=status.HTTP_201_CREATED
        )


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
        # A new email or phone has to be confirmed again before alerts use it.
        extra = {}
        if "email" in changed:
            extra["email_verified"] = False
        if "phone" in changed:
            extra["phone_verified"] = False
        data.save(**extra)
        if changed:
            record(request, ActivityLog.Action.PROFILE_UPDATE, fields=changed)
        user = request.user
        body = dict(data.data)
        if "email" in changed and user.email:
            body.update(email_check(user))
        if {"email", "phone"} & set(changed):
            home.sync_home_subscription(user)
        return Response(body)


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
    """Alert subscriptions made while signed in, or to this account's email.

    By email only once the account has confirmed it: anyone can type any
    address into their profile, and must not see or stop a stranger's alerts.
    """
    mine = Q(user=user)
    if user.email and user.email_verified:
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
                    "is_home": sub.is_home,
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
        if sub.is_home:
            request.user.home_alerts = False
            request.user.save(update_fields=["home_alerts"])
        stopping.stop(sub, Unsubscription.How.PROFILE)
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
    # Administrators only, and the admin page lists every account.
    pagination_class = None

    def get_queryset(self):
        sent = Q(
            alert_subscriptions__deliveries__kind="alert",
            alert_subscriptions__deliveries__outcome="sent",
        )
        return User.objects.annotate(
            subscriptions=Count("alert_subscriptions", distinct=True),
            # Alerts that reached them while subscribed; those after they
            # stopped are kept unnamed (alerts/stopping.py).
            alerts_received=Count("alert_subscriptions__deliveries", filter=sent, distinct=True),
            unsubscribed=Count("unsubscriptions", distinct=True),
        ).order_by("-is_active", "username")

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


# --- Social sign-in (accounts/social.py) ------------------------------------


class SocialProvidersView(APIView):
    """The sign-in providers set up on this server, for the sign-in page's buttons."""

    permission_classes = [AllowAny]

    def get(self, request):
        return Response([{"key": p.key, "name": p.name} for p in social.enabled()])


class SocialStartView(APIView):
    """POST to begin signing in with a provider; the URL to send the browser to.

    Signed in, it links the provider to the account instead. `next` is the
    dashboard page to come back to; `remember` keeps the sign-in after the
    browser closes.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request, provider):
        try:
            url = social.start(
                provider,
                next_page=str(request.data.get("next") or ""),
                remember=bool(request.data.get("remember")),
                link_user=request.user if request.user.is_authenticated else None,
            )
        except social.SocialError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response({"url": url})


def _dashboard(fragment: str) -> HttpResponseRedirect:
    return HttpResponseRedirect(f"{settings.DASHBOARD_URL.rstrip('/')}/{fragment}")


class SocialCallbackView(APIView):
    """Where the provider sends the browser back. Never returns JSON: it
    always redirects to the dashboard, with a ticket or with an error."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, provider):
        params = request.query_params
        if params.get("error"):
            state = params.get("state")
            if state:
                OAuthState.objects.filter(key=state).delete()
            return _dashboard(f"#/signin?error={quote('The sign-in was cancelled.')}")
        try:
            ticket, outcome = social.finish(
                provider, params.get("code", ""), params.get("state", "")
            )
        except social.SocialError as exc:
            return _dashboard(f"#/signin?error={quote(str(exc))}")
        if outcome.linked:
            record(request, ActivityLog.Action.SOCIAL_LINK, user=outcome.user, provider=provider)
        if outcome.created:
            record(request, ActivityLog.Action.REGISTER, user=outcome.user, provider=provider)
        return _dashboard(f"#/signin/complete/{ticket.key}")


class SocialCompleteView(APIView):
    """POST the ticket from the callback; get the token, as signing in does."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        try:
            ticket = social.redeem(str(request.data.get("ticket") or ""))
        except social.SocialError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        user = ticket.user
        home.sync_home_subscription(user)
        token, _created = Token.objects.get_or_create(user=user)
        update_last_login(None, user)
        record(request, ActivityLog.Action.SIGN_IN, user=user, method="social")
        return Response(
            {
                **signed_in(user, token),
                "next": ticket.next,
                "remember": ticket.remember,
                "created": ticket.created,
                "linked": ticket.linked,
            }
        )


class SocialAccountsView(APIView):
    """The signed-in user's linked providers, and the ones they could link."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        linked = {a.provider: a for a in request.user.social_accounts.all()}
        available = {p.key for p in social.enabled()}
        return Response(
            {
                "has_password": request.user.has_usable_password(),
                "providers": [
                    {
                        "key": key,
                        "name": provider.name,
                        "available": key in available,
                        "linked": key in linked,
                        "id": linked[key].id if key in linked else None,
                        "email": linked[key].email if key in linked else "",
                        "linked_at": linked[key].created_at if key in linked else None,
                    }
                    for key, provider in social.PROVIDERS.items()
                ],
            }
        )


class SocialAccountView(APIView):
    """DELETE a linked provider. Never the last way in: set a password first."""

    permission_classes = [IsAuthenticated]

    def delete(self, request, pk):
        account = get_object_or_404(SocialAccount, pk=pk, user=request.user)
        others = request.user.social_accounts.exclude(pk=pk).exists()
        if not others and not request.user.has_usable_password():
            raise ValidationError(
                {"detail": "This is your only way to sign in. Set a password first."}
            )
        provider = account.provider
        account.delete()
        record(request, ActivityLog.Action.SOCIAL_UNLINK, provider=provider)
        return Response(status=status.HTTP_204_NO_CONTENT)


# --- Password reset by email -------------------------------------------------


class PasswordResetView(APIView):
    """POST an email; if an account has it, a reset link is sent to it.

    The answer is the same either way, so the form cannot be used to find out
    who has an account.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        email = str(request.data.get("email") or "").strip()
        if "@" not in email:
            raise ValidationError({"email": "Enter your email address."})
        for user in User.objects.filter(email__iexact=email, is_active=True):
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            token = default_token_generator.make_token(user)
            link = f"{settings.DASHBOARD_URL.rstrip('/')}/#/reset/{uid}/{token}"
            send_mail(
                "Reset your FloodScout password",
                f"Hello {user.first_name or user.username},\n\n"
                f"Someone asked to reset the password of the FloodScout account "
                f"{user.username}. To choose a new one, open this link within three days:"
                f"\n\n{link}\n\n"
                "If it was not you, ignore this email: nothing changes until the link is used.",
                settings.DEFAULT_FROM_EMAIL,
                [user.email],
                fail_silently=True,
            )
        return Response(
            {"detail": "If an account uses that email, a link to reset the password is on its way."}
        )


class PasswordResetConfirmView(APIView):
    """POST the uid and token from the link with a new password."""

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        invalid = ValidationError({"detail": "This reset link is not valid or has been used."})
        try:
            pk = force_str(urlsafe_base64_decode(str(request.data.get("uid") or "")))
            user = User.objects.get(pk=pk, is_active=True)
        except (ValueError, TypeError, OverflowError, User.DoesNotExist) as exc:
            raise invalid from exc
        if not default_token_generator.check_token(user, str(request.data.get("token") or "")):
            raise invalid
        new_password = str(request.data.get("new_password") or "")
        try:
            password_validation.validate_password(new_password, user)
        except DjangoValidationError as exc:
            raise ValidationError({"new_password": list(exc.messages)}) from exc
        user.set_password(new_password)
        user.save(update_fields=["password"])
        Token.objects.filter(user=user).delete()
        record(request, ActivityLog.Action.PASSWORD_RESET, user=user)
        return Response({"detail": "Your password has been changed. Sign in with it now."})


# --- Confirming an email or phone, and where the user lives ------------------


class VerifyContactView(APIView):
    """POST {channel: "email"|"phone"} to send a code; with {code} to confirm it.

    Confirming makes the contact verified, so alerts reach it without asking,
    and brings the home subscription up to date.
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "alerts"

    def post(self, request):
        channel = request.data.get("channel", ContactCode.Channel.EMAIL)
        if channel not in ContactCode.Channel.values:
            raise ValidationError({"channel": "Expected email or phone."})
        user = request.user
        code = str(request.data.get("code") or "").strip()
        try:
            if not code:
                outcome = verification.send_code(user, channel, force=True)
                return Response({"status": outcome})
            verification.confirm(user, channel, code)
        except verification.VerificationError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        record(request, ActivityLog.Action.CONTACT_VERIFIED, channel=channel)
        home.sync_home_subscription(user)
        return Response(
            {
                "status": "verified",
                "email_verified": user.email_verified,
                "phone_verified": user.phone_verified,
                "home": home.home_summary(user),
            }
        )


class HomeView(APIView):
    """GET where the signed-in user lives; PUT it; DELETE to clear it.

    PUT {address, lat, lon} finds the sub-county from the point; {region}
    (a sub-county id) sets or corrects it directly. {alerts_on, alert_level}
    choose the automatic warnings.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(home.home_summary(request.user))

    def put(self, request):
        user = request.user
        data = request.data
        if "alert_level" in data:
            if data["alert_level"] not in ("advisory", "watch", "warning"):
                raise ValidationError({"alert_level": "Expected advisory, watch or warning."})
            user.home_alert_level = data["alert_level"]
        if "alerts_on" in data:
            user.home_alerts = bool(data["alerts_on"])
        user.save(update_fields=["home_alert_level", "home_alerts"])
        moving = any(k in data for k in ("lat", "lon", "region", "address"))
        try:
            if moving:
                home.set_home(
                    user,
                    address=str(data.get("address") or ""),
                    lat=data.get("lat"),
                    lon=data.get("lon"),
                    region_id=data.get("region"),
                )
                record(request, ActivityLog.Action.HOME_SET, region=user.home_region.name)
            else:
                home.sync_home_subscription(user)
        except home.HomeError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(home.home_summary(user))

    def delete(self, request):
        home.clear_home(request.user)
        return Response(home.home_summary(request.user))


class HomeSearchView(APIView):
    """GET ?q= : places in Kenya matching an address, each with its sub-county."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "alerts"

    def get(self, request):
        try:
            places = home.geocode(request.query_params.get("q", ""))
        except home.HomeError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        out = []
        for place in places:
            region = home.region_for_point(place["lon"], place["lat"])
            out.append(
                {
                    **place,
                    "region": region.pk if region else None,
                    "region_name": region.name if region else None,
                    "county_name": region.county_name if region else None,
                }
            )
        return Response(out)


class HomeLocateView(APIView):
    """POST {lat, lon} from the browser's location: the sub-county it is in."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            lat, lon = float(request.data.get("lat")), float(request.data.get("lon"))
        except (TypeError, ValueError) as exc:
            raise ValidationError({"detail": "That location could not be read."}) from exc
        region = home.region_for_point(lon, lat)
        return Response(
            {
                "region": region.pk if region else None,
                "region_name": region.name if region else None,
                "county_name": region.county_name if region else None,
            }
        )
