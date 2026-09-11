"use client";

import { useState, type FormEvent } from "react";

import { describeApiError } from "@/lib/api/error-message";
import { createOrganization } from "@/lib/api/organizations";

interface CreateOrganizationFormProps {
  onCreated: (organizationId: string) => void;
}

/**
 * The organization-bootstrap step: a signed-in person with no
 * organization yet needs exactly one before they can run a scan (every
 * Scanning route requires `require_organization_member`). No multi-org
 * management here -- this creates one and hands its id back to the
 * caller, which is all `useSelectedOrganizationId` needs.
 */
export function CreateOrganizationForm({ onCreated }: CreateOrganizationFormProps): JSX.Element {
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);
    try {
      const organization = await createOrganization({ name, slug });
      onCreated(organization.id);
    } catch (err) {
      setError(describeApiError(err));
      setIsSubmitting(false);
    }
  }

  return (
    <div className="p-8">
      <h1 className="mb-2 text-xl font-semibold">Create an organization</h1>
      <p className="mb-4 text-sm text-muted-foreground">
        You need an organization before you can run scans.
      </p>
      <form onSubmit={handleSubmit} className="flex max-w-sm flex-col gap-3">
        <label className="flex flex-col gap-1 text-sm">
          Name
          <input
            required
            value={name}
            onChange={(event) => setName(event.target.value)}
            className="rounded border border-input px-3 py-2"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          Slug
          <input
            required
            pattern="[a-z0-9]+(-[a-z0-9]+)*"
            title="Lowercase letters, numbers, and hyphens only"
            value={slug}
            onChange={(event) => setSlug(event.target.value)}
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
          {isSubmitting ? "Creating…" : "Create organization"}
        </button>
      </form>
    </div>
  );
}
