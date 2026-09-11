"use client";

import { useCallback, useEffect, useState } from "react";

const STORAGE_KEY = "security-platform:selected-organization-id";

interface SelectedOrganization {
  organizationId: string | null;
  setOrganizationId: (id: string) => void;
  /** False until the initial localStorage read (client-only, see below)
   * has happened -- lets callers show a brief loading state instead of
   * flashing the "create an organization" empty state before it's known
   * whether one is already stored. */
  isLoaded: boolean;
}

/**
 * The one piece of organization state this MVP needs client-side: which
 * organization the signed-in person is currently working in.
 *
 * This is emphatically not a credential, and storing it here is not the
 * same category of thing as the token-storage rule this app follows
 * elsewhere: an organization id alone grants no access to anything. Every
 * request built with it still goes through the browser's httpOnly
 * session cookies (`credentials: "include"`, `lib/api/client.ts`), and
 * the backend's own `require_organization_member` check
 * (PROJECT_STATE.md section 4) is what actually decides whether this id
 * is usable for this person -- a stale or foreign id here just surfaces
 * as an ordinary 403/404 from the API, handled the same way any other
 * API error is (see `error-message.ts`).
 *
 * Stores a single id, not a list -- no multi-org switcher UI. The
 * backend has no "list my organizations" endpoint yet
 * (PROJECT_STATE.md section 13); if one is added later, this is the one
 * place that would change to source from it instead of purely from
 * whatever the person last created or was handed client-side.
 *
 * Persisted to `localStorage`, not React state alone (which would lose
 * it on every navigation between pages in the App Router -- each route
 * is a separate component tree) and not `sessionStorage` (which would
 * lose it on tab close, worse for a value that's cheap to keep around
 * and costly to require re-entering).
 */
export function useSelectedOrganizationId(): SelectedOrganization {
  const [organizationId, setOrganizationIdState] = useState<string | null>(null);
  const [isLoaded, setIsLoaded] = useState(false);

  useEffect(() => {
    // Runs client-only (effects never execute during server rendering),
    // which is what makes this safe without a `typeof window` guard --
    // reading localStorage during render (e.g. via a lazy useState
    // initializer) would not be.
    try {
      setOrganizationIdState(window.localStorage.getItem(STORAGE_KEY));
    } catch {
      // localStorage can throw (private browsing in some browsers,
      // storage disabled by policy) -- treated the same as "nothing
      // stored yet" rather than crashing the page over a convenience.
    }
    setIsLoaded(true);
  }, []);

  const setOrganizationId = useCallback((id: string) => {
    setOrganizationIdState(id);
    try {
      window.localStorage.setItem(STORAGE_KEY, id);
    } catch {
      // Same as above -- in-memory state still updates for this page
      // load even if persistence fails.
    }
  }, []);

  return { organizationId, setOrganizationId, isLoaded };
}
