"""Database-backed session tokens, sent as a cookie or as `Authorization: Bearer`."""

import hashlib
import secrets
from datetime import datetime, timedelta

from fastapi import HTTPException, Request, Response

from fyro.auth.models import Session, User, utcnow
from fyro.auth.passwords import verify_password
from fyro.conf import get_settings

ABSOLUTE_LIFETIME = timedelta(days=14)  # Django's SESSION_COOKIE_AGE
IDLE_TIMEOUT = timedelta(hours=2)
TOUCH_INTERVAL = timedelta(minutes=1)  # last_seen is written at most this often
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CSRF_DETAIL = "CSRF check failed: send an X-Requested-With header."


def cookie_name() -> str:
    # __Host- makes browsers enforce Secure, no Domain and Path=/. It needs https, so debug
    # (http://localhost) uses the plain name.
    return "fyro_session" if get_settings().debug else "__Host-fyro_session"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _token(request: Request) -> tuple[str | None, str | None]:
    """The presented token and how it came: ("...", "bearer" | "cookie") or (None, None)."""
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() == "bearer":
        return value.strip(), "bearer"
    cookie = request.cookies.get(cookie_name())
    return (cookie, "cookie") if cookie else (None, None)


async def authenticate(username: str, password: str) -> User | None:
    """The active user with these credentials, or None.

    Unknown users are verified against a dummy hash and is_active is checked after the hash,
    so every failure looks and times the same.
    """
    user = await User.objects.filter(username=username).first()
    valid = await verify_password(password, user.password if user else None)
    return user if valid and user.is_active else None


async def end_user_sessions(user_id: int) -> None:
    """Log a user out everywhere (after a password change or deactivation)."""
    await Session.objects.filter(user_id=user_id).delete()


async def create_session(user: User) -> tuple[str, datetime]:
    token = secrets.token_urlsafe(32)  # 256 bits; a fresh token every login (no fixation)
    now = utcnow()
    expires_at = now + ABSOLUTE_LIFETIME
    await Session.objects.create(
        token_hash=_hash(token), user_id=user.id, created_at=now, last_seen=now,
        expires_at=expires_at,
    )
    return token, expires_at


async def end_session(request: Request) -> None:
    token, _ = _token(request)
    if token:
        await Session.objects.filter(token_hash=_hash(token)).delete()


def set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        cookie_name(),
        token,
        max_age=int(ABSOLUTE_LIFETIME.total_seconds()),
        path="/",
        secure=not get_settings().debug,
        httponly=True,
        samesite="lax",
    )


def clear_cookie(response: Response) -> None:
    response.delete_cookie(
        cookie_name(), path="/", secure=not get_settings().debug, httponly=True, samesite="lax"
    )


async def _user_for(token: str) -> User | None:
    session = await Session.objects.filter(token_hash=_hash(token)).first()
    if session is None:
        return None
    now = utcnow()
    if now >= session.expires_at or now >= session.last_seen + IDLE_TIMEOUT:
        return None
    user = await User.objects.filter(id=session.user_id, is_active=True).first()
    if user is not None and now - session.last_seen >= TOUCH_INTERVAL:
        await Session.objects.filter(id=session.id).update(last_seen=now)
    return user


async def _resolve(request: Request) -> User | None:
    token, via = _token(request)
    if token is None:
        return None
    user = await _user_for(token)
    # Browsers attach cookies to cross-site requests but cannot add custom headers without
    # the site's consent, so a cookie-authenticated write must carry one (OWASP).
    if (
        user is not None
        and via == "cookie"
        and request.method not in SAFE_METHODS
        and not (request.headers.get("x-requested-with") or request.headers.get("hx-request"))
    ):
        raise HTTPException(403, CSRF_DETAIL)
    return user


async def current_user(request: Request) -> User:
    """Dependency: the logged-in user, or 401."""
    user = await _resolve(request)
    if user is None:
        raise HTTPException(401, "Authentication required.", headers={"WWW-Authenticate": "Bearer"})
    return user


async def optional_user(request: Request) -> User | None:
    """Dependency: the logged-in user, or None."""
    return await _resolve(request)
