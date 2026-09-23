"""Google sign-in restricted to an email allowlist."""

from __future__ import annotations

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from .settings import Settings

SESSION_KEY = "user_email"


class LoginRequired(Exception):
    pass


def current_user(request: Request) -> str:
    """Dependency: the signed-in, allowlisted email, or redirect to /login."""
    email = request.session.get(SESSION_KEY)
    settings: Settings = request.app.state.settings
    if not email or email not in settings.allowed_emails:
        request.session.clear()
        raise LoginRequired
    return email


def build_router(settings: Settings) -> APIRouter:
    router = APIRouter()
    oauth = OAuth()
    oauth.register(
        name="google",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email"},
    )

    @router.get("/auth/google")
    async def google_login(request: Request):
        if settings.is_dev and settings.dev_user_email:
            return _sign_in(request, settings, settings.dev_user_email, verified=True)
        redirect_uri = request.url_for("google_callback")
        return await oauth.google.authorize_redirect(request, redirect_uri)

    @router.get("/auth/callback", name="google_callback")
    async def google_callback(request: Request):
        try:
            token = await oauth.google.authorize_access_token(request)
        except OAuthError as e:
            raise HTTPException(400, "Sign-in failed") from e
        info = token.get("userinfo") or {}
        return _sign_in(
            request, settings, str(info.get("email", "")).lower(), bool(info.get("email_verified"))
        )

    @router.post("/logout")
    async def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    return router


def _sign_in(request: Request, settings: Settings, email: str, verified: bool):
    if not verified or email not in settings.allowed_emails:
        request.session.clear()
        raise HTTPException(403, "This account is not allowed to use this site")
    request.session.clear()
    request.session[SESSION_KEY] = email
    return RedirectResponse("/", status_code=303)
