"use client";

import { useEffect, useRef, useState } from "react";

import { getScan } from "@/lib/api/scans";
import type { ScanDetailResponse, ScanStatus } from "@/lib/api/types";

const POLL_INTERVAL_MS = 3000;
/** Safety bound, not an expected duration -- ~10 minutes at the interval
 * above. A scan finishing normally stops polling well before this via
 * the terminal-status check; this only guards against polling forever
 * if a scan is somehow stuck, so a tab left open doesn't keep hitting
 * the API indefinitely. */
const MAX_POLLS = 200;

const TERMINAL_STATUSES: ReadonlySet<ScanStatus> = new Set(["completed", "failed", "cancelled"]);

interface ScanPollingState {
  scan: ScanDetailResponse | null;
  error: unknown;
  isLoading: boolean;
  /** True once polling has stopped for any reason -- terminal status,
   * the safety cap, or an error. Distinct from "scan is done": check
   * `scan.status` for that; this is about whether auto-refresh is still
   * happening. */
  pollingStopped: boolean;
}

/**
 * Polls `GET /organizations/{organizationId}/scans/{scanId}` on a fixed
 * interval (recursive `setTimeout`, not `setInterval` -- guarantees the
 * next poll never overlaps a still-in-flight previous one) until the
 * scan reaches a terminal status, the component unmounts, or `MAX_POLLS`
 * is hit.
 *
 * `refetchNonce` lets a caller (the run/retry button) force an
 * immediate extra fetch, bypassing the wait for the next scheduled
 * tick, by incrementing a counter that's in this hook's effect
 * dependency array -- restarting the whole polling loop fresh rather
 * than trying to reach into an in-flight timer from outside it.
 */
export function useScanPolling(
  organizationId: string,
  scanId: string,
  refetchNonce: number,
): ScanPollingState {
  const [scan, setScan] = useState<ScanDetailResponse | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [pollingStopped, setPollingStopped] = useState(false);
  const pollCountRef = useRef(0);

  useEffect(() => {
    let cancelled = false;
    let timeoutId: ReturnType<typeof setTimeout> | undefined;
    pollCountRef.current = 0;
    setIsLoading(true);
    setPollingStopped(false);

    async function poll(): Promise<void> {
      try {
        const result = await getScan(organizationId, scanId);
        if (cancelled) {
          return;
        }
        setScan(result);
        setError(null);
        setIsLoading(false);
        pollCountRef.current += 1;

        if (TERMINAL_STATUSES.has(result.status)) {
          setPollingStopped(true);
          return;
        }
        if (pollCountRef.current >= MAX_POLLS) {
          setPollingStopped(true);
          return;
        }
        timeoutId = setTimeout(() => {
          void poll();
        }, POLL_INTERVAL_MS);
      } catch (err) {
        if (cancelled) {
          return;
        }
        setError(err);
        setIsLoading(false);
        setPollingStopped(true);
      }
    }

    void poll();

    return () => {
      cancelled = true;
      if (timeoutId !== undefined) {
        clearTimeout(timeoutId);
      }
    };
  }, [organizationId, scanId, refetchNonce]);

  return { scan, error, isLoading, pollingStopped };
}
