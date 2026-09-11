/**
 * TypeScript mirrors of the backend's Pydantic request/response schemas.
 * Hand-maintained, not generated -- kept in lockstep manually with:
 *   - backend/app/api/v1/auth_schemas.py
 *   - backend/app/api/v1/organization_schemas.py
 *   - backend/app/api/v1/schemas.py
 *   - backend/app/domain/shared/enums.py (StrEnum values, verified
 *     against source -- these are what actually serialize to JSON)
 *
 * Field names are snake_case, matching the JSON wire format exactly
 * (Pydantic's default alias-free serialization) rather than converted to
 * camelCase, so a value can be passed straight through without a mapping
 * layer. datetime/UUID fields are `string` (ISO 8601 / UUID text), never
 * `Date` -- no implicit parsing happens in this client; a consumer that
 * needs a `Date` constructs one explicitly at the point of use.
 */

// --- Shared enums (backend/app/domain/shared/enums.py) ---

export type ScanStatus = "queued" | "running" | "completed" | "failed" | "cancelled";

export type WorkflowStepStatus = "pending" | "running" | "completed" | "failed" | "skipped";

export type WorkflowStepName =
  | "validate_target"
  | "execute_scanner"
  | "normalize"
  | "deduplicate"
  | "correlate"
  | "enrich"
  | "ai_analyze"
  | "persist";

// --- Auth (backend/app/api/v1/auth_schemas.py) ---

/** POST /auth/register body. */
export interface RegisterRequest {
  email: string;
  password: string;
  full_name: string;
}

/** POST /auth/login body. */
export interface LoginRequest {
  email: string;
  password: string;
}

/**
 * Returned by register/login/refresh alike. No token value in it --
 * tokens are httpOnly-cookie-only (PROJECT_STATE.md section 4, locked);
 * see errors.ts / client.ts for how the cookie-driven session is
 * actually handled by this client.
 */
export interface UserResponse {
  id: string;
  email: string;
  full_name: string;
}

// --- Organizations (backend/app/api/v1/organization_schemas.py) ---

/** POST /organizations body. */
export interface OrganizationCreateRequest {
  name: string;
  slug: string;
}

/** Returned by POST /organizations. */
export interface OrganizationResponse {
  id: string;
  name: string;
  slug: string;
}

// --- Scanning (backend/app/api/v1/schemas.py) ---

/**
 * POST /organizations/{organization_id}/scans body. `scanner_name` is
 * optional client-side (the backend defaults it to "nuclei", the only
 * adapter currently wired -- Literal["nuclei"] in the Pydantic model).
 */
export interface ScanCreateRequest {
  target: string;
  scanner_name?: "nuclei";
}

export interface WorkflowStepResponse {
  id: string;
  step_name: WorkflowStepName;
  step_order: number;
  status: WorkflowStepStatus;
  retry_count: number;
  started_at: string | null;
  completed_at: string | null;
  error_message: string | null;
}

/** Returned by every scan endpoint (create, run, get) -- same shape
 * regardless of which action was just taken. */
export interface ScanDetailResponse {
  id: string;
  organization_id: string;
  target: string;
  scanner_name: string;
  status: ScanStatus;
  created_at: string;
  updated_at: string;
  triggered_by_user_id: string | null;
  started_at: string | null;
  completed_at: string | null;
  workflow_steps: WorkflowStepResponse[];
}
