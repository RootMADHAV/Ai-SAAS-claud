import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { login, logout, refreshSession, register } from "./auth";
import { UnauthorizedError } from "./errors";
import { createOrganization } from "./organizations";
import { createScan, getScan, runScan } from "./scans";
import { BASE_URL, jsonResponse, nthFetchCall } from "./test-support";
import type { ScanDetailResponse } from "./types";

describe("endpoint wrappers", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  describe("auth", () => {
    it("register() posts to /auth/register and never retries on 401", async () => {
      fetchMock.mockResolvedValueOnce(jsonResponse(401, { detail: "nope" }));

      await expect(
        register({ email: "a@example.com", password: "password123", full_name: "A" }),
      ).rejects.toBeInstanceOf(UnauthorizedError);

      expect(fetchMock).toHaveBeenCalledTimes(1);
      const [url, init] = nthFetchCall(fetchMock, 0);
      expect(url).toBe(`${BASE_URL}/api/v1/auth/register`);
      expect(init.method).toBe("POST");
    });

    it("login() posts to /auth/login and returns UserResponse on success", async () => {
      fetchMock.mockResolvedValueOnce(
        jsonResponse(200, { id: "u1", email: "a@example.com", full_name: "A" }),
      );

      const user = await login({ email: "a@example.com", password: "password123" });

      expect(user).toEqual({ id: "u1", email: "a@example.com", full_name: "A" });
      const [url] = nthFetchCall(fetchMock, 0);
      expect(url).toBe(`${BASE_URL}/api/v1/auth/login`);
    });

    it("login() never retries on 401 (a failed login is a credentials error, not a stale session)", async () => {
      fetchMock.mockResolvedValueOnce(jsonResponse(401, { detail: "invalid credentials" }));

      await expect(login({ email: "a@example.com", password: "wrong" })).rejects.toBeInstanceOf(
        UnauthorizedError,
      );
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });

    it("refreshSession() posts to /auth/refresh and never retries on its own 401", async () => {
      fetchMock.mockResolvedValueOnce(jsonResponse(401, { detail: "invalid refresh token" }));

      await expect(refreshSession()).rejects.toBeInstanceOf(UnauthorizedError);

      expect(fetchMock).toHaveBeenCalledTimes(1);
      const [url] = nthFetchCall(fetchMock, 0);
      expect(url).toBe(`${BASE_URL}/api/v1/auth/refresh`);
    });

    it("logout() posts to /auth/logout and resolves with no value on a 204", async () => {
      fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));

      const result = await logout();

      expect(result).toBeUndefined();
      const [url, init] = nthFetchCall(fetchMock, 0);
      expect(url).toBe(`${BASE_URL}/api/v1/auth/logout`);
      expect(init.method).toBe("POST");
      expect(init.body).toBeUndefined();
    });

    it("logout() succeeds even with no prior session (backend treats it as a no-op, not an error)", async () => {
      // Mirrors the backend's own documented behavior (LogoutUseCase):
      // an unknown/absent session still resolves 204, not an error.
      fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));

      await expect(logout()).resolves.toBeUndefined();
    });
  });

  describe("organizations", () => {
    it("createOrganization() posts name/slug to /organizations", async () => {
      fetchMock.mockResolvedValueOnce(
        jsonResponse(201, { id: "org-1", name: "Acme", slug: "acme" }),
      );

      const org = await createOrganization({ name: "Acme", slug: "acme" });

      expect(org).toEqual({ id: "org-1", name: "Acme", slug: "acme" });
      const [url, init] = nthFetchCall(fetchMock, 0);
      expect(url).toBe(`${BASE_URL}/api/v1/organizations`);
      expect(init.method).toBe("POST");
      expect(init.body).toBe(JSON.stringify({ name: "Acme", slug: "acme" }));
    });
  });

  describe("scans", () => {
    const orgId = "org-1";
    const scanDetail: ScanDetailResponse = {
      id: "scan-1",
      organization_id: orgId,
      target: "example.com",
      scanner_name: "nuclei",
      status: "queued",
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
      triggered_by_user_id: "u1",
      started_at: null,
      completed_at: null,
      workflow_steps: [],
    };

    it("createScan() posts target/scanner_name to /organizations/{id}/scans", async () => {
      fetchMock.mockResolvedValueOnce(jsonResponse(201, scanDetail));

      const scan = await createScan(orgId, { target: "example.com" });

      expect(scan).toEqual(scanDetail);
      const [url, init] = nthFetchCall(fetchMock, 0);
      expect(url).toBe(`${BASE_URL}/api/v1/organizations/${orgId}/scans`);
      expect(init.method).toBe("POST");
      expect(init.body).toBe(JSON.stringify({ target: "example.com" }));
    });

    it("runScan() posts (no body) to /organizations/{id}/scans/{scanId}/run", async () => {
      fetchMock.mockResolvedValueOnce(jsonResponse(202, { ...scanDetail, status: "running" }));

      const scan = await runScan(orgId, "scan-1");

      expect(scan.status).toBe("running");
      const [url, init] = nthFetchCall(fetchMock, 0);
      expect(url).toBe(`${BASE_URL}/api/v1/organizations/${orgId}/scans/scan-1/run`);
      expect(init.method).toBe("POST");
      expect(init.body).toBeUndefined();
    });

    it("getScan() gets /organizations/{id}/scans/{scanId}", async () => {
      fetchMock.mockResolvedValueOnce(jsonResponse(200, scanDetail));

      const scan = await getScan(orgId, "scan-1");

      expect(scan).toEqual(scanDetail);
      const [url, init] = nthFetchCall(fetchMock, 0);
      expect(url).toBe(`${BASE_URL}/api/v1/organizations/${orgId}/scans/scan-1`);
      expect(init.method).toBe("GET");
    });

    it("getScan() retries once on 401 -- unlike auth.ts's wrappers, scan endpoints use the default auth-retry behavior", async () => {
      fetchMock
        .mockResolvedValueOnce(jsonResponse(401, { detail: "expired" }))
        .mockResolvedValueOnce(jsonResponse(200, { id: "u1", email: "a@b.com", full_name: "A" }))
        .mockResolvedValueOnce(jsonResponse(200, scanDetail));

      const scan = await getScan(orgId, "scan-1");

      expect(scan).toEqual(scanDetail);
      expect(fetchMock).toHaveBeenCalledTimes(3);
    });
  });
});
