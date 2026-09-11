// @vitest-environment jsdom
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { useSelectedOrganizationId } from "./use-selected-organization";

const STORAGE_KEY = "security-platform:selected-organization-id";

describe("useSelectedOrganizationId", () => {
  afterEach(() => {
    window.localStorage.clear();
  });

  it("starts with isLoaded=false, then loads null when nothing is stored", async () => {
    const { result } = renderHook(() => useSelectedOrganizationId());

    await waitFor(() => expect(result.current.isLoaded).toBe(true));
    expect(result.current.organizationId).toBeNull();
  });

  it("loads a previously-stored id from localStorage on mount", async () => {
    window.localStorage.setItem(STORAGE_KEY, "org-123");

    const { result } = renderHook(() => useSelectedOrganizationId());

    await waitFor(() => expect(result.current.isLoaded).toBe(true));
    expect(result.current.organizationId).toBe("org-123");
  });

  it("setOrganizationId() updates state and persists to localStorage", async () => {
    const { result } = renderHook(() => useSelectedOrganizationId());
    await waitFor(() => expect(result.current.isLoaded).toBe(true));

    act(() => {
      result.current.setOrganizationId("org-456");
    });

    expect(result.current.organizationId).toBe("org-456");
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe("org-456");
  });

  it("a fresh mount picks up an id set by a previous mount (survives 'reload')", async () => {
    const first = renderHook(() => useSelectedOrganizationId());
    await waitFor(() => expect(first.result.current.isLoaded).toBe(true));
    act(() => {
      first.result.current.setOrganizationId("org-789");
    });
    first.unmount();

    const second = renderHook(() => useSelectedOrganizationId());
    await waitFor(() => expect(second.result.current.isLoaded).toBe(true));
    expect(second.result.current.organizationId).toBe("org-789");
  });
});
