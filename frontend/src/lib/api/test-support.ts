/**
 * Shared helpers for the API client's test suite. Not a `*.test.ts`
 * file itself, so vitest.config.ts's `include` glob does not pick it up
 * as a test file.
 */

import type { MockInstance } from "vitest";

export const BASE_URL = "https://localhost:8000";

export function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

export function emptyResponse(status: number): Response {
  return new Response(null, { status });
}

/**
 * Safe accessor for a mocked fetch call's `[url, init]` args. Plain
 * `mock.calls[n]` is `... | undefined` under this project's
 * `noUncheckedIndexedAccess` (tsconfig.json) -- this throws a clear,
 * assertion-message-style error instead of a bare "possibly undefined"
 * type error, and instead of a silent `undefined` destructure if a
 * test's assumed call count is ever wrong.
 */
export function nthFetchCall(
  mock: MockInstance<(...args: unknown[]) => unknown>,
  index: number,
): [string, RequestInit] {
  const call = mock.mock.calls[index];
  if (!call) {
    throw new Error(
      `Expected a fetch call at index ${index}, but only ${mock.mock.calls.length} call(s) were made.`,
    );
  }
  return call as [string, RequestInit];
}
