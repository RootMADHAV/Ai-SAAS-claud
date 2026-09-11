/**
 * Core HTTP client for the backend's `/api/v1` surface.
 *
 * - `credentials: "include"` on every request -- required for the
 *   httpOnly access/refresh cookies (PROJECT_STATE.md section 4, locked
 *   auth transport) to be sent at all; this is the one thing every
 *   caller needs and must never forget, so it lives here once rather
 *   than at each call site.
 * - A 401 triggers exactly one silent `POST /auth/refresh` before the
 *   error is surfaced, then retries the original request once. If the
 *   refresh itself fails (expired/missing refresh token), the original
 *   401 is thrown as `UnauthorizedError` -- no infinite loop, no second
 *   retry.
 * - Concurrent requests that all hit a 401 at once share a single
 *   in-flight refresh call (module-level `inFlightRefresh`) rather than
 *   each firing their own `POST /auth/refresh` -- avoids a thundering
 *   herd against the refresh-token-rotation endpoint, where only the
 *   first rotation would succeed and the rest would each invalidate the
 *   token the previous one just rotated in.
 * - `skipAuthRetry` opts a request out of this entirely -- for
 *   `/auth/login` and `/auth/register`, where a 401/other error is a
 *   credentials/validation failure, not a stale session, and attempting
 *   a refresh makes no sense (there is nothing to refresh into). The
 *   client's own internal calls to `/auth/refresh` also always set this,
 *   which is what actually prevents the recursion case (a refresh call
 *   that itself 401s) rather than any path-name check.
 */

import { getApiBaseUrl } from "./config";
import {
  ApiError,
  ConflictError,
  ForbiddenError,
  NetworkError,
  NotFoundError,
  UnauthorizedError,
  ValidationApiError,
  type ValidationErrorDetail,
} from "./errors";

export type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

export interface ApiRequestOptions {
  method?: HttpMethod;
  /** JSON-serializable request body. Omit for GET/DELETE requests with
   * no body. */
  body?: unknown;
  /** Skips the 401 silent-refresh-and-retry for this request. See
   * module docstring. */
  skipAuthRetry?: boolean;
  signal?: AbortSignal;
}

const REFRESH_PATH = "/auth/refresh";

/** Shared in-flight refresh call -- see module docstring. `null` when no
 * refresh is currently running. */
let inFlightRefresh: Promise<boolean> | null = null;

function buildUrl(path: string): string {
  const base = getApiBaseUrl().replace(/\/+$/, "");
  const suffix = path.startsWith("/") ? path : `/${path}`;
  return `${base}/api/v1${suffix}`;
}

/** The one place an actual `fetch()` call happens. Never retries, never
 * inspects the response status -- `apiRequest` below is the only caller
 * that interprets a response, so every "what does a 401 mean" decision
 * stays in one place. */
async function rawRequest(path: string, options: ApiRequestOptions): Promise<Response> {
  const { method = "GET", body, signal } = options;
  const hasBody = body !== undefined;
  try {
    return await fetch(buildUrl(path), {
      method,
      credentials: "include",
      headers: hasBody ? { "Content-Type": "application/json" } : undefined,
      body: hasBody ? JSON.stringify(body) : undefined,
      signal,
    });
  } catch (cause) {
    // fetch() itself throws (TypeError) for network-level failures --
    // offline, DNS, a blocked CORS preflight, connection refused. This
    // never happens for an HTTP error response (4xx/5xx still resolves
    // the promise normally); see `apiRequest` for that case.
    throw new NetworkError("Network request failed", cause);
  }
}

/** Fires the shared refresh call (deduped across concurrent callers) and
 * reports whether it succeeded. Never throws -- a failed refresh is a
 * normal, expected outcome (the session is simply over), not an
 * exceptional one; the caller decides what to do with `false`. */
function refreshAccessToken(): Promise<boolean> {
  if (inFlightRefresh) {
    return inFlightRefresh;
  }
  inFlightRefresh = (async () => {
    try {
      // rawRequest() never triggers a retry itself -- only apiRequest()
      // interprets a 401 -- so there is no recursion risk here even
      // without an explicit skipAuthRetry; a refresh call that itself
      // 401s just resolves to `false` below, same as any other failure.
      const response = await rawRequest(REFRESH_PATH, { method: "POST" });
      return response.ok;
    } catch {
      return false;
    }
  })();
  // Cleared once settled (success or failure) so the *next* 401, later,
  // triggers a fresh refresh attempt rather than reusing this result
  // forever.
  inFlightRefresh.finally(() => {
    inFlightRefresh = null;
  });
  return inFlightRefresh;
}

async function parseJsonBody(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

function throwForErrorResponse(status: number, body: unknown): never {
  const detail =
    body !== null && typeof body === "object" && "detail" in body
      ? (body as { detail: unknown }).detail
      : undefined;

  if (status === 422 && Array.isArray(detail)) {
    throw new ValidationApiError(detail as ValidationErrorDetail[]);
  }

  const message = typeof detail === "string" ? detail : `Request failed with status ${status}`;

  switch (status) {
    case 401:
      throw new UnauthorizedError(message);
    case 403:
      throw new ForbiddenError(message);
    case 404:
      throw new NotFoundError(message);
    case 409:
      throw new ConflictError(message);
    default:
      throw new ApiError(status, message);
  }
}

/**
 * Issues a request against `/api/v1{path}` and returns the parsed JSON
 * body, typed as `T`. Throws a typed subclass from `errors.ts` for any
 * non-2xx response, or `NetworkError` if the request never reached the
 * backend at all.
 */
export async function apiRequest<T>(path: string, options: ApiRequestOptions = {}): Promise<T> {
  const response = await rawRequest(path, options);

  if (response.status === 401 && !options.skipAuthRetry) {
    const refreshed = await refreshAccessToken();
    if (refreshed) {
      return apiRequest<T>(path, { ...options, skipAuthRetry: true });
    }
    // Refresh failed -- surface the *original* 401, not a fabricated
    // one, so the message matches what the backend actually said.
    const body = await parseJsonBody(response);
    throwForErrorResponse(401, body);
  }

  if (!response.ok) {
    const body = await parseJsonBody(response);
    throwForErrorResponse(response.status, body);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}
