"""The profile page's API: registering, one's own details and password, one's
alerts, and the administrator's users and activity log."""

from django.core.cache import cache
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from alerts.models import Subscriber
from riskengine.tests import make_region

from .models import ActivityLog, User

PASSWORD = "a-long-test-passphrase"


class _Signed(APITestCase):
    def setUp(self):
        cache.clear()  # the sign-in throttle counts across tests otherwise
        self.admin = User.objects.create_user(
            "otieno", password=PASSWORD, role=User.Role.ADMINISTRATOR, email="o@example.com"
        )
        self.user = User.objects.create_user("wanjiku", password=PASSWORD, email="w@example.com")

    def sign_in(self, user):
        token, _ = Token.objects.get_or_create(user=user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
        return token

    def actions(self, user=None):
        qs = ActivityLog.objects.all() if user is None else ActivityLog.objects.filter(user=user)
        return list(qs.order_by("at", "id").values_list("action", flat=True))


class RegisterTests(_Signed):
    def test_creates_an_end_user_signed_in(self):
        response = self.client.post(
            "/api/auth/register/",
            {"username": "akinyi", "email": "a@example.com", "password": PASSWORD},
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["role"], "user")
        self.assertFalse(response.data["is_administrator"])
        user = User.objects.get(username="akinyi")
        self.assertEqual(Token.objects.get(user=user).key, response.data["token"])
        self.assertEqual(self.actions(user), ["register"])

    def test_refuses_a_taken_name_and_a_weak_password(self):
        taken = self.client.post(
            "/api/auth/register/", {"username": "Wanjiku", "password": PASSWORD}
        )
        self.assertEqual(taken.status_code, status.HTTP_400_BAD_REQUEST)
        weak = self.client.post("/api/auth/register/", {"username": "new", "password": "12345678"})
        self.assertEqual(weak.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_ask_to_be_an_administrator(self):
        self.client.post(
            "/api/auth/register/", {"username": "sneaky", "password": PASSWORD, "role": "admin"}
        )
        self.assertEqual(User.objects.get(username="sneaky").role, User.Role.END_USER)


class SignInTests(_Signed):
    def test_sign_in_returns_the_name_and_is_logged_with_the_time(self):
        self.user.first_name = "Wanjiku"
        self.user.save()
        response = self.client.post(
            "/api/auth/token/", {"username": "wanjiku", "password": PASSWORD}
        )
        self.assertEqual(response.data["first_name"], "Wanjiku")
        self.user.refresh_from_db()
        self.assertIsNotNone(self.user.last_login)
        self.assertEqual(self.actions(self.user), ["sign_in"])


class MeTests(_Signed):
    def test_needs_a_sign_in(self):
        self.assertEqual(self.client.get("/api/auth/me/").status_code, status.HTTP_401_UNAUTHORIZED)

    def test_reads_and_changes_only_the_details(self):
        county = make_region("Kisumu")
        self.sign_in(self.user)
        response = self.client.patch(
            "/api/auth/me/",
            {
                "first_name": "Wanjiku",
                "phone": "0712 345 678",
                "organisation": "Kisumu County",
                "county": county.id,
                "role": "admin",
                "username": "boss",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["phone"], "+254712345678")
        self.assertEqual(response.data["county_name"], "Kisumu")
        self.user.refresh_from_db()
        self.assertEqual((self.user.role, self.user.username), (User.Role.END_USER, "wanjiku"))
        log = ActivityLog.objects.get(user=self.user, action="profile_update")
        self.assertCountEqual(
            log.detail["fields"], ["first_name", "phone", "organisation", "county"]
        )

    def test_a_bad_phone_is_refused(self):
        self.sign_in(self.user)
        response = self.client.patch("/api/auth/me/", {"phone": "12"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class PasswordTests(_Signed):
    def test_changes_the_password_and_ends_other_sign_ins(self):
        old = self.sign_in(self.user)
        response = self.client.post(
            "/api/auth/password/",
            {"current_password": PASSWORD, "new_password": "another-long-passphrase"},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotEqual(response.data["token"], old.key)
        self.assertFalse(Token.objects.filter(key=old.key).exists())
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("another-long-passphrase"))
        self.assertIn("password_change", self.actions(self.user))

    def test_needs_the_current_password_and_a_strong_new_one(self):
        self.sign_in(self.user)
        wrong = self.client.post(
            "/api/auth/password/",
            {"current_password": "nope", "new_password": "another-long-passphrase"},
        )
        self.assertEqual(wrong.status_code, status.HTTP_400_BAD_REQUEST)
        weak = self.client.post(
            "/api/auth/password/", {"current_password": PASSWORD, "new_password": "123"}
        )
        self.assertEqual(weak.status_code, status.HTTP_400_BAD_REQUEST)


class MySubscriptionTests(_Signed):
    def test_lists_and_stops_only_ones_own(self):
        region = make_region("Alpha")
        mine = Subscriber.objects.create(email="W@example.com", confirmed=True)
        mine.regions.add(region)
        theirs = Subscriber.objects.create(email="someone@example.com", confirmed=True)
        self.sign_in(self.user)
        listed = self.client.get("/api/auth/me/subscriptions/")
        self.assertEqual([row["id"] for row in listed.data], [mine.id])
        self.assertEqual(listed.data[0]["regions"], ["Alpha"])
        self.assertEqual(
            self.client.delete(f"/api/auth/me/subscriptions/{theirs.id}/").status_code,
            status.HTTP_404_NOT_FOUND,
        )
        self.assertEqual(
            self.client.delete(f"/api/auth/me/subscriptions/{mine.id}/").status_code,
            status.HTTP_204_NO_CONTENT,
        )
        self.assertFalse(Subscriber.objects.filter(pk=mine.pk).exists())


class AdminUsersTests(_Signed):
    def test_only_administrators_see_users_and_activity(self):
        self.sign_in(self.user)
        self.assertEqual(
            self.client.get("/api/admin/users/").status_code, status.HTTP_403_FORBIDDEN
        )
        self.assertEqual(
            self.client.get("/api/admin/activity/").status_code, status.HTTP_403_FORBIDDEN
        )

    def test_change_a_role_and_switch_an_account_off(self):
        self.sign_in(self.admin)
        users = self.client.get("/api/admin/users/")
        self.assertEqual({u["username"] for u in users.data}, {"otieno", "wanjiku"})

        promoted = self.client.patch(
            f"/api/admin/users/{self.user.id}/", {"role": "admin"}, format="json"
        )
        self.assertEqual(promoted.data["role"], "admin")
        Token.objects.create(user=self.user)
        off = self.client.patch(
            f"/api/admin/users/{self.user.id}/", {"is_active": False}, format="json"
        )
        self.assertFalse(off.data["is_active"])
        self.assertFalse(Token.objects.filter(user=self.user).exists())

        log = self.client.get("/api/admin/activity/")
        self.assertEqual([row["action"] for row in log.data][:2], ["account_switch", "role_change"])
        self.assertEqual(log.data[0]["username"], "otieno")

    def test_cannot_change_oneself_or_unmake_a_superuser(self):
        root = User.objects.create_superuser("root", password=PASSWORD)
        self.sign_in(self.admin)
        own = self.client.patch(
            f"/api/admin/users/{self.admin.id}/", {"role": "user"}, format="json"
        )
        self.assertEqual(own.status_code, status.HTTP_400_BAD_REQUEST)
        demote = self.client.patch(f"/api/admin/users/{root.id}/", {"role": "user"}, format="json")
        self.assertEqual(demote.status_code, status.HTTP_400_BAD_REQUEST)
        # Only the role and the switch can be changed.
        self.client.patch(f"/api/admin/users/{self.user.id}/", {"username": "x"}, format="json")
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, "wanjiku")

    def test_rescoring_is_logged(self):
        self.sign_in(self.admin)
        make_region("Alpha")
        self.client.post("/api/risk-scores/rescore/")
        self.assertIn("rescore", self.actions(self.admin))
