// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ForbiddenError } from "@/lib/api/errors";

import { NewScanForm } from "./new-scan-form";

const mocks = vi.hoisted(() => ({
  createScan: vi.fn(),
  push: vi.fn(),
}));

vi.mock("@/lib/api/scans", () => ({ createScan: mocks.createScan }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mocks.push }) }));

describe("NewScanForm", () => {
  beforeEach(() => {
    mocks.createScan.mockReset();
    mocks.push.mockReset();
  });

  afterEach(() => {
    cleanup();
  });

  it("renders the target input and the fixed scanner label", () => {
    render(<NewScanForm organizationId="org-1" />);

    expect(screen.getByLabelText("Target")).toBeTruthy();
    expect(screen.getByText(/Scanner: nuclei/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Start scan" })).toBeTruthy();
  });

  it("creates the scan for the organization and navigates to its detail page", async () => {
    mocks.createScan.mockResolvedValueOnce({ id: "scan-42" });
    render(<NewScanForm organizationId="org-1" />);

    fireEvent.change(screen.getByLabelText("Target"), { target: { value: "example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Start scan" }));

    await waitFor(() => expect(mocks.push).toHaveBeenCalledWith("/scans/scan-42"));
    expect(mocks.createScan).toHaveBeenCalledWith("org-1", { target: "example.com" });
  });

  it("disables the button while submitting", async () => {
    mocks.createScan.mockReturnValueOnce(new Promise(() => {}));
    render(<NewScanForm organizationId="org-1" />);

    fireEvent.change(screen.getByLabelText("Target"), { target: { value: "example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Start scan" }));

    const button = await screen.findByRole("button", { name: "Starting…" });
    expect((button as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows an error, stays on the page and re-enables the button when creation is rejected (e.g. 403 for a viewer)", async () => {
    mocks.createScan.mockRejectedValueOnce(new ForbiddenError());
    render(<NewScanForm organizationId="org-1" />);

    fireEvent.change(screen.getByLabelText("Target"), { target: { value: "bad" } });
    fireEvent.click(screen.getByRole("button", { name: "Start scan" }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("You don't have access to do that.");
    expect(mocks.push).not.toHaveBeenCalled();
    const button = screen.getByRole("button", { name: "Start scan" });
    expect((button as HTMLButtonElement).disabled).toBe(false);
  });
});
