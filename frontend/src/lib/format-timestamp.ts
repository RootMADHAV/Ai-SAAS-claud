/**
 * Renders an ISO 8601 timestamp (as sent by the backend) in a fixed,
 * timezone-independent form: "YYYY-MM-DD HH:MM:SS UTC". Fixed UTC keeps
 * the output deterministic across machines/tests and unambiguous next to
 * server-side logs. An unparseable value is returned unchanged rather
 * than throwing, so a malformed field can never break the page.
 */
export function formatTimestamp(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return iso;
  }
  return `${date.toISOString().slice(0, 19).replace("T", " ")} UTC`;
}
