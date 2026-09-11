"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";

import { ConflictError, ValidationApiError } from "@/lib/api/errors";
import { useAuth } from "@/lib/auth/context";

export default function RegisterPage(): JSX.Element {
  const { register } = useAuth();

  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [didSucceed, setDidSucceed] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);
    try {
      await register({ email, password, full_name: fullName });
      // Registering does not start a session (see AuthProvider's own
      // docstring) -- show a success state with a link to /login rather
      // than auto-redirecting into a session that doesn't exist yet.
      setDidSucceed(true);
    } catch (err) {
      if (err instanceof ConflictError) {
        setError("An account with this email already exists.");
      } else if (err instanceof ValidationApiError) {
        setError(err.message);
      } else {
        setError("Something went wrong. Please try again.");
      }
    } finally {
      setIsSubmitting(false);
    }
  }

  if (didSucceed) {
    return (
      <main className="mx-auto max-w-sm p-8">
        <h1 className="mb-4 text-xl font-semibold">Account created</h1>
        <p className="mb-4 text-sm text-muted-foreground">
          Your account has been created. Log in to continue.
        </p>
        <Link href="/login" className="underline">
          Log in
        </Link>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-sm p-8">
      <h1 className="mb-4 text-xl font-semibold">Register</h1>
      <form onSubmit={handleSubmit} className="flex flex-col gap-3">
        <label className="flex flex-col gap-1 text-sm">
          Full name
          <input
            required
            autoComplete="name"
            value={fullName}
            onChange={(event) => setFullName(event.target.value)}
            className="rounded border border-input px-3 py-2"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          Email
          <input
            type="email"
            required
            autoComplete="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            className="rounded border border-input px-3 py-2"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          Password
          <input
            type="password"
            required
            minLength={8}
            autoComplete="new-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className="rounded border border-input px-3 py-2"
          />
        </label>
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
        <button
          type="submit"
          disabled={isSubmitting}
          className="rounded bg-primary px-3 py-2 text-primary-foreground disabled:opacity-50"
        >
          {isSubmitting ? "Creating account…" : "Register"}
        </button>
      </form>
      <p className="mt-4 text-sm">
        Already have an account?{" "}
        <Link href="/login" className="underline">
          Log in
        </Link>
      </p>
    </main>
  );
}
