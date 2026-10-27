"""Sign in with Google, Microsoft or GitHub.

Google and Microsoft are OpenID Connect providers; GitHub is plain OAuth 2.0.
All three use the authorization-code flow with PKCE (RFC 7636):

  1. The dashboard asks `start` for a URL. An OAuthState row keeps the PKCE
     verifier and the OIDC nonce on the server; only its random key goes to
     the provider, as `state`.
  2. The provider sends the browser to `callback` with a code and the state.
     The state row is looked up and deleted (single use, ten minutes).
  3. The code is exchanged for tokens server to server, with the verifier
     and the client secret.
  4. Who the person is comes from the ID token (OIDC) or the user API
     (GitHub). The ID token arrives straight from the token endpoint over
     TLS, so, as OpenID Connect Core 3.1.3.7 allows, the TLS check stands
     in for its signature; its issuer, audience, expiry and nonce are still
     checked here.
  5. The person is matched to a FloodScout account (see `match_user`), and
     the browser goes back to the dashboard with a one-minute single-use
     LoginTicket in the URL fragment. The dashboard trades it for a token
     by POST. The token is never put in a URL.

Account matching is where social sign-in usually goes wrong, so the rules
are strict:

  - A link is found by (provider, the provider's subject id), never by email.
  - A new link may join an existing account by email only when both sides
    have proved the address: the provider says it is verified (Google's
    `email_verified`, or a primary verified address from GitHub), and the
    account confirmed it too. Otherwise anyone could register with someone
    else's address, wait for them to sign in with Google, and share their
    account with the password they chose ("pre-account hijacking"). Microsoft's email claim is not verified
    by Microsoft for every account type and has been used to take over
    accounts ("nOAuth"), so Microsoft never joins an existing account by
    email; the person signs in with their password and links Microsoft from
    their profile.
  - Otherwise a new End User account is made, with no usable password.

A provider is offered only when its client id and secret are set in `.env`.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import LoginTicket, OAuthState, SocialAccount, User

STATE_LIFETIME = dt.timedelta(minutes=10)
TICKET_LIFETIME = dt.timedelta(minutes=1)
HTTP_TIMEOUT = 20


@dataclass(frozen=True)
class Provider:
    key: str
    name: str
    authorize_url: str
    token_url: str
    scope: str
    oidc: bool
    # For OIDC: what `iss` must look like. Microsoft's common endpoint issues
    # tokens from each person's own tenant, so it is a pattern.
    issuer: str = ""


PROVIDERS = {
    "google": Provider(
        key="google",
        name="Google",
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        scope="openid email profile",
        oidc=True,
        issuer=r"https://accounts\.google\.com|accounts\.google\.com",
    ),
    "microsoft": Provider(
        key="microsoft",
        name="Microsoft",
        authorize_url="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        token_url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
        scope="openid email profile",
        oidc=True,
        issuer=r"https://login\.microsoftonline\.com/[0-9a-f-]{36}/v2\.0",
    ),
    "github": Provider(
        key="github",
        name="GitHub",
        authorize_url="https://github.com/login/oauth/authorize",
        token_url="https://github.com/login/oauth/access_token",
        scope="read:user user:email",
        oidc=False,
    ),
}

GITHUB_USER = "https://api.github.com/user"
GITHUB_EMAILS = "https://api.github.com/user/emails"


class SocialError(Exception):
    """The sign-in could not be finished. The message is shown to the person."""


@dataclass
class Identity:
    subject: str
    email: str
    email_verified: bool
    first_name: str = ""
    last_name: str = ""


def credentials(provider: str) -> tuple[str, str]:
    prefix = provider.upper()
    return (
        getattr(settings, f"{prefix}_CLIENT_ID", ""),
        getattr(settings, f"{prefix}_CLIENT_SECRET", ""),
    )


def enabled() -> list[Provider]:
    """The providers with a client id and secret set, in a fixed order."""
    return [p for key, p in PROVIDERS.items() if all(credentials(key))]


def get_provider(key: str) -> Provider:
    provider = PROVIDERS.get(key)
    if provider is None or not all(credentials(key)):
        raise SocialError("That sign-in method is not set up on this server.")
    return provider


def callback_url(provider: str) -> str:
    return f"{settings.PUBLIC_API_URL.rstrip('/')}/auth/social/{provider}/callback/"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def pkce_challenge(verifier: str) -> str:
    """The S256 code challenge for `verifier` (RFC 7636, section 4.2)."""
    return _b64url(hashlib.sha256(verifier.encode()).digest())


def safe_next(value: str) -> str:
    """A dashboard page to return to: a hash path like "#/alerts", nothing else."""
    value = (value or "").strip()
    return value if re.fullmatch(r"#/[\w/\-]*", value) else ""


def start(provider_key: str, *, next_page: str = "", remember: bool = False, link_user=None) -> str:
    """Begin a sign-in (or a link, with `link_user`); the URL to send the browser to."""
    provider = get_provider(provider_key)
    OAuthState.objects.filter(created_at__lt=timezone.now() - STATE_LIFETIME).delete()
    state = OAuthState.objects.create(
        key=secrets.token_urlsafe(32),
        provider=provider.key,
        code_verifier=secrets.token_urlsafe(64),
        nonce=secrets.token_urlsafe(24),
        next=safe_next(next_page),
        remember=remember,
        link_user=link_user,
    )
    client_id, _secret = credentials(provider.key)
    params = {
        "client_id": client_id,
        "redirect_uri": callback_url(provider.key),
        "response_type": "code",
        "scope": provider.scope,
        "state": state.key,
        "code_challenge": pkce_challenge(state.code_verifier),
        "code_challenge_method": "S256",
    }
    if provider.oidc:
        params["nonce"] = state.nonce
        params["prompt"] = "select_account"
    return f"{provider.authorize_url}?{urllib.parse.urlencode(params)}"


def _request(url: str, *, data: dict | None = None, token: str | None = None):
    headers = {"Accept": "application/json", "User-Agent": "FloodScout"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    request = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise SocialError("The sign-in provider refused the request. Try again.") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise SocialError("The sign-in provider could not be reached. Try again.") from exc


def exchange_code(provider: Provider, code: str, verifier: str) -> dict:
    client_id, client_secret = credentials(provider.key)
    tokens = _request(
        provider.token_url,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": callback_url(provider.key),
            "client_id": client_id,
            "client_secret": client_secret,
            "code_verifier": verifier,
        },
    )
    if "error" in tokens or "access_token" not in tokens:
        raise SocialError("The sign-in provider did not accept the sign-in. Try again.")
    return tokens


def id_token_claims(id_token: str) -> dict:
    """The claims of a JWT, without its signature (see the module docstring)."""
    try:
        payload = id_token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError) as exc:
        raise SocialError("The sign-in provider sent an unreadable identity.") from exc


def check_claims(provider: Provider, claims: dict, nonce: str, now: float | None = None) -> None:
    """Issuer, audience, expiry and nonce, as OpenID Connect Core 3.1.3.7 lists."""
    now = timezone.now().timestamp() if now is None else now
    client_id, _secret = credentials(provider.key)
    audience = claims.get("aud")
    audiences = audience if isinstance(audience, list) else [audience]
    problems = [
        not re.fullmatch(provider.issuer, str(claims.get("iss", ""))),
        client_id not in audiences,
        float(claims.get("exp", 0)) < now,
        claims.get("nonce") != nonce,
        not claims.get("sub"),
    ]
    if any(problems):
        raise SocialError("The sign-in provider's identity did not check out. Try again.")


def identity(provider: Provider, tokens: dict, nonce: str) -> Identity:
    """Who signed in, from the ID token (OIDC) or GitHub's user API."""
    if provider.oidc:
        if "id_token" not in tokens:
            raise SocialError("The sign-in provider sent no identity.")
        claims = id_token_claims(tokens["id_token"])
        check_claims(provider, claims, nonce)
        email = claims.get("email") or (
            claims.get("preferred_username", "") if provider.key == "microsoft" else ""
        )
        return Identity(
            subject=str(claims["sub"]),
            email=email if "@" in (email or "") else "",
            # Only Google vouches for the address; see the module docstring.
            email_verified=provider.key == "google" and claims.get("email_verified") is True,
            first_name=claims.get("given_name", ""),
            last_name=claims.get("family_name", ""),
        )

    user = _request(GITHUB_USER, token=tokens["access_token"])
    emails = _request(GITHUB_EMAILS, token=tokens["access_token"])
    primary = next(
        (e for e in emails if isinstance(e, dict) and e.get("primary") and e.get("verified")), None
    )
    name = (user.get("name") or "").split(" ", 1)
    return Identity(
        subject=str(user["id"]),
        email=primary["email"] if primary else "",
        email_verified=primary is not None,
        first_name=name[0],
        last_name=name[1] if len(name) > 1 else "",
    )


