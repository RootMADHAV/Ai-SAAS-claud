import { describe, expect, it } from "vitest";

import { formatTimestamp } from "./format-timestamp";

describe("formatTimestamp", () => {
  it("renders an ISO timestamp as a fixed UTC string", () => {
    expect(formatTimestamp("2026-01-02T03:04:05Z")).toBe("2026-01-02 03:04:05 UTC");
  });

  it("normalises a non-UTC offset to UTC", () => {
    expect(formatTimestamp("2026-01-02T05:34:05+05:30")).toBe("2026-01-02 00:04:05 UTC");
  });

  it("returns an unparseable value unchanged instead of throwing", () => {
    expect(formatTimestamp("not-a-date")).toBe("not-a-date");
  });
});
