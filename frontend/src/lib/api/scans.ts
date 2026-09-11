/**
 * Typed wrappers over the backend's Scanning endpoints
 * (backend/app/api/v1/scans.py), all scoped under
 * `/organizations/{organizationId}/scans`. Every one of these requires
 * an active session and organization membership -- a 401/403 from any
 * of them means "not logged in" / "not a member of this org"
 * respectively (see errors.ts).
 */

import { apiRequest } from "./client";
import type { ScanCreateRequest, ScanDetailResponse } from "./types";

/** POST /organizations/{organizationId}/scans. Creates a scan (status
 * "queued") plus its workflow steps; does not run anything yet -- call
 * `runScan` for that. 201 on success. */
export function createScan(
  organizationId: string,
  payload: ScanCreateRequest,
): Promise<ScanDetailResponse> {
  return apiRequest<ScanDetailResponse>(`/organizations/${organizationId}/scans`, {
    method: "POST",
    body: payload,
  });
}

/** POST /organizations/{organizationId}/scans/{scanId}/run. Dispatches
 * the async pipeline and returns immediately (202) with the scan's
 * pre-execution detail -- poll `getScan` to observe progress. Safe to
 * call again on a scan that previously failed partway through (the
 * backend's own resumability); a no-op (still 202) if already running.
 * Throws NotFoundError (404) for an unknown scan, ConflictError (409)
 * if the scan's recorded scanner_name doesn't match this deployment's
 * wired adapter. */
export function runScan(organizationId: string, scanId: string): Promise<ScanDetailResponse> {
  return apiRequest<ScanDetailResponse>(
    `/organizations/${organizationId}/scans/${scanId}/run`,
    { method: "POST" },
  );
}

/** GET /organizations/{organizationId}/scans/{scanId}. A direct read --
 * how a caller observes a dispatched run's progress to completion.
 * Throws NotFoundError (404) for an unknown scan. */
export function getScan(organizationId: string, scanId: string): Promise<ScanDetailResponse> {
  return apiRequest<ScanDetailResponse>(`/organizations/${organizationId}/scans/${scanId}`);
}
