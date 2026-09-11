"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

import {
  login as apiLogin,
  logout as apiLogout,
  refreshSession,
  register as apiRegister,
} from "@/lib/api/auth";
import type { LoginRequest, RegisterRequest, UserResponse } from "@/lib/api/types";

export type SessionStatus = "loading" | "authenticated" | "unauthenticated";

interface AuthContextValue {
  user: UserResponse | null;
  status: SessionStatus;
  login: (payload: LoginRequest) => Promise<void>;
  register: (payload: RegisterRequest) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

/**
 * Owns the client-side *view* of the session. The session itself lives
 * entirely in the backend's httpOnly cookies (PROJECT_STATE.md section
 * 4, locked) -- this component never reads, stores, or exposes a token
 * value; `user`/`status` only ever mirror what the backend's responses
 * already said, so the UI (nav state, route protection) can react
 * without every consumer making its own network call or reimplementing
 * this bookkeeping.
 *
 * On mount, calls `refreshSession()` (`POST /auth/refresh`) exactly
 * once -- the one way this client can silently discover "is there
 * already a valid session" without the person doing anything, since
 * there is no dedicated `/auth/me` endpoint. This is a design choice
 * documented in `lib/api/auth.ts`'s own docstring, not a gap: `refresh`
 * already returns the same `UserResponse` login/register do, and
 * rotating the refresh token on every app load is exactly the kind of
 * frequent use the backend's rotating-refresh-token design already
 * expects (PROJECT_STATE.md section 4). A ref guards against React 18
 * Strict Mode's dev-only double-effect firing this call twice.
 */
export function AuthProvider({ children }: { children: ReactNode }): JSX.Element {
  const [user, setUser] = useState<UserResponse | null>(null);
  const [status, setStatus] = useState<SessionStatus>("loading");
  const hasCheckedSession = useRef(false);

  useEffect(() => {
    if (hasCheckedSession.current) {
      return undefined;
    }
    hasCheckedSession.current = true;

    let cancelled = false;
    refreshSession()
      .then((restoredUser) => {
        if (!cancelled) {
          setUser(restoredUser);
          setStatus("authenticated");
        }
      })
      .catch(() => {
        // No valid refresh-token cookie, or a network-level failure --
        // both settle to "not logged in", the safe default either way.
        // The API client's own error hierarchy already distinguishes
        // these (UnauthorizedError vs NetworkError) for a caller that
        // needs to; this provider doesn't need to act on which one it
        // was.
        if (!cancelled) {
          setUser(null);
          setStatus("unauthenticated");
        }
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(async (payload: LoginRequest): Promise<void> => {
    const loggedInUser = await apiLogin(payload);
    setUser(loggedInUser);
    setStatus("authenticated");
  }, []);

  const register = useCallback(async (payload: RegisterRequest): Promise<void> => {
    // Deliberately does not touch user/status -- registering does not
    // start a session (backend route docstring, app/api/v1/auth.py).
    // The register page sends the person to /login next.
    await apiRegister(payload);
  }, []);

  const logout = useCallback(async (): Promise<void> => {
    try {
      await apiLogout();
    } catch (error) {
      // A failed network call to /auth/logout doesn't change the
      // outcome from this provider's point of view -- see the comment
      // below. Caught here (not just cleaned up via `finally`, which
      // still rethrows) so callers such as the nav bar's logout button
      // don't need their own error handling for a case that already has
      // a defined, safe outcome; logged so the failure isn't silent.
      console.error("logout() failed to reach the backend:", error);
    } finally {
      // Cleared either way. Local state here is never the source of
      // truth for whether a session exists -- only the backend's
      // cookies are -- so even if the logout request itself failed to
      // reach the backend (NetworkError), the person still asked to
      // log out, and the next authenticated request will 401 (and the
      // API client's own silent-refresh-retry will settle the real
      // state) on its own regardless of what this local state says.
      setUser(null);
      setStatus("unauthenticated");
    }
  }, []);

  return (
    <AuthContext.Provider value={{ user, status, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (context === null) {
    throw new Error("useAuth() must be called within an AuthProvider");
  }
  return context;
}
