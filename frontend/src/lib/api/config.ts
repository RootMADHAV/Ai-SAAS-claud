/**
 * Backend API origin. Read from NEXT_PUBLIC_API_BASE_URL
 * (.env.local.example) rather than hardcoded, but the *value* itself is
 * not a free choice locally: it must be `https://localhost:8000` (or
 * whatever port the backend uses), matching the mkcert HTTPS setup
 * this project settled on for local dev (PROJECT_STATE.md sections 1/16,
 * frontend/README.md). The backend's auth cookies always carry Secure
 * (locked, section 4) and a Secure cookie is only stored by the browser
 * if the response that set it arrived over HTTPS -- an http:// base URL
 * here would silently break the entire cookie-based session, not just
 * look wrong.
 */
export function getApiBaseUrl(): string {
  const url = process.env.NEXT_PUBLIC_API_BASE_URL;
  if (!url) {
    throw new Error(
      "NEXT_PUBLIC_API_BASE_URL is not set. Copy frontend/.env.local.example " +
        "to frontend/.env.local (see frontend/README.md for the full local " +
        "HTTPS/mkcert setup this value depends on).",
    );
  }
  return url;
}
