"""Identity and authentication.

Everyone can play immediately as a guest with a stable anonymous ID.  When
Supabase auth is configured, players can additionally sign in with Google and
keep their stats across devices.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from typing import Any, MutableMapping

from config import AVATARS, Settings, configure_logging, get_settings
from database import Database
from player import sanitize_name

logger = configure_logging()

SESSION_KEY = "identity"

_ADJECTIVES = (
    "Swift", "Quiet", "Bold", "Clever", "Lucky", "Iron", "Solar", "Neon",
    "Vivid", "Nimble", "Stone", "Rapid", "Cosmic", "Wired", "Sly", "Prime",
)
_NOUNS = (
    "Falcon", "Otter", "Comet", "Cipher", "Vector", "Pilot", "Ronin", "Ember",
    "Circuit", "Nomad", "Badger", "Quasar", "Lynx", "Rook", "Piston", "Drift",
)


def suggest_name(seed: str | None = None) -> str:
    """Deterministic, friendly guest name derived from an ID."""
    digest = hashlib.sha256((seed or uuid.uuid4().hex).encode()).digest()
    adjective = _ADJECTIVES[digest[0] % len(_ADJECTIVES)]
    noun = _NOUNS[digest[1] % len(_NOUNS)]
    return f"{adjective} {noun}"


def suggest_avatar(seed: str | None = None) -> str:
    """Deterministic avatar derived from an ID."""
    digest = hashlib.sha256((seed or uuid.uuid4().hex).encode()).digest()
    return AVATARS[digest[2] % len(AVATARS)]


class AuthError(RuntimeError):
    """Raised when sign-up or sign-in cannot be completed."""


@dataclass(slots=True)
class Identity:
    """Who is currently playing."""

    player_id: str
    name: str
    avatar: str
    is_guest: bool = True
    email: str = ""
    provider: str = "guest"
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def display(self) -> str:
        """Avatar + name."""
        return f"{self.avatar} {self.name}"

    @property
    def short_id(self) -> str:
        """First 8 characters of the player ID, for display."""
        return self.player_id[:8]

    def to_dict(self) -> dict[str, Any]:
        """Serialisable form stored in the Streamlit session."""
        return {
            "player_id": self.player_id,
            "name": self.name,
            "avatar": self.avatar,
            "is_guest": self.is_guest,
            "email": self.email,
            "provider": self.provider,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Identity":
        """Rebuild from :meth:`to_dict`."""
        return cls(
            player_id=data.get("player_id") or uuid.uuid4().hex,
            name=data.get("name") or "Guest",
            avatar=data.get("avatar") or AVATARS[0],
            is_guest=bool(data.get("is_guest", True)),
            email=data.get("email", ""),
            provider=data.get("provider", "guest"),
        )

    @classmethod
    def guest(cls) -> "Identity":
        """Create a fresh anonymous identity."""
        player_id = uuid.uuid4().hex
        return cls(
            player_id=player_id,
            name=suggest_name(player_id),
            avatar=suggest_avatar(player_id),
            is_guest=True,
            provider="guest",
        )


class AuthManager:
    """Session-scoped identity handling with optional Google sign-in."""

    def __init__(
        self,
        db: Database,
        session: MutableMapping[str, Any],
        settings: Settings | None = None,
    ) -> None:
        self.db = db
        self.session = session
        self.settings = settings or get_settings()

    # ------------------------------------------------------------- identity
    @property
    def google_available(self) -> bool:
        """True when Google sign-in is configured and usable."""
        return bool(self.settings.enable_google_auth and self.settings.supabase_configured)

    def current(self) -> Identity:
        """Return the session identity, creating a guest one on first visit."""
        raw = self.session.get(SESSION_KEY)
        if isinstance(raw, dict):
            identity = Identity.from_dict(raw)
        else:
            identity = Identity.guest()
            self.session[SESSION_KEY] = identity.to_dict()
            logger.info("New guest identity %s", identity.short_id)
        self._sync(identity)
        return identity

    def update_profile(self, name: str | None = None, avatar: str | None = None) -> Identity:
        """Change display name and/or avatar."""
        identity = self.current()
        if name is not None:
            identity.name = sanitize_name(name, identity.name)
        if avatar:
            identity.avatar = avatar
        self.session[SESSION_KEY] = identity.to_dict()
        self.db.update_player(identity.player_id, name=identity.name, avatar=identity.avatar)
        return identity

    def sign_out(self) -> Identity:
        """Drop the current identity and start a new guest session."""
        try:
            if self.google_available:
                client = self._client()
                if client:
                    client.auth.sign_out()
        except Exception as exc:  # pragma: no cover - network dependent
            logger.debug("Supabase sign-out failed: %s", exc)
        self.session.pop(SESSION_KEY, None)
        return self.current()

    # ------------------------------------------------------- email accounts
    @property
    def accounts_available(self) -> bool:
        """True when Supabase is configured, so real accounts are possible."""
        return bool(self.settings.supabase_configured and self._client())

    def sign_up(self, email: str, password: str, name: str = "", avatar: str = "") -> Identity:
        """Create an account and sign in, keeping the current display name.

        Supabase may require the address to be confirmed before the account can
        be used. In that case no session comes back, and we say so plainly
        rather than pretending the player is signed in.
        """
        client = self._require_client()
        email, password = email.strip().lower(), password.strip()
        if "@" not in email or "." not in email.split("@")[-1]:
            raise AuthError("That does not look like an email address.")
        if len(password) < 8:
            raise AuthError("Passwords need at least 8 characters.")

        current = self.current()
        display = sanitize_name(name or current.name, current.name)
        try:
            response = client.auth.sign_up(
                {
                    "email": email,
                    "password": password,
                    "options": {"data": {"name": display, "avatar": avatar or current.avatar}},
                }
            )
        except Exception as exc:
            raise AuthError(self._readable(exc)) from exc

        user = getattr(response, "user", None)
        session = getattr(response, "session", None)
        if user is None:
            raise AuthError("Sign-up failed. Try a different address.")
        if session is None:
            raise AuthError(
                "Account created. Check your inbox for a confirmation link, then sign in."
            )
        return self._adopt(user, avatar or current.avatar, display)

    def sign_in(self, email: str, password: str) -> Identity:
        """Sign in to an existing account and restore that player's history."""
        client = self._require_client()
        try:
            response = client.auth.sign_in_with_password(
                {"email": email.strip().lower(), "password": password}
            )
        except Exception as exc:
            raise AuthError(self._readable(exc)) from exc

        user = getattr(response, "user", None)
        if user is None:
            raise AuthError("Wrong email or password.")
        return self._adopt(user)

    def _require_client(self):
        """Return the Supabase client or explain why accounts are unavailable."""
        client = self._client()
        if client is None:
            raise AuthError(
                "Accounts need Supabase. Add SUPABASE_URL and SUPABASE_KEY, then restart."
            )
        return client

    def _adopt(self, user: Any, avatar: str = "", name: str = "") -> Identity:
        """Replace the session identity with the signed-in account.

        The player ID becomes the Supabase user ID, which is stable across
        devices and browsers — that is what makes statistics persist.
        """
        metadata = dict(getattr(user, "user_metadata", None) or {})
        email = getattr(user, "email", "") or ""
        identity = Identity(
            player_id=str(user.id),
            name=sanitize_name(name or metadata.get("name") or email.split("@")[0], "Player"),
            avatar=avatar or metadata.get("avatar") or suggest_avatar(str(user.id)),
            is_guest=False,
            email=email,
            provider="email",
        )
        self.session[SESSION_KEY] = identity.to_dict()
        self.session.pop("_identity_synced", None)
        self._sync(identity)
        logger.info("Signed in as %s", identity.short_id)
        return identity

    @staticmethod
    def _readable(exc: Exception) -> str:
        """Turn a Supabase auth error into something worth showing a player."""
        message = str(getattr(exc, "message", "") or exc).strip()
        lowered = message.lower()
        if "already registered" in lowered or "already been registered" in lowered:
            return "That email already has an account. Sign in instead."
        if "invalid login" in lowered or "invalid credentials" in lowered:
            return "Wrong email or password."
        if "password" in lowered and "short" in lowered:
            return "Passwords need at least 8 characters."
        if "rate limit" in lowered or "too many" in lowered:
            return "Too many attempts. Wait a minute and try again."
        return message or "Could not complete that request."

    def _sync(self, identity: Identity) -> None:
        """Mirror the identity into the players table (best effort, deduplicated)."""
        snapshot = identity.to_dict()
        if self.session.get("_identity_synced") == snapshot:
            return
        self.session["_identity_synced"] = snapshot
        try:
            self.db.ensure_player(
                identity.player_id, identity.name, identity.avatar, identity.is_guest
            )
        except Exception as exc:  # pragma: no cover - storage may be read-only
            logger.warning("Could not persist player %s: %s", identity.short_id, exc)

    # --------------------------------------------------------------- OAuth
    def _client(self):
        """Supabase client for auth calls, or ``None`` when unavailable."""
        db = self.db
        return getattr(db, "client", None)

    def google_sign_in_url(self) -> str | None:
        """Build the Google OAuth redirect URL.

        Returns:
            A URL to send the browser to, or ``None`` when unavailable.
        """
        if not self.google_available:
            return None
        client = self._client()
        if client is None:
            return None
        try:
            options: dict[str, Any] = {}
            if self.settings.oauth_redirect_url:
                options["redirect_to"] = self.settings.oauth_redirect_url
            response = client.auth.sign_in_with_oauth(
                {"provider": "google", "options": options}
            )
            return getattr(response, "url", None) or (
                response.get("url") if isinstance(response, dict) else None
            )
        except Exception as exc:  # pragma: no cover - network dependent
            logger.warning("Google sign-in unavailable: %s", exc)
            return None

    def complete_oauth(self, query_params: MutableMapping[str, Any]) -> Identity | None:
        """Finish a Google sign-in when the browser returns with a code.

        Args:
            query_params: The current URL query parameters.

        Returns:
            The signed-in identity, or ``None`` when there was nothing to do.
        """
        code = query_params.get("code")
        if isinstance(code, list):
            code = code[0] if code else None
        if not code or not self.google_available:
            return None

        client = self._client()
        if client is None:
            return None
        try:
            session = client.auth.exchange_code_for_session({"auth_code": code})
            user = getattr(session, "user", None) or getattr(
                getattr(session, "session", None), "user", None
            )
            if user is None:
                return None
            metadata = getattr(user, "user_metadata", {}) or {}
            identity = Identity(
                player_id=str(getattr(user, "id", uuid.uuid4().hex)),
                name=sanitize_name(metadata.get("full_name") or metadata.get("name") or "Player"),
                avatar=suggest_avatar(str(getattr(user, "id", ""))),
                is_guest=False,
                email=str(getattr(user, "email", "") or ""),
                provider="google",
            )
            self.session[SESSION_KEY] = identity.to_dict()
            self._sync(identity)
            logger.info("Google sign-in complete for %s", identity.short_id)
            return identity
        except Exception as exc:  # pragma: no cover - network dependent
            logger.warning("OAuth exchange failed: %s", exc)
            return None