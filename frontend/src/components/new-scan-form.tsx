"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { describeApiError } from "@/lib/api/error-message";
import { createScan } from "@/lib/api/scans";

interface NewScanFormProps {
  organizationId: string;
}

/** Scan creation: target only. The scanner is not user-selectable in
 * this milestone -- it is shown as a label and not sent explicitly, so
 * the backend applies its default (`scanner_name="nuclei"`; the backend
 * contract also accepts "nmap", see `ScannerName`). */
export function NewScanForm({ organizationId }: NewScanFormProps): JSX.Element {
  const router = useRouter();
  const [target, setTarget] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);
    try {
      const scan = await createScan(organizationId, { target });
      router.push(`/scans/${scan.id}`);
    } catch (err) {
      setError(describeApiError(err));
      setIsSubmitting(false);
    }
  }

  return (
    <div className="p-8">
      <h1 className="mb-2 text-xl font-semibold">New scan</h1>
      <form onSubmit={handleSubmit} className="flex max-w-sm flex-col gap-3">
        <label className="flex flex-col gap-1 text-sm">
          Target
          <input
            required
            placeholder="example.com"
            value={target}
            onChange={(event) => setTarget(event.target.value)}
            className="rounded border border-input px-3 py-2"
          />
        </label>
        <p className="text-sm text-muted-foreground">Scanner: nuclei (fixed for this MVP)</p>
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
          {isSubmitting ? "Starting…" : "Start scan"}
        </button>
      </form>
    </div>
  );
}
