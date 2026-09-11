// @vitest-environment jsdom
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ScanDetailResponse, ScanStatus } from "@/lib/api/types";

import { useScanPolling } from "./use-scan-polling";

const mocks = vi.hoisted(() => ({
  getScan: vi.fn(),
}));

vi.mock("@/lib/api/scans", () => mocks);

function scanWith(status: ScanStatus): ScanDetailResponse {
  return {
    id: "scan-1",
    organization_id: "org-1",
    target: "example.com",
    scanner_name: "nuclei",
    status,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    triggered_by_user_id: "u1",
    started_at: null,
    completed_at: null,
    workflow_steps: [],
  };
}

/**
 * Flushes pending microtasks/timers under fake timers. Deliberately not
 * `@testing-library/react`'s `waitFor` -- `waitFor` polls on its own
 * internal `setInterval`, which `vi.useFakeTimers()` also fakes, so it
 * never re-checks unless *that* timer is separately advanced too. This
 * is the more direct, documented-correct way to let a chain of
 * `await mockedPromise()` -> setState -> `setTimeout(...)` settle.
 */
async function flush(ms = 0): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

describe("useScanPolling", () => {
  beforeEach(() => {
    mocks.getScan.mockReset();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("fetches immediately, keeps polling while non-terminal, and stops once terminal", async () => {
    mocks.getScan
      .mockResolvedValueOnce(scanWith("queued"))
      .mockResolvedValueOnce(scanWith("running"))
      .mockResolvedValueOnce(scanWith("completed"));

    const { result } = renderHook(() => useScanPolling("org-1", "scan-1", 0));
    await flush();

    expect(result.current.scan?.status).toBe("queued");
    expect(result.current.pollingStopped).toBe(false);

    await flush(3000);
    expect(result.current.scan?.status).toBe("running");
    expect(result.current.pollingStopped).toBe(false);

    await flush(3000);
    expect(result.current.scan?.status).toBe("completed");
    expect(result.current.pollingStopped).toBe(true);
    expect(mocks.getScan).toHaveBeenCalledTimes(3);

    // No further calls even after more time passes -- confirms polling
    // actually stopped, not just "hasn't ticked yet".
    await flush(10000);
    expect(mocks.getScan).toHaveBeenCalledTimes(3);
  });

  it("does not poll again after the component unmounts", async () => {
    mocks.getScan.mockResolvedValue(scanWith("queued"));

    const { unmount } = renderHook(() => useScanPolling("org-1", "scan-1", 0));
    await flush();
    expect(mocks.getScan).toHaveBeenCalledTimes(1);

    unmount();
    await flush(10000);

    expect(mocks.getScan).toHaveBeenCalledTimes(1);
  });

  it("stops polling and surfaces the error when a fetch fails", async () => {
    mocks.getScan.mockRejectedValueOnce(new Error("not found"));

    const { result } = renderHook(() => useScanPolling("org-1", "scan-1", 0));
    await flush();

    expect(result.current.pollingStopped).toBe(true);
    expect(result.current.error).toBeInstanceOf(Error);
    expect(result.current.scan).toBeNull();

    await flush(10000);
    expect(mocks.getScan).toHaveBeenCalledTimes(1);
  });

  it("bumping refetchNonce triggers an immediate extra fetch without waiting for the next tick", async () => {
    mocks.getScan.mockResolvedValue(scanWith("queued"));

    const { rerender } = renderHook(
      ({ nonce }: { nonce: number }) => useScanPolling("org-1", "scan-1", nonce),
      { initialProps: { nonce: 0 } },
    );
    await flush();
    expect(mocks.getScan).toHaveBeenCalledTimes(1);

    rerender({ nonce: 1 });
    await flush();

    expect(mocks.getScan).toHaveBeenCalledTimes(2);
  });

  it("stops polling once the safety cap is reached, even if the scan never reaches a terminal status", async () => {
    mocks.getScan.mockResolvedValue(scanWith("running"));

    const { result } = renderHook(() => useScanPolling("org-1", "scan-1", 0));
    await flush();
    expect(mocks.getScan).toHaveBeenCalledTimes(1);

    // 200 total polls (MAX_POLLS), 3000ms apart -- fake timers make this
    // instantaneous in real wall-clock test time.
    await flush(3000 * 199);

    expect(result.current.pollingStopped).toBe(true);
    expect(mocks.getScan).toHaveBeenCalledTimes(200);

    await flush(10000);
    expect(mocks.getScan).toHaveBeenCalledTimes(200);
  });
});
