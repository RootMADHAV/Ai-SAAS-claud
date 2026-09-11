/**
 * Typed error hierarchy for every non-2xx response the API client can
 * see. Every backend error response (app/main.py's global exception
 * handlers, verified against source) is a uniform `{"detail": string}`
 * JSON body for 401/403/404/409 -- except 422, where FastAPI's own
 * request-validation handler returns `{"detail": [{loc, msg, type}, ...]}`,
 * an array of field-level errors, not a string. `client.ts` inspects the
 * status code to pick the right shape; callers catch a specific subclass
 * with `instanceof` rather than string-matching a message.
 */

/** Base class for every HTTP error response (a request that reached the
 * backend and got a non-2xx status back). */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** 401 -- no valid access-token cookie, or (after this client's own
 * silent-refresh-and-retry already ran once and also failed) the
 * refresh token is missing/invalid/expired too. A caller catching this
 * should treat the session as over and send the user to log in again --
 * this client does not do that itself (no routing decision belongs in
 * an API client; see the Step 3 auth flow for where that lives). */
export class UnauthorizedError extends ApiError {
  constructor(message = "Not authenticated") {
    super(401, message);
    this.name = "UnauthorizedError";
  }
}

/** 403 -- authenticated, but not an ACTIVE member of the organization
 * named in the request path (require_organization_member). */
export class ForbiddenError extends ApiError {
  constructor(message = "Not authorized for this organization") {
    super(403, message);
    this.name = "ForbiddenError";
  }
}

/** 404 -- the organization or scan named in the request path does not
 * exist (or is soft-deleted -- repository reads already filter that). */
export class NotFoundError extends ApiError {
  constructor(message = "Not found") {
    super(404, message);
    this.name = "NotFoundError";
  }
}

/** 409 -- a state conflict: duplicate email/slug, or a scan's recorded
 * scanner_name not matching this deployment's wired adapter
 * (ScannerMismatchError). */
export class ConflictError extends ApiError {
  constructor(message = "Conflict") {
    super(409, message);
    this.name = "ConflictError";
  }
}

/** One field-level entry from FastAPI's 422 validation-error array. */
export interface ValidationErrorDetail {
  loc: Array<string | number>;
  msg: string;
  type: string;
}

/** 422 -- request-validation failure. Carries the full field-level
 * `errors` array (not just a joined message) so a caller can map
 * individual errors back to form fields once a form exists (Step 3+). */
export class ValidationApiError extends ApiError {
  readonly errors: ValidationErrorDetail[];

  constructor(errors: ValidationErrorDetail[]) {
    super(422, ValidationApiError.summarize(errors));
    this.name = "ValidationApiError";
    this.errors = errors;
  }

  private static summarize(errors: ValidationErrorDetail[]): string {
    return errors.length > 0 ? errors.map((e) => e.msg).join("; ") : "Validation failed";
  }
}

/** The request never got a response at all -- offline, DNS failure, a
 * blocked/misconfigured CORS preflight, the backend not running, etc.
 * Distinct from ApiError: there is no HTTP status to report. */
export class NetworkError extends Error {
  readonly cause?: unknown;

  constructor(message: string, cause?: unknown) {
    super(message);
    this.name = "NetworkError";
    this.cause = cause;
  }
}