def unique_username(email: str, fallback: str) -> str:
    """A free username from the email's local part, e.g. kmurega, kmurega2."""
    base = re.sub(r"[^\w.@+-]", "", (email.split("@")[0] if email else fallback))[:140] or "user"
    candidate, n = base, 1
    while User.objects.filter(username__iexact=candidate).exists():
        n += 1
        candidate = f"{base}{n}"
    return candidate


@dataclass
class Outcome:
    user: User
    created: bool = False
    linked: str = ""


def match_user(provider: Provider, person: Identity, link_user=None) -> Outcome:
    """The FloodScout account for `person`, made or linked under the rules above."""
    existing = (
        SocialAccount.objects.select_related("user")
        .filter(provider=provider.key, subject=person.subject)
        .first()
    )
    if link_user is not None:
        if existing and existing.user_id != link_user.id:
            raise SocialError(
                f"That {provider.name} account is already linked to another FloodScout account."
            )
        if existing is None:
            if SocialAccount.objects.filter(user=link_user, provider=provider.key).exists():
                raise SocialError(f"Your account is already linked to a {provider.name} account.")
            SocialAccount.objects.create(
                user=link_user, provider=provider.key, subject=person.subject, email=person.email
            )
        return Outcome(user=link_user, linked=provider.key)

    if existing:
        if not existing.user.is_active:
            raise SocialError("This account has been switched off by an administrator.")
        existing.last_used = timezone.now()
        existing.email = person.email or existing.email
        existing.save(update_fields=["last_used", "email"])
        return Outcome(user=existing.user)

    if person.email:
        same_email = User.objects.filter(email__iexact=person.email).first()
        if same_email:
            if not (person.email_verified and same_email.email_verified):
                raise SocialError(
                    f"An account already uses {person.email}. Sign in with your password, then "
                    f"link {provider.name} from your profile."
                )
            if not same_email.is_active:
                raise SocialError("This account has been switched off by an administrator.")
            SocialAccount.objects.create(
                user=same_email, provider=provider.key, subject=person.subject, email=person.email
            )
            return Outcome(user=same_email, linked=provider.key)

    with transaction.atomic():
        user = User(
            username=unique_username(person.email, f"{provider.key}{person.subject[-6:]}"),
            email=person.email,
            first_name=person.first_name[:150],
            last_name=person.last_name[:150],
            email_verified=bool(person.email) and person.email_verified,
        )
        user.set_unusable_password()
        user.save()
        SocialAccount.objects.create(
            user=user, provider=provider.key, subject=person.subject, email=person.email
        )
    return Outcome(user=user, created=True)


