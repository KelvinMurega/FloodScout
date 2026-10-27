"""Sign in with Google, Microsoft and GitHub (accounts/social.py), password
reset by email, and signing in with an email address.

The providers are never called: urlopen is replaced by a fake that answers
as each provider's token and user endpoints do.
"""

import base64
import json
import time
import urllib.parse
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from . import social
from .models import ActivityLog, LoginTicket, OAuthState, SocialAccount, User

PASSWORD = "a-long-test-passphrase"
KEYS = {
    "GOOGLE_CLIENT_ID": "google-client",
    "GOOGLE_CLIENT_SECRET": "g-secret",
    "MICROSOFT_CLIENT_ID": "ms-client",
    "MICROSOFT_CLIENT_SECRET": "m-secret",
    "GITHUB_CLIENT_ID": "gh-client",
    "GITHUB_CLIENT_SECRET": "h-secret",
    "PUBLIC_API_URL": "http://127.0.0.1:8010/api",
    "DASHBOARD_URL": "http://localhost:5173",
    "EMAIL_BACKEND": "django.core.mail.backends.locmem.EmailBackend",
}
MS_TENANT = "9188040d-6c67-4c5b-b112-36a304b66dad"


def jwt(claims: dict) -> str:
    def part(data):
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    return f"{part({'alg': 'RS256'})}.{part(claims)}.signature"


class FakeResponse:
    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeProvider:
    """Answers the token endpoint and GitHub's user API; records the requests."""

    def __init__(self, claims=None, github_user=None, github_emails=None, token_error=False):
        self.claims = claims or {}
        self.github_user = github_user
        self.github_emails = github_emails or []
        self.token_error = token_error
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        url = request.full_url
        if url.endswith("/user/emails"):
            return FakeResponse(self.github_emails)
        if url.endswith("api.github.com/user"):
            return FakeResponse(self.github_user)
        if self.token_error:
            return FakeResponse({"error": "invalid_grant"})
        body = {"access_token": "access", "token_type": "Bearer"}
        if self.claims:
            body["id_token"] = jwt(self.claims)
        return FakeResponse(body)


