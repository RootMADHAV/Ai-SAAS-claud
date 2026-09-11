"use client";

import { useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { useAuth } from "@/lib/auth/context";

/**
 * Client-side route guard -- the *only* route-protection mechanism in
 * this app; deliberately no `middleware.ts`.
 *
 * Verifying the access-token JWT at the edge would mean sharing the
 * backend's `JWT_SECRET` with the frontend/edge runtime -- a real
 * secret-distribution decision this step doesn't take. The alternative,
 * middleware that only checks cookie *presence* (not validity), buys
 * little over what this component already does: it asks the actual,
 * already-known session state (post session-restore-on-load in
 * `AuthProvider`), which is strictly more informed than "is there a
 * cookie" and needs no separate check of its own.
 *
 * Either way this is UX only, never the security boundary -- the
 * backend's `get_current_user`/`require_organization_member` remain the
 * actual authorization control regardless of what this component does
 * or doesn't render (PROJECT_STATE.md section 4). A person could disable
 * JavaScript entirely and every API call would still be exactly as
 * protected as it is today.
 */
export function RequireAuth({ children }: { children: ReactNode }): JSX.Element | null {
  const { status } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (status === "unauthenticated") {
      router.replace("/login");
    }
  }, [status, router]);

  if (status === "loading") {
    return <p className="p-8 text-sm text-muted-foreground">Loading…</p>;
  }
  if (status === "unauthenticated") {
    // Redirecting (effect above) -- render nothing rather than a flash
    // of protected content.
    return null;
  }
  return <>{children}</>;
}
