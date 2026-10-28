"""The Django admin's sign-in limits (floodscout/admin_login.py)."""

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from accounts.models import User

PASSWORD = "a-long-test-passphrase"


class AdminSignInLimitTests(TestCase):
    def setUp(self):
        cache.clear()
        User.objects.create_superuser("boss", "boss@example.com", PASSWORD)

    def attempt(self, password, address="10.0.0.1"):
        return self.client.post(
            reverse("admin-login"), {"username": "boss", "password": password}, REMOTE_ADDR=address
        )

    def test_the_eleventh_wrong_password_is_refused_even_with_the_right_one(self):
        codes = [self.attempt("wrong", f"10.0.0.{i}").status_code for i in range(10)]
        self.assertEqual(set(codes), {200})  # the form again, with an error
        self.assertEqual(self.attempt(PASSWORD, "10.9.9.9").status_code, 429)

    def test_one_address_guessing_many_accounts_is_stopped(self):
        for i in range(10):
            self.client.post(
                reverse("admin-login"), {"username": f"u{i}", "password": "x"}, REMOTE_ADDR="10.0.0.1"
            )
        self.assertEqual(self.attempt(PASSWORD).status_code, 429)
        self.assertEqual(self.attempt(PASSWORD, "10.0.0.2").status_code, 302)

    def test_the_right_password_still_signs_in(self):
        self.assertEqual(self.attempt(PASSWORD).status_code, 302)
