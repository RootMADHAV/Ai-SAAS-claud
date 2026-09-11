"use client";

import Link from "next/link";

import { useAuth } from "@/lib/auth/context";

/**
 * Basic authenticated/unauthenticated navigation state -- functional
 * only, no visual design polish (explicitly out of this step's scope).
 */
export function NavBar(): JSX.Element {
  const { user, status, logout } = useAuth();

  return (
    <nav className="flex items-center justify-between border-b border-border px-4 py-3 text-sm">
      <Link href="/" className="font-medium">
        Security Platform
      </Link>
      <div className="flex items-center gap-4">
        {status === "loading" && <span className="text-muted-foreground">…</span>}
        {status === "unauthenticated" && (
          <>
            <Link href="/login">Log in</Link>
            <Link href="/register">Register</Link>
          </>
        )}
        {status === "authenticated" && user && (
          <>
            <Link href="/dashboard">Dashboard</Link>
            <span className="text-muted-foreground">{user.email}</span>
            <button type="button" onClick={() => void logout()} className="underline">
              Log out
            </button>
          </>
        )}
      </div>
    </nav>
  );
}
