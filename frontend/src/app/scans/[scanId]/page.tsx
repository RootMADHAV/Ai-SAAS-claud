"use client";

import Link from "next/link";
import { useParams } from "next/navigation";

import { ScanDetail } from "@/components/scan-detail";
import { RequireAuth } from "@/lib/auth/require-auth";
import { useSelectedOrganizationId } from "@/lib/organization/use-selected-organization";

function ScanDetailPageContent(): JSX.Element {
  const params = useParams<{ scanId: string }>();
  const { organizationId, isLoaded } = useSelectedOrganizationId();

  if (!isLoaded) {
    return <p className="p-8 text-sm text-muted-foreground">Loading…</p>;
  }

  if (organizationId === null) {
    // Only reachable by navigating here directly without ever visiting
    // /dashboard first (e.g. a bookmarked/shared link, or a different
    // browser profile with nothing in localStorage) -- there is no
    // scan-to-organization lookup this client can do on its own.
    return (
      <div className="p-8">
        <p className="text-sm text-muted-foreground">No organization selected.</p>
        <Link href="/dashboard" className="mt-2 inline-block text-sm underline">
          Go to dashboard
        </Link>
      </div>
    );
  }

  return <ScanDetail organizationId={organizationId} scanId={params.scanId} />;
}

export default function ScanDetailPage(): JSX.Element {
  return (
    <RequireAuth>
      <ScanDetailPageContent />
    </RequireAuth>
  );
}
