import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { apiRequest } from "./client";
import {
  ConflictError,
  ForbiddenError,
  NetworkError,
  NotFoundError,
  UnauthorizedError,
  ValidationApiError,
} from "./errors";
import { BASE_URL, emptyResponse, jsonResponse, nthFetchCall } from "./test-support";

const REFRESH_URL = `${BASE_URL}/api/v1/auth/refresh`;

describe("apiRequest", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("sends credentials: include and no body/headers for a bodyless GET request", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { id: "scan-1" }));

    const result = await apiRequest<{ id: string }>("/organizations/org-1/scans/scan-1");

    expect(result).toEqual({ id: "scan-1" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = nthFetchCall(fetchMock, 0);
    expect(url).toBe(`${BASE_URL}/api/v1/organizations/org-1/scans/scan-1`);
    expect(init).toMatchObject({ method: "GET", credentials: "include" });
    expect(init.headers).toBeUndefined();
    expect(init.body).toBeUndefined();
  });

  it("JSON-encodes the body and sets Content-Type for a POST request", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(201, { id: "org-1" }));

    await apiRequest("/organizations", {
      method: "POST",
      body: { name: "Acme", slug: "acme" },
    });

    const [, init] = nthFetchCall(fetchMock, 0);
    expect(init.method).toBe("POST");
    expect(init.headers).toEqual({ "Content-Type": "application/json" });
    expect(init.body).toBe(JSON.stringify({ name: "Acme", slug: "acme" }));
  });

  it.each([
    [403, ForbiddenError],
    [404, NotFoundError],
    [409, ConflictError],
  ] as const)("maps a %i response to %s", async (status, ErrorClass) => {
    fetchMock.mockResolvedValueOnce(jsonResponse(status, { detail: "nope" }));

    const error = await apiRequest("/whatever").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ErrorClass);
    expect((error as InstanceType<typeof ErrorClass>).status).toBe(status);
    expect((error as Error).message).toBe("nope");
  });

  it("maps a 422 response's field-error array to ValidationApiError, not a plain message", async () => {
    const errors = [
      { loc: ["body", "email"], msg: "value is not a valid email address", type: "value_error" },
    ];
    fetchMock.mockResolvedValueOnce(jsonResponse(422, { detail: errors }));

    const error = await apiRequest("/auth/register", {
      method: "POST",
      body: {},
      skipAuthRetry: true,
    }).catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ValidationApiError);
    expect((error as ValidationApiError).status).toBe(422);
    expect((error as ValidationApiError).errors).toEqual(errors);
  });

  it("falls back to a generic message when the error body isn't parseable JSON", async () => {
    fetchMock.mockResolvedValueOnce(new Response("not json", { status: 500 }));

    const error = await apiRequest("/whatever").catch((e: unknown) => e);
    expect((error as Error).message).toBe("Request failed with status 500");
  });

  it("wraps a fetch-level failure (offline/DNS/CORS) in NetworkError", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));

    await expect(apiRequest("/whatever")).rejects.toBeInstanceOf(NetworkError);
  });

  it("returns undefined for a 204 No Content response without attempting to parse a body", async () => {
    fetchMock.mockResolvedValueOnce(emptyResponse(204));

    await expect(apiRequest("/whatever")).resolves.toBeUndefined();
  });

  describe("401 handling", () => {
    it("silently refreshes once, then retries and returns the original request's result", async () => {
      fetchMock
        .mockResolvedValueOnce(jsonResponse(401, { detail: "invalid or expired access token" }))
        .mockResolvedValueOnce(jsonResponse(200, { id: "u1", email: "a@b.com", full_name: "A" }))
        .mockResolvedValueOnce(jsonResponse(200, { id: "scan-1" }));

      const result = await apiRequest<{ id: string }>("/organizations/org-1/scans/scan-1");

      expect(result).toEqual({ id: "scan-1" });
      expect(fetchMock).toHaveBeenCalledTimes(3);
      const [originalUrl] = nthFetchCall(fetchMock, 0);
      const [refreshUrl, refreshInit] = nthFetchCall(fetchMock, 1);
      const [retryUrl] = nthFetchCall(fetchMock, 2);
      expect(refreshUrl).toBe(REFRESH_URL);
      expect(refreshInit).toMatchObject({ method: "POST" });
      expect(retryUrl).toBe(originalUrl);
    });

    it("throws the original UnauthorizedError, with no further retry, when the refresh itself fails", async () => {
      fetchMock
        .mockResolvedValueOnce(jsonResponse(401, { detail: "invalid or expired access token" }))
        .mockResolvedValueOnce(jsonResponse(401, { detail: "invalid refresh token" }));

      const error = await apiRequest("/organizations/org-1/scans/scan-1").catch(
        (e: unknown) => e,
      );

      expect(error).toBeInstanceOf(UnauthorizedError);
      expect((error as Error).message).toBe("invalid or expired access token");
      expect(fetchMock).toHaveBeenCalledTimes(2);
    });

    it("does not attempt a refresh at all when skipAuthRetry is set", async () => {
      fetchMock.mockResolvedValueOnce(jsonResponse(401, { detail: "invalid credentials" }));

      await expect(
        apiRequest("/auth/login", { method: "POST", body: {}, skipAuthRetry: true }),
      ).rejects.toBeInstanceOf(UnauthorizedError);
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });

    it("dedupes concurrent refreshes: two requests that 401 at once share a single /auth/refresh call", async () => {
      const seenCounts = new Map<string, number>();
      fetchMock.mockImplementation(async (url: string) => {
        if (url === REFRESH_URL) {
          return jsonResponse(200, { id: "u1", email: "a@b.com", full_name: "A" });
        }
        const seen = (seenCounts.get(url) ?? 0) + 1;
        seenCounts.set(url, seen);
        if (seen === 1) {
          return jsonResponse(401, { detail: "expired" });
        }
        return jsonResponse(200, { id: url.endsWith("/a") ? "a" : "b" });
      });

      const [resultA, resultB] = await Promise.all([
        apiRequest<{ id: string }>("/organizations/org-1/scans/a"),
        apiRequest<{ id: string }>("/organizations/org-1/scans/b"),
      ]);

      expect(resultA).toEqual({ id: "a" });
      expect(resultB).toEqual({ id: "b" });
      expect(fetchMock).toHaveBeenCalledTimes(5); // 2 originals + 1 shared refresh + 2 retries
      const refreshCalls = fetchMock.mock.calls.filter(([url]) => url === REFRESH_URL);
      expect(refreshCalls).toHaveLength(1);
    });
  });
});
