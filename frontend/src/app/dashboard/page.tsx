"use client";

import { CreateOrganizationForm } from "@/components/create-organization-form";
import { NewScanForm } from "@/components/new-scan-form";
import { RequireAuth } from "@/lib/auth/require-auth";
import { useSelectedOrganizationId } from "@/lib/organization/use-selected-organization";

function DashboardContent(): JSX.Element {
  const { organizationId, setOrganizationId, isLoaded } = useSelectedOrganizationId();

  if (!isLoaded) {
    return <p className="p-8 text-sm text-muted-foreground">Loading…</p>;
  }

  if (organizationId === null) {
    return <CreateOrganizationForm onCreated={setOrganizationId} />;
  }

  return <NewScanForm organizationId={organizationId} />;
}

export default function DashboardPage(): JSX.Element {
  return (
    <RequireAuth>
      <DashboardContent />
    </RequireAuth>
  );
}