def finish(provider_key: str, code: str, state_key: str) -> tuple[LoginTicket, Outcome]:
    """Steps 2 to 5: check the state, exchange the code, match, issue a ticket."""
    state = OAuthState.objects.filter(key=state_key, provider=provider_key).first()
    if state is None or timezone.now() - state.created_at > STATE_LIFETIME:
        if state:
            state.delete()
        raise SocialError("This sign-in took too long or was already used. Start again.")
    link_user, nonce, verifier = state.link_user, state.nonce, state.code_verifier
    next_page, remember = state.next, state.remember
    state.delete()
    if not code:
        raise SocialError("The sign-in was cancelled.")

    provider = get_provider(provider_key)
    tokens = exchange_code(provider, code, verifier)
    outcome = match_user(provider, identity(provider, tokens, nonce), link_user)
    LoginTicket.objects.filter(created_at__lt=timezone.now() - TICKET_LIFETIME).delete()
    ticket = LoginTicket.objects.create(
        key=secrets.token_urlsafe(32),
        user=outcome.user,
        next=next_page,
        remember=remember,
        created=outcome.created,
        linked=outcome.linked,
    )
    return ticket, outcome


def redeem(key: str) -> LoginTicket:
    """The ticket for `key`, deleted as it is read; an error when used or old."""
    with transaction.atomic():
        ticket = (
            LoginTicket.objects.select_for_update().select_related("user").filter(key=key).first()
        )
        if ticket is None:
            raise SocialError("This sign-in link has already been used. Sign in again.")
        ticket.delete()
    if timezone.now() - ticket.created_at > TICKET_LIFETIME:
        raise SocialError("This sign-in link has expired. Sign in again.")
    if not ticket.user.is_active:
        raise SocialError("This account has been switched off by an administrator.")
    return ticket
