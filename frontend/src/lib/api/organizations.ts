/**
 * Typed wrapper over the backend's Organization bootstrap endpoint
 * (backend/app/api/v1/organizations.py) -- the one route in that
 * bounded context's HTTP surface so far.
 */

import { apiRequest } from "./client";
import type { OrganizationCreateRequest, OrganizationResponse } from "./types";

/** POST /organizations. Creates an organization and makes the caller
 * its Owner in one call. 201 on success. Requires an active session
 * (401 otherwise) but not existing membership -- there is none yet.
 * Throws ConflictError (409) if `slug` is already taken,
 * ValidationApiError (422) for a malformed body (e.g. a slug that
 * doesn't match the required lowercase-hyphenated pattern). */
export function createOrganization(
  payload: OrganizationCreateRequest,
): Promise<OrganizationResponse> {
  return apiRequest<OrganizationResponse>("/organizations", {
    method: "POST",
    body: payload,
  });
}
