// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import DashboardPage from "./page";

const mocks = vi.hoisted(() => ({
  useAuth: vi.fn(),
  useSelectedOrganizationId: vi.fn(),
  replace: vi.fn(),
  push: vi.fn(),
  createOrganization: vi.fn(),
  createScan: vi.fn(),
}));

vi.mock("@/lib/auth/context", () => ({ useAuth: mocks.useAuth }));
vi.mock("@/lib/organization/use-selected-organization", () => ({
  useSelectedOrganizationId: mocks.useSelectedOrganizationId,
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mocks.replace, push: mocks.push }),
}));
vi.mock("@/lib/api/organizations", () => ({ createOrganization: mocks.createOrganization }));
vi.mock("@/lib/api/scans", () => ({ createScan: mocks.createScan }));

function selected(organizationId: string | null, isLoaded = true): void {
  mocks.useSelectedOrganizationId.mockReturnValue({
    organizationId,
    setOrganizationId: vi.fn(),
    isLoaded,
  });
}

describe("DashboardPage", () => {
  beforeEach(() => {
    for (const mock of Object.values(mocks)) {
      mock.mockReset();
    }
    mocks.useAuth.mockReturnValue({ status: "authenticated" });
  });

  afterEach(() => {
    cleanup();
  });

  it("shows a loading state while the session is being restored", () => {
    mocks.useAuth.mockReturnValue({ status: "loading" });
    selected(null);
    render(<DashboardPage />);
    expect(screen.getByText("Loading…")).toBeTruthy();
    expect(mocks.replace).not.toHaveBeenCalled();
  });

  it("redirects to /login and renders nothing when unauthenticated", () => {
    mocks.useAuth.mockReturnValue({ status: "unauthenticated" });
    selected(null);
    const { container } = render(<DashboardPage />);
    expect(mocks.replace).toHaveBeenCalledWith("/login");
    expect(container.textContent).toBe("");
  });

  it("shows a loading state until the stored organization has been read", () => {
    selected(null, false);
    render(<DashboardPage />);
    expect(screen.getByText("Loading…")).toBeTruthy();
    expect(screen.queryByText("Create an organization")).toBeNull();
  });

  it("asks the user to create an organization when none is selected", () => {
    selected(null);
    render(<DashboardPage />);
    expect(screen.getByText("Create an organization")).toBeTruthy();
    expect(screen.queryByText("New scan")).toBeNull();
  });

  it("shows the new-scan form once an organization is selected", () => {
    selected("org-1");
    render(<DashboardPage />);
    expect(screen.getByText("New scan")).toBeTruthy();
    expect(screen.getByLabelText("Target")).toBeTruthy();
    expect(screen.queryByText("Create an organization")).toBeNull();
  });
});
