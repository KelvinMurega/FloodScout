"""Tests for signing in and the administrator role (Phase 4 of
docs/GAP_CLOSURE_PLAN.md). The endpoint the role guards is tested in
api/tests.py."""

from django.core.cache import cache
from django.urls import reverse
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from .models import User

PASSWORD = "a-long-test-passphrase"


class RoleTests(APITestCase):
    def test_only_the_administrator_role_and_superusers_are_administrators(self):
        end_user = User.objects.create_user("wanjiku", password=PASSWORD)
        admin = User.objects.create_user(
            "otieno", password=PASSWORD, role=User.Role.ADMINISTRATOR
        )
        superuser = User.objects.create_superuser("root", password=PASSWORD)

        self.assertFalse(end_user.is_administrator())
        self.assertTrue(admin.is_administrator())
        self.assertTrue(superuser.is_administrator())


class TokenTests(APITestCase):
    def setUp(self):
        cache.clear()  # the sign-in throttle counts across tests otherwise
        self.url = reverse("auth-token")
        self.admin = User.objects.create_user(
            "otieno", password=PASSWORD, role=User.Role.ADMINISTRATOR
        )
        self.end_user = User.objects.create_user("wanjiku", password=PASSWORD)

    def test_valid_credentials_return_a_token_and_the_role(self):
        response = self.client.post(self.url, {"username": "otieno", "password": PASSWORD})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["token"], Token.objects.get(user=self.admin).key)
        self.assertEqual(response.data["username"], "otieno")
        self.assertEqual(response.data["role"], User.Role.ADMINISTRATOR)
        self.assertTrue(response.data["is_administrator"])

    def test_an_end_user_can_sign_in_but_is_not_an_administrator(self):
        response = self.client.post(self.url, {"username": "wanjiku", "password": PASSWORD})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["is_administrator"])

    def test_a_wrong_password_is_refused_and_no_token_is_made(self):
        response = self.client.post(self.url, {"username": "otieno", "password": "wrong"})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertNotIn("token", response.data)
        self.assertFalse(Token.objects.exists())

    def test_an_unknown_user_is_refused(self):
        response = self.client.post(self.url, {"username": "nobody", "password": PASSWORD})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_repeated_attempts_are_throttled(self):
        for _ in range(10):
            self.client.post(self.url, {"username": "otieno", "password": "wrong"})

        response = self.client.post(self.url, {"username": "otieno", "password": PASSWORD})

        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_signing_out_deletes_the_token(self):
        token = Token.objects.create(user=self.admin)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

        response = self.client.post(reverse("auth-logout"))

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Token.objects.filter(user=self.admin).exists())
        # The deleted token no longer signs anyone in.
        self.assertEqual(
            self.client.post(reverse("auth-logout")).status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

    def test_signing_out_needs_a_token(self):
        response = self.client.post(reverse("auth-logout"))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
