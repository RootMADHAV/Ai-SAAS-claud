"""The public Identity & Access authentication API: register, login,
refresh, logout -- resolves Technical Debt #9's "no authentication on
any /api/v1 route" at its source. See app/api/dependencies.py's module
docstring for how ``get_current_user``/``require_organization_member``
then use the access-token cookie this module sets to secure the
existing Scanning routes (app/api/v1/scans.py).

Tokens are delivered exclusively as httpOnly cookies (``_set_auth_
cookies`` below), not in a JSON response body -- the project's locked
auth-transport decision (PROJECT_STATE.md section 2). ``login`` and
``refresh`` both set fresh cookies and return a ``UserResponse`` with no
token value in it; ``refresh`` reads the presented refresh token from
its own cookie, not a request body -- the browser attaches it
automatically, the same way it attaches the access-token cookie to every
other authenticated request, which is the point of using cookies at all.

Registration and login are kept separate rather than auto-issuing tokens
on signup (``register`` returns a ``UserResponse`` with no cookies set)
-- a newly registered user is not yet a member of any organization (see
``RegisterUserUseCase``'s own docstring on why this use case does not
create one), so handing back an authenticated session immediately would
be a session with nothing yet to authorize; a separate, explicit
``login`` call keeps "an account exists" and "I am now authenticated" as
two distinct, independently-testable steps.

``logout`` ends the session the presented refresh token belongs to,
server-side (``LogoutUseCase.execute`` revokes it via the same
``RefreshTokenRepositoryPort.revoke`` rotation already exercises) and
clears both cookies (``_clear_auth_cookies`` below, using the same
attributes ``_set_auth_cookies`` used to set them -- see that function's
own docstring on why that match matters). Deliberately does not depend
on ``get_current_user``: an expired-but-not-yet-refreshed access token
is a completely ordinary state to be in when clicking "sign out" (the
access token's own 15-minute lifetime is far shorter than the refresh
token's 30 days), and requiring a still-valid access token here would
make logout itself fail with a 401 in exactly the case it exists to
handle. No request body, no response body (204) -- there is nothing left
to return once the session is over.
"""

from __future__ import annotations

from fastapi import APIRouter, Cookie, Depends, Response

