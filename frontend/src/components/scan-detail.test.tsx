// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { NetworkError } from "@/lib/api/errors";
import type { ScanDetailResponse, ScanStatus, WorkflowStepResponse } from "@/lib/api/types";

import { ScanDetail } from "./scan-detail";

const mocks = vi.hoisted(() => ({
  runScan: vi.fn(),
  useScanPolling: vi.fn(),
}));

vi.mock("@/lib/api/scans", () => ({ runScan: mocks.runScan }));
vi.mock("@/lib/scans/use-scan-polling", () => ({ useScanPolling: mocks.useScanPolling }));

function step(overrides: Partial<WorkflowStepResponse> = {}): WorkflowStepResponse {
  return {
    id: "step-1",
    step_name: "validate_target",
    step_order: 1,
    status: "pending",
    retry_count: 0,
    started_at: null,
    completed_at: null,
    error_message: null,
    ...overrides,
  };
}

function scanWith(
  status: ScanStatus,
  overrides: Partial<ScanDetailResponse> = {},
): ScanDetailResponse {
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
    ...overrides,
  };
}

function polling(scan: ScanDetailResponse | null, extra: Record<string, unknown> = {}): void {
  mocks.useScanPolling.mockReturnValue({
    scan,
    error: null,
    isLoading: false,
    pollingStopped: false,
    ...extra,
  });
}

function renderDetail(): void {
  render(<ScanDetail organizationId="org-1" scanId="scan-1" />);
}

describe("ScanDetail", () => {
  beforeEach(() => {
    mocks.runScan.mockReset();
    mocks.useScanPolling.mockReset();
  });

  afterEach(() => {
    cleanup();
  });

  it("shows a loading state before the first fetch completes", () => {
    polling(null, { isLoading: true });
    renderDetail();
    expect(screen.getByText("Loading scan…")).toBeTruthy();
  });

  it("shows the error and a way back when the first fetch fails", () => {
    polling(null, { error: new NetworkError("down") });
    renderDetail();
    expect(screen.getByRole("alert").textContent).toContain("Could not reach the server");
    expect(screen.getByText("Back to dashboard")).toBeTruthy();
  });

  it("shows target, status and scanner name (nmap included)", () => {
    polling(scanWith("completed", { scanner_name: "nmap" }));
    renderDetail();
    expect(screen.getByText("Scan: example.com")).toBeTruthy();
    expect(screen.getByText("Status: completed")).toBeTruthy();
    expect(screen.getByText("Scanner: nmap")).toBeTruthy();
  });

  it("shows 'No workflow steps yet.' when there are none", () => {
    polling(scanWith("queued"));
    renderDetail();
    expect(screen.getByText("No workflow steps yet.")).toBeTruthy();
  });

  it("lists steps in step_order with name and status", () => {
    polling(
      scanWith("running", {
        workflow_steps: [
          step({ id: "b", step_name: "execute_scanner", step_order: 2, status: "running" }),
          step({ id: "a", step_name: "validate_target", step_order: 1, status: "completed" }),
        ],
      }),
    );
    renderDetail();
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(within(items[0] as HTMLElement).getByText("validate_target")).toBeTruthy();
    expect(within(items[1] as HTMLElement).getByText("execute_scanner")).toBeTruthy();
  });

  it("shows retry count, timestamps and error message for a failed step", () => {
    polling(
      scanWith("failed", {
        workflow_steps: [
          step({
            step_name: "execute_scanner",
            status: "failed",
            retry_count: 2,
            started_at: "2026-01-02T03:04:05Z",
            completed_at: "2026-01-02T03:05:06Z",
            error_message: "scanner exited with code 1",
          }),
        ],
      }),
    );
    renderDetail();
    const item = screen.getByRole("listitem");
    expect(within(item).getByText("Retries: 2")).toBeTruthy();
    expect(within(item).getByText("Started: 2026-01-02 03:04:05 UTC")).toBeTruthy();
    expect(within(item).getByText("Completed: 2026-01-02 03:05:06 UTC")).toBeTruthy();
    expect(within(item).getByText("Error: scanner exited with code 1")).toBeTruthy();
  });

  it("omits retries, timestamps and error for a step that has none", () => {
    polling(scanWith("queued", { workflow_steps: [step()] }));
    renderDetail();
    const item = screen.getByRole("listitem");
    expect(within(item).queryByText(/Retries:/)).toBeNull();
    expect(within(item).queryByText(/Started:/)).toBeNull();
    expect(within(item).queryByText(/Completed:/)).toBeNull();
    expect(within(item).queryByText(/Error:/)).toBeNull();
  });

  it("offers 'Run scan' for a queued scan and 'Retry scan' for a failed one", () => {
    polling(scanWith("queued"));
    renderDetail();
    expect(screen.getByRole("button", { name: "Run scan" })).toBeTruthy();
    cleanup();

    polling(scanWith("failed"));
    renderDetail();
    expect(screen.getByRole("button", { name: "Retry scan" })).toBeTruthy();
  });

  it("hides the run button while the scan is running", () => {
    polling(scanWith("running"));
    renderDetail();
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByText("Scan is running…")).toBeTruthy();
  });

  it("runs the scan and restarts polling with a new refetch nonce", async () => {
    polling(scanWith("queued"));
    mocks.runScan.mockResolvedValueOnce(scanWith("running"));
    renderDetail();

    fireEvent.click(screen.getByRole("button", { name: "Run scan" }));

    await waitFor(() => expect(mocks.runScan).toHaveBeenCalledWith("org-1", "scan-1"));
    await waitFor(() =>
      expect(mocks.useScanPolling).toHaveBeenLastCalledWith("org-1", "scan-1", 1),
    );
  });

  it("shows an error when the run request fails", async () => {
    polling(scanWith("queued"));
    mocks.runScan.mockRejectedValueOnce(new NetworkError("down"));
    renderDetail();

    fireEvent.click(screen.getByRole("button", { name: "Run scan" }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Could not reach the server");
  });

  it("tells the user to reload when polling hit its safety cap on a non-terminal scan", () => {
    polling(scanWith("running"), { pollingStopped: true });
    renderDetail();
    expect(screen.getByText(/Stopped auto-refreshing/)).toBeTruthy();
  });
});
