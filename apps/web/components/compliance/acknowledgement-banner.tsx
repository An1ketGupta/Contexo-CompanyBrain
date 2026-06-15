"use client";

import Link from "next/link";
import { FileText } from "lucide-react";
import useSWR from "swr";

interface PendingItem {
  id: string;
  document_id: string;
  documents?: { name?: string | null } | null;
}

interface PendingResponse {
  pending: PendingItem[];
}

const fetcher = async (url: string): Promise<PendingResponse> => {
  const res = await fetch(url, { credentials: "include" });
  if (!res.ok) return { pending: [] };
  return res.json();
};

/**
 * Dashboard-wide banner shown when the current user has pending policy
 * acknowledgements. Stays out of the way (single line, dismissible only by
 * acknowledging — the banner is the compliance UX, not a notification).
 *
 * SWR revalidates on focus so an acknowledgement made elsewhere makes the
 * banner disappear without a manual refresh.
 */
export function AcknowledgementBanner() {
  const { data } = useSWR<PendingResponse>(
    "/api/compliance/my-pending",
    fetcher,
    { revalidateOnFocus: true, refreshInterval: 60_000 },
  );

  const pending = data?.pending ?? [];
  if (pending.length === 0) return null;

  const label =
    pending.length === 1
      ? "1 policy document needs your acknowledgement"
      : `${pending.length} policy documents need your acknowledgement`;

  return (
    <div className="border-b border-blue-200 bg-blue-50/80 px-4 py-2.5 text-sm dark:border-blue-900/60 dark:bg-blue-950/40">
      <div className="mx-auto flex max-w-7xl items-center gap-3 text-blue-900 dark:text-blue-200">
        <FileText className="h-4 w-4 shrink-0" />
        <span className="flex-1 truncate">{label}</span>
        <Link
          href="/compliance/pending"
          className="shrink-0 rounded-md px-2.5 py-1 font-medium underline-offset-2 hover:underline"
        >
          Review now
        </Link>
      </div>
    </div>
  );
}