@override_settings(**KEYS)
class SocialFlowTests(APITestCase):
    def setUp(self):
        cache.clear()

    def begin(self, provider="google", **body):
        response = self.client.post(f"/api/auth/social/{provider}/start/", body, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(response.data["url"]).query)
        return {k: v[0] for k, v in query.items()}

    def google_claims(self, params, **overrides):
        state = OAuthState.objects.get(key=params["state"])
        return {
            "iss": "https://accounts.google.com",
            "aud": "google-client",
            "sub": "1234567890",
            "exp": time.time() + 600,
            "nonce": state.nonce,
            "email": "wanjiku@gmail.com",
            "email_verified": True,
            "given_name": "Wanjiku",
            "family_name": "Kamau",
            **overrides,
        }

    def callback(self, provider, params, fake, code="the-code"):
        with mock.patch("urllib.request.urlopen", fake):
            return self.client.get(
                f"/api/auth/social/{provider}/callback/", {"code": code, "state": params["state"]}
            )

    def ticket_from(self, response):
        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        location = response["Location"]
        self.assertTrue(location.startswith("http://localhost:5173/#/signin/complete/"), location)
        return location.rsplit("/", 1)[1]

    def test_providers_are_offered_only_when_set_up(self):
        response = self.client.get("/api/auth/social/providers/")
        self.assertEqual([p["key"] for p in response.data], ["google", "microsoft", "github"])
        with override_settings(GITHUB_CLIENT_SECRET=""):
            response = self.client.get("/api/auth/social/providers/")
            self.assertNotIn("github", [p["key"] for p in response.data])
            refused = self.client.post("/api/auth/social/github/start/")
            self.assertEqual(refused.status_code, status.HTTP_400_BAD_REQUEST)

    def test_start_uses_pkce_and_keeps_the_secrets_on_the_server(self):
        params = self.begin(next="#/alerts", remember=True)
        state = OAuthState.objects.get(key=params["state"])
        self.assertEqual(params["code_challenge_method"], "S256")
        self.assertEqual(params["code_challenge"], social.pkce_challenge(state.code_verifier))
        self.assertEqual(params["nonce"], state.nonce)
        self.assertEqual(
            params["redirect_uri"], "http://127.0.0.1:8010/api/auth/social/google/callback/"
        )
        self.assertNotIn(state.code_verifier, json.dumps(params))
        self.assertEqual((state.next, state.remember), ("#/alerts", True))

    def test_a_new_google_user_gets_an_account_and_a_token_by_ticket(self):
        params = self.begin(next="#/alerts")
        fake = FakeProvider(claims=self.google_claims(params))
        ticket = self.ticket_from(self.callback("google", params, fake))

        # The code was exchanged with the verifier and the secret, server to server.
        sent = urllib.parse.parse_qs(fake.requests[0].data.decode())
        self.assertEqual(sent["grant_type"], ["authorization_code"])
        self.assertEqual(sent["client_secret"], ["g-secret"])
        self.assertIn("code_verifier", sent)
        self.assertFalse(OAuthState.objects.exists())  # used once

        response = self.client.post("/api/auth/social/complete/", {"ticket": ticket})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        user = User.objects.get(email="wanjiku@gmail.com")
        self.assertEqual(response.data["token"], Token.objects.get(user=user).key)
        self.assertEqual((response.data["next"], response.data["created"]), ("#/alerts", True))
        self.assertEqual(
            (user.username, user.first_name, user.role), ("wanjiku", "Wanjiku", "user")
        )
        self.assertFalse(user.has_usable_password())
        self.assertEqual(SocialAccount.objects.get(user=user).subject, "1234567890")

        # A ticket works once.
        again = self.client.post("/api/auth/social/complete/", {"ticket": ticket})
        self.assertEqual(again.status_code, status.HTTP_400_BAD_REQUEST)

    def test_the_same_person_signs_in_to_the_same_account(self):
        for _ in range(2):
            params = self.begin()
            ticket = self.ticket_from(
                self.callback("google", params, FakeProvider(claims=self.google_claims(params)))
            )
            self.client.post("/api/auth/social/complete/", {"ticket": ticket})
        self.assertEqual(User.objects.count(), 1)
        self.assertIsNotNone(SocialAccount.objects.get().last_used)

    def test_a_verified_google_email_joins_the_existing_account(self):
        existing = User.objects.create_user(
            "wanjiku_k", email="Wanjiku@gmail.com", password=PASSWORD, email_verified=True
        )
        params = self.begin()
        ticket = self.ticket_from(
            self.callback("google", params, FakeProvider(claims=self.google_claims(params)))
        )
        response = self.client.post("/api/auth/social/complete/", {"ticket": ticket})
        self.assertEqual(response.data["username"], "wanjiku_k")
        self.assertEqual(response.data["linked"], "google")
        self.assertEqual(SocialAccount.objects.get().user, existing)

    def test_an_account_that_never_confirmed_the_email_is_not_joined(self):
        # Someone registers with Wanjiku's address before she ever signs in.
        # Her Google sign-in must not land in their account.
        register = self.client.post(
            "/api/auth/register/",
            {"username": "squatter", "email": "wanjiku@gmail.com", "password": PASSWORD},
        )
        self.assertEqual(register.status_code, status.HTTP_201_CREATED)
        params = self.begin()
        response = self.callback("google", params, FakeProvider(claims=self.google_claims(params)))
        self.assertIn("#/signin?error=", response["Location"])
        self.assertFalse(SocialAccount.objects.exists())
        self.assertFalse(User.objects.get(username="squatter").email_verified)

    def test_an_unverified_email_never_joins_an_existing_account(self):
        User.objects.create_user("wanjiku_k", email="wanjiku@gmail.com", password=PASSWORD)
        params = self.begin()
        claims = self.google_claims(params, email_verified=False)
        response = self.callback("google", params, FakeProvider(claims=claims))
        self.assertIn("#/signin?error=", response["Location"])
        self.assertIn("link", urllib.parse.unquote(response["Location"]))
        self.assertFalse(SocialAccount.objects.exists())

    def test_microsoft_never_joins_by_email(self):
        User.objects.create_user("akinyi", email="akinyi@strathmore.edu", password=PASSWORD)
        params = self.begin("microsoft")
        state = OAuthState.objects.get(key=params["state"])
        claims = {
            "iss": f"https://login.microsoftonline.com/{MS_TENANT}/v2.0",
            "aud": "ms-client",
            "sub": "ms-subject",
            "exp": time.time() + 600,
            "nonce": state.nonce,
            "email": "akinyi@strathmore.edu",
        }
        response = self.callback("microsoft", params, FakeProvider(claims=claims))
        self.assertIn("#/signin?error=", response["Location"])
        self.assertEqual(User.objects.count(), 1)

    def test_a_microsoft_user_with_a_new_email_gets_an_account(self):
        params = self.begin("microsoft")
        state = OAuthState.objects.get(key=params["state"])
        claims = {
            "iss": f"https://login.microsoftonline.com/{MS_TENANT}/v2.0",
            "aud": "ms-client",
            "sub": "ms-subject",
            "exp": time.time() + 600,
            "nonce": state.nonce,
            "preferred_username": "otieno@strathmore.edu",
        }
        self.ticket_from(self.callback("microsoft", params, FakeProvider(claims=claims)))
        self.assertEqual(User.objects.get().email, "otieno@strathmore.edu")

    def test_bad_identity_claims_are_refused(self):
        for bad in (
            {"aud": "someone-else"},
            {"iss": "https://evil.example"},
            {"exp": time.time() - 10},
            {"nonce": "replayed"},
        ):
            params = self.begin()
            claims = self.google_claims(params, **bad)
            response = self.callback("google", params, FakeProvider(claims=claims))
            self.assertIn("#/signin?error=", response["Location"], bad)
        self.assertFalse(User.objects.exists())

    def test_github_uses_the_primary_verified_email(self):
        params = self.begin("github")
        fake = FakeProvider(
            github_user={"id": 42, "login": "otieno", "name": "Otieno Ouma"},
            github_emails=[
                {"email": "old@example.com", "primary": False, "verified": True},
                {"email": "otieno@example.com", "primary": True, "verified": True},
            ],
        )
        ticket = self.ticket_from(self.callback("github", params, fake))
        response = self.client.post("/api/auth/social/complete/", {"ticket": ticket})
        self.assertEqual(response.data["email"], "otieno@example.com")
        self.assertEqual(response.data["last_name"], "Ouma")
        self.assertEqual(fake.requests[1].get_header("Authorization"), "Bearer access")

    def test_a_reused_or_unknown_state_and_a_cancelled_sign_in_go_back_with_an_error(self):
        params = self.begin()
        fake = FakeProvider(claims=self.google_claims(params))
        self.ticket_from(self.callback("google", params, fake))
        replay = self.callback("google", params, fake)
        self.assertIn("#/signin?error=", replay["Location"])

        cancelled = self.client.get(
            "/api/auth/social/google/callback/", {"error": "access_denied", "state": "x"}
        )
        self.assertIn("cancelled", urllib.parse.unquote(cancelled["Location"]))

    def test_a_provider_that_refuses_the_code(self):
        params = self.begin()
        response = self.callback("google", params, FakeProvider(token_error=True))
        self.assertIn("#/signin?error=", response["Location"])

    def test_a_signed_in_user_links_and_unlinks_a_provider(self):
        user = User.objects.create_user("akinyi", email="a@example.com", password=PASSWORD)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=user).key}")
        params = self.begin()
        self.assertEqual(OAuthState.objects.get().link_user, user)
        self.client.credentials()
        claims = self.google_claims(params, email="different@gmail.com")
        ticket = self.ticket_from(self.callback("google", params, FakeProvider(claims=claims)))
        done = self.client.post("/api/auth/social/complete/", {"ticket": ticket})
        self.assertEqual((done.data["username"], done.data["linked"]), ("akinyi", "google"))

        self.client.credentials(HTTP_AUTHORIZATION=f"Token {done.data['token']}")
        listed = self.client.get("/api/auth/social/accounts/")
        google = next(p for p in listed.data["providers"] if p["key"] == "google")
        self.assertTrue(google["linked"])
        removed = self.client.delete(f"/api/auth/social/accounts/{google['id']}/")
        self.assertEqual(removed.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(
            list(ActivityLog.objects.filter(user=user).values_list("action", flat=True))[:2],
            ["social_unlink", "sign_in"],
        )

    def test_the_last_way_in_cannot_be_unlinked(self):
        user = User.objects.create(username="nopass")
        user.set_unusable_password()
        user.save()
        account = SocialAccount.objects.create(user=user, provider="google", subject="1")
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=user).key}")
        refused = self.client.delete(f"/api/auth/social/accounts/{account.id}/")
        self.assertEqual(refused.status_code, status.HTTP_400_BAD_REQUEST)
        # Setting a password needs no current one for such an account.
        set_one = self.client.post("/api/auth/password/", {"new_password": PASSWORD})
        self.assertEqual(set_one.status_code, status.HTTP_200_OK)

    def test_an_old_ticket_is_refused(self):
        user = User.objects.create_user("akinyi", password=PASSWORD)
        ticket = LoginTicket.objects.create(key="old", user=user)
        LoginTicket.objects.filter(pk=ticket.pk).update(
            created_at=ticket.created_at - social.TICKET_LIFETIME * 2
        )
        response = self.client.post("/api/auth/social/complete/", {"ticket": "old"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class HelperTests(SimpleTestCase):
    def test_pkce_challenge_matches_rfc_7636_appendix_b(self):
        self.assertEqual(
            social.pkce_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"),
            "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
        )

    def test_next_is_only_ever_a_dashboard_page(self):
        self.assertEqual(social.safe_next("#/alerts/subscribe/12"), "#/alerts/subscribe/12")
        for bad in ("https://evil.example", "//evil.example", "#/x?y=<script>", "javascript:x"):
            self.assertEqual(social.safe_next(bad), "")


@override_settings(**KEYS)
class PasswordResetAndEmailSignInTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user("wanjiku", email="w@example.com", password=PASSWORD)

    def test_sign_in_with_the_email_instead_of_the_username(self):
        response = self.client.post(
            "/api/auth/token/", {"username": "W@Example.com", "password": PASSWORD}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["username"], "wanjiku")

    def test_reset_sends_a_link_and_the_link_sets_a_new_password(self):
        response = self.client.post("/api/auth/password-reset/", {"email": "w@example.com"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        link = next(line for line in mail.outbox[0].body.splitlines() if "#/reset/" in line)
        uid, token = link.rsplit("#/reset/", 1)[1].split("/")
        Token.objects.create(user=self.user)

        done = self.client.post(
            "/api/auth/password-reset/confirm/",
            {"uid": uid, "token": token, "new_password": "a-brand-new-passphrase"},
        )
        self.assertEqual(done.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("a-brand-new-passphrase"))
        self.assertFalse(Token.objects.filter(user=self.user).exists())
        # The link works once.
        again = self.client.post(
            "/api/auth/password-reset/confirm/",
            {"uid": uid, "token": token, "new_password": "yet-another-passphrase"},
        )
        self.assertEqual(again.status_code, status.HTTP_400_BAD_REQUEST)

    def test_reset_does_not_say_whether_an_account_exists(self):
        known = self.client.post("/api/auth/password-reset/", {"email": "w@example.com"})
        unknown = self.client.post("/api/auth/password-reset/", {"email": "nobody@example.com"})
        self.assertEqual(known.data, unknown.data)
        self.assertEqual(len(mail.outbox), 1)

    def test_an_email_already_used_cannot_register_again(self):
        response = self.client.post(
            "/api/auth/register/",
            {"username": "other", "email": "W@example.com", "password": PASSWORD},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
