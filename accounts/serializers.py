from django.contrib.auth import password_validation
from rest_framework import serializers

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

    def validate(self, data):
        candidate = User(username=data["username"], email=data.get("email", ""))
        password_validation.validate_password(data["password"], candidate)
        return data


class PasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField()
    new_password = serializers.CharField()

    def validate_current_password(self, value):
        if not self.context["request"].user.check_password(value):
            raise serializers.ValidationError("Your current password is not right.")
        return value

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
