"""Security headers and CSRF protection.

The Content-Security-Policy allows no inline scripts or styles, so templates
must never use style="" or onclick="". Every form posts a CSRF token that is
checked against a cookie.
"""
from __future__ import annotations

import hmac
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

CSRF_COOKIE = "hbl_csrf"
CSRF_FIELD = "csrf_token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

CSP = (
    "default-src 'none'; "
    "base-uri 'none'; "
    "form-action 'self'; "
    "frame-ancestors 'self'; "
    "img-src 'self' data:; "
    "script-src 'self'; "
    "style-src 'self'; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "object-src 'none'"
)

HEADERS = {
    "Content-Security-Policy": CSP,
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}


def new_token() -> str:
    return secrets.token_urlsafe(32)


async def get_form(request: Request):
    """The form, parsed once per request.

    The middleware has to read the body to check the CSRF token, which empties
    the request stream. It stashes the parsed form on the shared request state,
    and route handlers read it back through here instead of parsing again.
    """
    cached = getattr(request.state, "form", None)
    if cached is not None:
        return cached
    form = await request.form()
    request.state.form = form
    return form


class SecurityMiddleware(BaseHTTPMiddleware):
    """Adds the headers, issues the CSRF cookie, and checks unsafe requests."""

    async def dispatch(self, request: Request, call_next):
        cookie = request.cookies.get(CSRF_COOKIE)
        token = cookie or new_token()
        request.state.csrf_token = token

        form = None
        if request.method not in SAFE_METHODS:
            if not cookie:
                return self._deny("Missing CSRF cookie. Reload the page and try again.")
            form = await get_form(request)
            sent = str(form.get(CSRF_FIELD, ""))
            if not sent or not hmac.compare_digest(sent, cookie):
                return self._deny("CSRF check failed. Reload the page and try again.")

        try:
            response: Response = await call_next(request)
        finally:
            if form is not None:
                await form.close()  # release any spooled upload files
        for key, value in HEADERS.items():
            response.headers[key] = value
        if not cookie:
            response.set_cookie(
                CSRF_COOKIE,
                token,
                httponly=True,
                samesite="strict",
                path="/",
                max_age=60 * 60 * 24 * 365,
            )
        return response

    def _deny(self, message: str) -> Response:
        response = PlainTextResponse(message, status_code=403)
        for key, value in HEADERS.items():
            response.headers[key] = value
        return response
