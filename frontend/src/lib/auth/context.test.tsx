// @vitest-environment jsdom
/**
 * Component-level test for the session-state logic Step 3 actually
 * adds -- mount-time session restore, login/register/logout state
 * transitions -- with the API layer mocked, since that layer already
 * has its own full test coverage (client.test.ts, endpoints.test.ts).
 * A probe component exercises the context the way a real page would,
 * without pulling in next/navigation or full page rendering.
 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider, useAuth } from "./context";

const mocks = vi.hoisted(() => ({
  login: vi.fn(),
  logout: vi.fn(),
  refreshSession: vi.fn(),
  register: vi.fn(),
}));

vi.mock("@/lib/api/auth", () => mocks);

function Probe(): JSX.Element {
  const { user, status, login, logout, register } = useAuth();
  return (
    <div>
      <span data-testid="status">{status}</span>
      <span data-testid="email">{user?.email ?? "none"}</span>
      <button onClick={() => void login({ email: "a@b.com", password: "x" })}>do-login</button>
      <button onClick={() => void logout()}>do-logout</button>
      <button
        onClick={() => void register({ email: "a@b.com", password: "x", full_name: "A" })}
      >
        do-register
      </button>
    </div>
  );
}

function renderWithProvider(): void {
  render(
    <AuthProvider>
      <Probe />
    </AuthProvider>,
  );
}

describe("AuthProvider / useAuth", () => {
  beforeEach(() => {
    mocks.login.mockReset();
    mocks.logout.mockReset();
    mocks.refreshSession.mockReset();
    mocks.register.mockReset();
  });

  afterEach(() => {
    cleanup();
  });

  it("starts loading, then restores a session when refreshSession() succeeds", async () => {
    mocks.refreshSession.mockResolvedValue({ id: "u1", email: "a@b.com", full_name: "A" });

    renderWithProvider();

    expect(screen.getByTestId("status").textContent).toBe("loading");
    await waitFor(() => expect(screen.getByTestId("status").textContent).toBe("authenticated"));
    expect(screen.getByTestId("email").textContent).toBe("a@b.com");
    expect(mocks.refreshSession).toHaveBeenCalledTimes(1);
  });

  it("settles to unauthenticated when refreshSession() rejects (no valid session cookie)", async () => {
    mocks.refreshSession.mockRejectedValue(new Error("no session"));

    renderWithProvider();

    await waitFor(() => expect(screen.getByTestId("status").textContent).toBe("unauthenticated"));
    expect(screen.getByTestId("email").textContent).toBe("none");
  });

  it("login() sets user and status to authenticated on success", async () => {
    mocks.refreshSession.mockRejectedValue(new Error("no session"));
    mocks.login.mockResolvedValue({ id: "u2", email: "b@b.com", full_name: "B" });
    renderWithProvider();
    await waitFor(() => expect(screen.getByTestId("status").textContent).toBe("unauthenticated"));

    fireEvent.click(screen.getByText("do-login"));

    await waitFor(() => expect(screen.getByTestId("status").textContent).toBe("authenticated"));
    expect(screen.getByTestId("email").textContent).toBe("b@b.com");
  });

  it("register() does not change status or user -- registering does not start a session", async () => {
    mocks.refreshSession.mockRejectedValue(new Error("no session"));
    mocks.register.mockResolvedValue({ id: "u3", email: "c@b.com", full_name: "C" });
    renderWithProvider();
    await waitFor(() => expect(screen.getByTestId("status").textContent).toBe("unauthenticated"));

    fireEvent.click(screen.getByText("do-register"));
    await waitFor(() => expect(mocks.register).toHaveBeenCalledTimes(1));

    expect(screen.getByTestId("status").textContent).toBe("unauthenticated");
    expect(screen.getByTestId("email").textContent).toBe("none");
  });

  it("logout() clears user/status even when the API call itself fails", async () => {
    mocks.refreshSession.mockResolvedValue({ id: "u4", email: "d@b.com", full_name: "D" });
    mocks.logout.mockRejectedValue(new Error("network error"));
    renderWithProvider();
    await waitFor(() => expect(screen.getByTestId("status").textContent).toBe("authenticated"));

    fireEvent.click(screen.getByText("do-logout"));

    await waitFor(() => expect(screen.getByTestId("status").textContent).toBe("unauthenticated"));
    expect(screen.getByTestId("email").textContent).toBe("none");
  });

  it("useAuth() throws when used outside an AuthProvider", () => {
    function Bare(): JSX.Element {
      useAuth();
      return <></>;
    }
    // Suppress the expected React error-boundary console.error noise
    // for this one intentionally-throwing render.
    const consoleErrorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => render(<Bare />)).toThrow("useAuth() must be called within an AuthProvider");
    consoleErrorSpy.mockRestore();
  });
});
