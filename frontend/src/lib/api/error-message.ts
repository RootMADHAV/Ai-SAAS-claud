import {
  ApiError,
  ConflictError,
  ForbiddenError,
  NetworkError,
  NotFoundError,
  UnauthorizedError,
  ValidationApiError,
} from "@/lib/api/errors";

/**
 * Maps a caught API client error to a short, user-facing message.
 * Centralizes the instanceof-chain every form/action in Step 4 needs
 * (Step 3's login/register pages each hand-wrote a similar, shorter
 * chain inline; Step 4 has enough new call sites -- organization
 * create, scan create, run/retry -- that duplicating it again stopped
 * making sense). Callers that need a status-specific message beyond
 * this generic default (e.g. "an account with this email already
 * exists" instead of a generic conflict message) still catch and check
 * `instanceof` themselves first; this is a fallback, not a replacement
 * for that pattern.
 */
export function describeApiError(error: unknown): string {
  if (error instanceof ValidationApiError) {
    return error.message;
  }
  if (error instanceof UnauthorizedError) {
    return "Your session has expired. Please log in again.";
  }
  if (error instanceof ForbiddenError) {
    return "You don't have access to do that.";
  }
  if (error instanceof NotFoundError) {
    return "Not found.";
  }
  if (error instanceof ConflictError) {
    return error.message;
  }
  if (error instanceof NetworkError) {
    return "Could not reach the server. Check your connection and try again.";
  }
  if (error instanceof ApiError) {
    return error.message;
  }
  return "Something went wrong. Please try again.";
}
