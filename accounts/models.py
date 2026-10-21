from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.contrib.gis.db import models as gis_models
from django.db import models


class User(AbstractUser):
    """The single USERS entity from the ERD (Chapter 4, Section 4.4.1).

    The Use Case Diagram (4.3.1) and Class Diagram (4.3.4) both describe two
    actors, System Administrator and End User, sharing one authentication
    base and distinguished only by role — this mirrors that directly rather
    than creating two separate user classes.

    `phone`, `organisation` and `county` are the profile page's: who the
    person is and where they care about, all optional.
    """

    class Role(models.TextChoices):
        ADMINISTRATOR = "admin", "System Administrator"
        END_USER = "user", "End User"

    role = models.CharField(max_length=10, choices=Role.choices, default=Role.END_USER)
    phone = models.CharField(max_length=16, blank=True, help_text="+2547XXXXXXXX")
    organisation = models.CharField(max_length=120, blank=True)
    county = models.ForeignKey(
        "geodata.Region",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        limit_choices_to={"level": "county"},
        help_text="The county the person mostly cares about",
    )

    # Whether the email and phone above are known to reach this person:
    # vouched for by Google or GitHub, or confirmed once with a code. Alerts
    # go to them in one click only when they are; a change clears the flag.
    email_verified = models.BooleanField(default=False)
    phone_verified = models.BooleanField(default=False)

    # Where the person lives, for warnings that come without asking
    # (accounts/home.py). The point is rounded to about 100 m: enough to
    # find the sub-county, not the house.
    home_address = models.CharField(max_length=255, blank=True)
    home_point = gis_models.PointField(srid=4326, null=True, blank=True)
    home_region = models.ForeignKey(
        "geodata.Region",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        limit_choices_to={"level": "sub_county"},
        help_text="The sub-county the person lives in",
    )
    home_alerts = models.BooleanField(
        default=True, help_text="Send the alerts for where they live without being asked"
    )
    home_alert_level = models.CharField(
        max_length=10,
        default="watch",
        choices=[
            ("advisory", "Advisories and up"),
            ("watch", "Watches and warnings"),
            ("warning", "Warnings only"),
        ],
    )

    def is_administrator(self) -> bool:
        # A Django superuser (`createsuperuser`) counts as well, so the first
        # account on a new install can administer without a role edit.
        return self.is_superuser or self.role == self.Role.ADMINISTRATOR

    def __str__(self) -> str:
        return self.username


class ActivityLog(models.Model):
    """Who did what, and when: sign-ins and every change that matters.

    Written by accounts/activity.py from the views that make the changes:
    signing in and out, account and password changes, rescoring, and issuing,
    cancelling and running alerts. Read on the profile page, each person's
    own and, for a System Administrator, everyone's.
    """

    class Action(models.TextChoices):
        SIGN_IN = "sign_in", "Signed in"
        SIGN_OUT = "sign_out", "Signed out"
        REGISTER = "register", "Created an account"
        PROFILE_UPDATE = "profile_update", "Changed their details"
        PASSWORD_CHANGE = "password_change", "Changed their password"
        RESCORE = "rescore", "Rescored every region"
        ALERT_ISSUE = "alert_issue", "Issued an alert"
        ALERT_CANCEL = "alert_cancel", "Cancelled an alert"
        ALERT_RUN = "alert_run", "Ran the alert rules"
        ALERT_TEST = "alert_test", "Sent a test message"
        ROLE_CHANGE = "role_change", "Changed a user's role"
        ACCOUNT_SWITCH = "account_switch", "Switched an account on or off"
        SUBSCRIPTION_REMOVE = "subscription_remove", "Stopped some alerts"
        SOCIAL_LINK = "social_link", "Linked a Google, Microsoft or GitHub sign-in"
        SOCIAL_UNLINK = "social_unlink", "Unlinked a Google, Microsoft or GitHub sign-in"
        PASSWORD_RESET = "password_reset", "Reset their password by email"
        CONTACT_VERIFIED = "contact_verified", "Confirmed their email or phone"
        HOME_SET = "home_set", "Set where they live"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="activity",
    )
    action = models.CharField(max_length=24, choices=Action.choices)
    detail = models.JSONField(default=dict, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-at", "-id"]
        indexes = [models.Index(fields=["user", "-at"])]

    def __str__(self) -> str:
        return f"{self.user or 'someone'}: {self.get_action_display()} ({self.at:%Y-%m-%d %H:%M})"


class SocialAccount(models.Model):
    """A sign-in at Google, Microsoft or GitHub linked to a FloodScout account.

    Matched on (provider, subject): the provider's own unchanging id for the
    person, never the email, which can change hands.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="social_accounts"
    )
    provider = models.CharField(max_length=20)
    subject = models.CharField(max_length=255, help_text="The provider's id for the person")
    email = models.EmailField(blank=True, help_text="As the provider gave it, for display")
    created_at = models.DateTimeField(auto_now_add=True)
    last_used = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["provider"]
        constraints = [
            models.UniqueConstraint(fields=["provider", "subject"], name="unique_social_account"),
            models.UniqueConstraint(
                fields=["user", "provider"], name="one_social_account_per_provider"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user} via {self.provider}"


class OAuthState(models.Model):
    """One sign-in started at a provider and not yet finished.

    Kept on the server so the PKCE verifier and the nonce never pass through
    the browser: only `key` travels, as the OAuth `state`. Used once.
    """

    key = models.CharField(max_length=64, unique=True)
    provider = models.CharField(max_length=20)
    code_verifier = models.CharField(max_length=128)
    nonce = models.CharField(max_length=64)
    next = models.CharField(max_length=200, blank=True, help_text="Dashboard page to return to")
    remember = models.BooleanField(default=False)
    link_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="+",
        help_text="Set when a signed-in user is linking this provider",
    )
    created_at = models.DateTimeField(auto_now_add=True)


class LoginTicket(models.Model):
    """A one-minute, single-use ticket the dashboard trades for a token.

    The provider sends the browser back to the API; the API sends it on to
    the dashboard with this ticket in the URL fragment (which never reaches a
    server log), and the dashboard exchanges it by POST. The token itself is
    never put in a URL.
    """

    key = models.CharField(max_length=64, unique=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    next = models.CharField(max_length=200, blank=True)
    remember = models.BooleanField(default=False)
    created = models.BooleanField(default=False, help_text="The account was made by this sign-in")
    linked = models.CharField(max_length=20, blank=True, help_text="Provider just linked, if any")
    created_at = models.DateTimeField(auto_now_add=True)


class ContactCode(models.Model):
    """A six-digit code sent to confirm that an email or phone is the user's.

    Sent when the account is made, when someone signs in with an email not
    yet confirmed, and when the email or phone changes. Entering it marks the
    contact verified, and alerts then reach it without asking again.
    """

    class Channel(models.TextChoices):
        EMAIL = "email", "Email"
        PHONE = "phone", "Phone"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="contact_codes"
    )
    channel = models.CharField(max_length=5, choices=Channel.choices)
    destination = models.CharField(max_length=254, help_text="The address or number it went to")
    code = models.CharField(max_length=6)
    attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["user", "channel"], name="one_code_per_contact")
        ]
