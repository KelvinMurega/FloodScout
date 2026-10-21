from django.contrib.auth import password_validation
from rest_framework import serializers
from rest_framework.authtoken.serializers import AuthTokenSerializer

from alerts.channels import PhoneError, normalise_phone
from geodata.models import Region

from .models import ActivityLog, User


class MeSerializer(serializers.ModelSerializer):
    """The signed-in user's own profile. Only the details are writable."""

    is_administrator = serializers.BooleanField(read_only=True)
    county = serializers.PrimaryKeyRelatedField(
        queryset=Region.objects.filter(level=Region.Level.COUNTY), allow_null=True, required=False
    )
    county_name = serializers.CharField(source="county.name", default=None, read_only=True)

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "first_name",
            "last_name",
            "email",
            "phone",
            "organisation",
            "county",
            "county_name",
            "role",
            "is_administrator",
            "date_joined",
            "last_login",
        ]
        read_only_fields = ["id", "username", "role", "date_joined", "last_login"]

    def validate_email(self, value):
        taken = User.objects.filter(email__iexact=value).exclude(pk=self.instance.pk)
        if value and taken.exists():
            raise serializers.ValidationError("Another account already uses this email.")
        return value

    def validate_phone(self, value):
        if not value:
            return ""
        try:
            return normalise_phone(value)
        except PhoneError as exc:
            raise serializers.ValidationError(str(exc)) from exc


class RegisterSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    email = serializers.EmailField(required=False, allow_blank=True)
    password = serializers.CharField(write_only=True)

    def validate_username(self, value):
        value = value.strip()
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("That username is taken.")
        User.username_validator(value)
        return value

    def validate_email(self, value):
        # One account per email, so an email can be used to sign in and to
        # recognise a Google or GitHub sign-in.
        if value and User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError(
                "An account already uses this email. Sign in instead."
            )
        return value

    def validate(self, data):
        candidate = User(username=data["username"], email=data.get("email", ""))
        password_validation.validate_password(data["password"], candidate)
        return data


class EmailOrUsernameTokenSerializer(AuthTokenSerializer):
    """Sign in with a username or with the email address on the account.

    An email used by more than one account is not accepted as a sign-in
    name: the username is then the only way to say which account is meant.
    """

    def validate(self, attrs):
        name = (attrs.get("username") or "").strip()
        if "@" in name:
            matches = list(User.objects.filter(email__iexact=name)[:2])
            if len(matches) == 1:
                attrs["username"] = matches[0].get_username()
        return super().validate(attrs)


class PasswordSerializer(serializers.Serializer):
    # Not needed by an account made through Google, Microsoft or GitHub that
    # has never had a password: it is setting one, not changing it.
    current_password = serializers.CharField(required=False, allow_blank=True)
    new_password = serializers.CharField()

    def validate_current_password(self, value):
        user = self.context["request"].user
        if user.has_usable_password() and not user.check_password(value):
            raise serializers.ValidationError("Your current password is not right.")
        return value

    def validate(self, data):
        user = self.context["request"].user
        if user.has_usable_password() and not data.get("current_password"):
            raise serializers.ValidationError({"current_password": "Enter your current password."})
        return data

    def validate_new_password(self, value):
        password_validation.validate_password(value, self.context["request"].user)
        return value


class UserAdminSerializer(serializers.ModelSerializer):
    """An account as the administrator's Users table shows it."""

    subscriptions = serializers.IntegerField(read_only=True)

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "first_name",
            "last_name",
            "email",
            "role",
            "is_active",
            "is_superuser",
            "date_joined",
            "last_login",
            "subscriptions",
        ]
        read_only_fields = [
            "id",
            "username",
            "first_name",
            "last_name",
            "email",
            "is_superuser",
            "date_joined",
            "last_login",
        ]


class ActivitySerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", default=None, read_only=True)

    class Meta:
        model = ActivityLog
        fields = ["id", "username", "action", "detail", "at"]
