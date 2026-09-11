import { describe, expect, it } from "vitest";

import { describeApiError } from "./error-message";
import {
  ConflictError,
  ForbiddenError,
  NetworkError,
  NotFoundError,
  UnauthorizedError,
  ValidationApiError,
} from "./errors";

describe("describeApiError", () => {
  it("returns the field-error summary for a ValidationApiError", () => {
    const error = new ValidationApiError([
      { loc: ["body", "slug"], msg: "slug must be lowercase", type: "value_error" },
    ]);
    expect(describeApiError(error)).toBe("slug must be lowercase");
  });

  it("returns a session-expired message for UnauthorizedError", () => {
    expect(describeApiError(new UnauthorizedError())).toBe(
      "Your session has expired. Please log in again.",
    );
  });

  it("returns an access message for ForbiddenError", () => {
    expect(describeApiError(new ForbiddenError())).toBe("You don't have access to do that.");
  });

  it("returns a not-found message for NotFoundError", () => {
    expect(describeApiError(new NotFoundError())).toBe("Not found.");
  });

  it("returns the backend's own message for ConflictError", () => {
    const error = new ConflictError("organization slug 'acme' is already taken");
    expect(describeApiError(error)).toBe("organization slug 'acme' is already taken");
  });

  it("returns a connectivity message for NetworkError", () => {
    expect(describeApiError(new NetworkError("Network request failed"))).toBe(
      "Could not reach the server. Check your connection and try again.",
    );
  });

  it("falls back to a generic message for a non-API error", () => {
    expect(describeApiError(new Error("boom"))).toBe("Something went wrong. Please try again.");
  });

  it("falls back to a generic message for a non-Error thrown value", () => {
    expect(describeApiError("just a string")).toBe("Something went wrong. Please try again.");
  });
});
