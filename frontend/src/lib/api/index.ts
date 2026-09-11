/**
 * Public surface of the API client foundation. Import from
 * "@/lib/api" rather than reaching into individual files -- the
 * per-bounded-context split (auth.ts/scans.ts/organizations.ts) mirrors
 * the backend's own router split (app/api/v1/{auth,scans,
 * organizations}.py) and is an internal organizing detail, not part of
 * the public shape.
 */

export * from "./types";
export * from "./errors";
export { apiRequest, type ApiRequestOptions, type HttpMethod } from "./client";
export { getApiBaseUrl } from "./config";
export { register, login, refreshSession, logout } from "./auth";
export { createScan, runScan, getScan } from "./scans";
export { createOrganization } from "./organizations";
