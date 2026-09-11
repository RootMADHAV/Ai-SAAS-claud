"use client";

import Link from "next/link";
import { useState } from "react";

import { describeApiError } from "@/lib/api/error-message";
import { runScan } from "@/lib/api/scans";
import { useScanPolling } from "@/lib/scans/use-scan-polling";

interface ScanDetailProps {
  organizationId: string;
  scanId: string;
}

export function ScanDetail({ organizationId, scanId }: ScanDetailProps): JSX.Element {
  const [refetchNonce, setRefetchNonce] = useState(0);
  const { scan, error, isLoading, pollingStopped } = useScanPolling(
    organizationId,
    scanId,
    refetchNonce,
  );
  const [isStartingRun, setIsStartingRun] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);

  async function handleRun(): Promise<void> {
    setRunError(null);
    setIsStartingRun(true);
    try {
      await runScan(organizationId, scanId);
      // Forces useScanPolling's effect to restart immediately, rather
      // than waiting up to POLL_INTERVAL_MS for the next scheduled
      // tick, so the status shown updates promptly after a run/retry.
      setRefetchNonce((n) => n + 1);
    } catch (err) {
      setRunError(describeApiError(err));
    } finally {
      setIsStartingRun(false);
    }
  }

  // Loading: no scan fetched yet at all.
  if (isLoading && scan === null) {
    return <p className="p-8 text-sm text-muted-foreground">Loading scan…</p>;
  }

  // Error: the very first fetch failed -- nothing to show.
  if (error !== null && scan === null) {
    return (
      <div className="p-8">
        <p role="alert" className="text-sm text-destructive">
          {describeApiError(error)}
        </p>
        <Link href="/dashboard" className="mt-2 inline-block text-sm underline">
          Back to dashboard
        </Link>
      </div>
    );
  }

  // Empty: shouldn't happen given the backend always creates a scan
  // with data (defensive, not an expected path).
  if (scan === null) {
    return <p className="p-8 text-sm text-muted-foreground">No scan data.</p>;
  }

  // A scan already RUNNING blocks a new submission -- both server-side
  // (the backend no-ops a re-dispatch of an already-running scan) and
  // here, so the button doesn't invite a duplicate click while a
  // request most likely wouldn't do anything different anyway.
  const canRun = scan.status !== "running" && !isStartingRun;
  const runLabel = scan.status === "queued" ? "Run scan" : "Retry scan";
  const hitSafetyCap =
    pollingStopped && scan.status !== "completed" && scan.status !== "failed" && scan.status !== "cancelled";

  return (
    <div className="p-8">
      <Link href="/dashboard" className="text-sm underline">
        ← Dashboard
      </Link>
      <h1 className="mb-2 mt-2 text-xl font-semibold">Scan: {scan.target}</h1>
      <p className="text-sm text-muted-foreground">Status: {scan.status}</p>

      <div className="mt-4">
        {canRun ? (
          <button
            type="button"
            onClick={() => void handleRun()}
            disabled={isStartingRun}
            className="rounded bg-primary px-3 py-2 text-sm text-primary-foreground disabled:opacity-50"
          >
            {isStartingRun ? "Starting…" : runLabel}
          </button>
        ) : (
          <p className="text-sm text-muted-foreground">Scan is running…</p>
        )}
        {runError && (
          <p role="alert" className="mt-2 text-sm text-destructive">
            {runError}
          </p>
        )}
      </div>

      <h2 className="mb-2 mt-6 text-sm font-semibold">Workflow steps</h2>
      {scan.workflow_steps.length === 0 ? (
        <p className="text-sm text-muted-foreground">No workflow steps yet.</p>
      ) : (
        <ul className="flex flex-col gap-1 text-sm">
          {scan.workflow_steps
            .slice()
            .sort((a, b) => a.step_order - b.step_order)
            .map((step) => (
              <li
                key={step.id}
                className="flex items-center justify-between border-b border-border py-1"
              >
                <span>{step.step_name}</span>
                <span className="text-muted-foreground">{step.status}</span>
              </li>
            ))}
        </ul>
      )}

      {hitSafetyCap && (
        <p className="mt-4 text-xs text-muted-foreground">
          Stopped auto-refreshing. Reload the page to check again.
        </p>
      )}
    </div>
  );
}
