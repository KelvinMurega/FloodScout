from django.conf import settings
from django.contrib.auth.models import AbstractUser
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