from app.api.dependencies import (
    AuthConfig,
    get_auth_config,
    get_login_use_case,
    get_logout_use_case,
    get_refresh_token_use_case,
    get_register_user_use_case,
)
from app.api.v1.auth_schemas import LoginRequest, RegisterRequest, UserResponse
from app.application.identity.errors import InvalidRefreshTokenError
from app.application.identity.login_user import LoginUseCase
from app.application.identity.logout import LogoutUseCase
from app.application.identity.refresh_token import RefreshTokenUseCase
from app.application.identity.register_user import RegisterUserUseCase
from app.application.identity.tokens import TokenPair
from app.infrastructure.security.token_service import (
    ACCESS_TOKEN_COOKIE_NAME,
    REFRESH_TOKEN_COOKIE_NAME,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_auth_cookies(response: Response, tokens: TokenPair, auth_config: AuthConfig) -> None:
    """Sets both the access and refresh token as httpOnly cookies.

    ``secure=True`` unconditionally, matching what a Secure cookie is
    for -- it is only ever sent by a browser over HTTPS. This is not
    relaxed for local/test convenience: this project's own tests pass
    the cookie value directly on each request instead of depending on a
    client's cookie jar to round-trip a Secure-flagged cookie over the
    plain-HTTP transport a test client uses, so nothing here needs the
    flag weakened to be testable.

    ``samesite="lax"``: sent on top-level navigation and same-site
    requests, withheld on cross-site POSTs, which is what keeps a classic
    CSRF form-post from carrying these cookies to an authenticated
    endpoint. A double-submit CSRF token is not additionally built --
    out of scope for this work; ``samesite="lax"`` is the same-origin-API
    baseline this correction adds, not a claim that CSRF is fully solved
    for a not-yet-built frontend whose own origin/session needs are not
    known yet.
    """
    response.set_cookie(
        key=ACCESS_TOKEN_COOKIE_NAME,
        value=tokens.access_token,
        max_age=auth_config.access_token_expire_minutes * 60,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        key=REFRESH_TOKEN_COOKIE_NAME,
        value=tokens.refresh_token,
        max_age=auth_config.refresh_token_expire_days * 24 * 60 * 60,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )


def _clear_auth_cookies(response: Response) -> None:
    """Clears both auth cookies. Attributes (``path``/``httponly``/
    ``secure``/``samesite``) match ``_set_auth_cookies`` above exactly --
    a browser matches a cookie for deletion primarily by name/path/
    domain, and keeping every attribute symmetric with how the cookie was
    set is what makes this reliably clear it rather than depend on
    browser-specific leniency."""
    response.delete_cookie(
        key=ACCESS_TOKEN_COOKIE_NAME,
        path="/",
        httponly=True,
        secure=True,
        samesite="lax",
    )
    response.delete_cookie(
        key=REFRESH_TOKEN_COOKIE_NAME,
        path="/",
        httponly=True,
        secure=True,
        samesite="lax",
    )


@router.post("/register", status_code=201)
async def register(
    payload: RegisterRequest,
    register_user: RegisterUserUseCase = Depends(get_register_user_use_case),
) -> UserResponse:
    """Raises (via the global exception handler, app/main.py) HTTP 409 if
    ``payload.email`` is already registered. Sets no cookies -- a new
    account is not yet an authenticated session (see module docstring)."""
    user = await register_user.execute(
        email=payload.email, password=payload.password, full_name=payload.full_name
    )
    return UserResponse(id=user.id, email=user.email, full_name=user.full_name)


@router.post("/login")
async def login(
    payload: LoginRequest,
    response: Response,
    login_use_case: LoginUseCase = Depends(get_login_use_case),
    auth_config: AuthConfig = Depends(get_auth_config),
) -> UserResponse:
    """Raises (via the global exception handler) HTTP 401 if the
    credentials are invalid. Sets the access/refresh cookies on success."""
    tokens = await login_use_case.execute(email=payload.email, password=payload.password)
    _set_auth_cookies(response, tokens, auth_config)
    return UserResponse(id=tokens.user.id, email=tokens.user.email, full_name=tokens.user.full_name)


@router.post("/refresh")
async def refresh(
    response: Response,
    refresh_use_case: RefreshTokenUseCase = Depends(get_refresh_token_use_case),
    auth_config: AuthConfig = Depends(get_auth_config),
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_TOKEN_COOKIE_NAME),
) -> UserResponse:
    """Reads the presented refresh token from its own cookie -- not a
    request body, since the whole point of the cookie transport is that
    the browser attaches it automatically. Raises (via the global
    exception handler) HTTP 401 if the cookie is missing, or the token it
    names is unknown, already revoked, or expired. Rotates the presented
    token (see ``RefreshTokenUseCase``'s own docstring) -- the fresh
    cookies this sets replace the old ones; there is nothing for the
    caller to discard or resubmit manually."""
    if refresh_token is None:
        raise InvalidRefreshTokenError("no refresh token cookie present")
    tokens = await refresh_use_case.execute(raw_refresh_token=refresh_token)
    _set_auth_cookies(response, tokens, auth_config)
    return UserResponse(id=tokens.user.id, email=tokens.user.email, full_name=tokens.user.full_name)


@router.post("/logout", status_code=204, response_model=None)
async def logout(
    response: Response,
    logout_use_case: LogoutUseCase = Depends(get_logout_use_case),
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_TOKEN_COOKIE_NAME),
) -> None:
    """Ends the current session: revokes the presented refresh token
    (see ``LogoutUseCase``'s own docstring on why an unknown/already-
    revoked token is not an error) and clears both cookies regardless.
    Always 204, even with no refresh-token cookie present at all -- the
    caller asked for "make sure I'm logged out," and that is already
    true in that case, not a failure to report."""
    if refresh_token is not None:
        await logout_use_case.execute(raw_refresh_token=refresh_token)
    _clear_auth_cookies(response)
