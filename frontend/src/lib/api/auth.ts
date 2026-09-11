/**
 * Typed wrappers over the backend's Identity & Access auth endpoints
 * (backend/app/api/v1/auth.py). No cookie handling here -- the browser
 * attaches/receives them automatically via `credentials: "include"`
 * (client.ts); these functions only ever see the `UserResponse` body.
 */

import { apiRequest } from "./client";
import type { LoginRequest, RegisterRequest, UserResponse } from "./types";

/** POST /auth/register. 201 on success; throws ConflictError (409) if
 * the email is already registered, ValidationApiError (422) for a
 * malformed body. Sets no cookies -- registering does not log you in
 * (see the backend route's own docstring). */
export function register(payload: RegisterRequest): Promise<UserResponse> {
  return apiRequest<UserResponse>("/auth/register", {
    method: "POST",
    body: payload,
    // A failed registration is a validation/conflict error, never a
    // stale-session case -- nothing to refresh into.
    skipAuthRetry: true,
  });
}

/** POST /auth/login. Sets the access/refresh httpOnly cookies on
 * success. Throws UnauthorizedError (401) for invalid credentials. */
export function login(payload: LoginRequest): Promise<UserResponse> {
  return apiRequest<UserResponse>("/auth/login", {
    method: "POST",
    body: payload,
    // A failed login is a credentials failure, not an expired session --
    // attempting a refresh here would be meaningless (and would just
    // fail anyway, since there's no prior session to refresh).
    skipAuthRetry: true,
  });
}

/** POST /auth/refresh. Rotates the refresh token and sets fresh cookies
 * on success. Exposed for an app-level "restore session on load" call;
 * `client.ts`'s own automatic 401-retry logic calls the same backend
 * endpoint internally and does not go through this function. Throws
 * UnauthorizedError (401) if there is no valid refresh-token cookie. */
export function refreshSession(): Promise<UserResponse> {
  return apiRequest<UserResponse>("/auth/refresh", {
    method: "POST",
    // This *is* the refresh call -- it must never trigger another
    // refresh attempt on its own 401.
    skipAuthRetry: true,
  });
}

/** POST /auth/logout. Revokes the current refresh token server-side and
 * clears both cookies (backend route docstring: 204, no body, either
 * way). Never depends on a retry -- the backend route itself never
 * depends on get_current_user, so a 401 here isn't a stale-session case
 * to recover from; skipAuthRetry just keeps this consistent with the
 * other three functions in this module. */
export function logout(): Promise<void> {
  return apiRequest<void>("/auth/logout", {
    method: "POST",
    skipAuthRetry: true,
  });
}
