"use client";

import Link from "next/link";
import useSWR from "swr";
import { Plus } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";

interface RequisitionRow {
  id: string;
  role_request: string;
  status: string;
  ats_platform: string | null;
  ats_url: string | null;
  created_at: string;
  published_at: string | null;
}

interface ListResponse {
  requisitions: RequisitionRow[];
}

const fetcher = async (url: string): Promise<ListResponse> => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed (${res.status})`);
  return res.json();
};

const STATUS_COLORS: Record<string, string> = {
  draft: "bg-zinc-100 text-zinc-700",
  published: "bg-emerald-100 text-emerald-700",
  failed: "bg-red-100 text-red-700",
};

export default function RecruitingPage() {
  const { data, error, isLoading } = useSWR<ListResponse>(
    "/api/recruiting/requisitions",
    fetcher,
    { revalidateOnFocus: false },
  );

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6 md:p-8">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Recruiting</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Generate JD variants from your KB, publish to your ATS, set up the hiring tracker.
          </p>
        </div>
        <Button asChild>
          <Link href="/recruiting/new">
            <Plus className="mr-2 h-4 w-4" /> New requisition
          </Link>
        </Button>
      </header>

      {isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-20 w-full" />
          ))}
        </div>
      ) : error ? (
        <div className="rounded border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          Failed to load requisitions.
        </div>
      ) : !data?.requisitions?.length ? (
        <div className="rounded border border-dashed p-8 text-center text-sm text-muted-foreground">
          No requisitions yet. Click <span className="font-medium">New requisition</span> to start.
        </div>
      ) : (
        <ul className="space-y-3">
          {data.requisitions.map((r) => (
            <li
              key={r.id}
              className="rounded border bg-white p-4 transition hover:bg-zinc-50"
            >
              <Link href={`/recruiting/${r.id}`} className="block">
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0 flex-1">
                    <h2 className="truncate font-medium">{r.role_request}</h2>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {new Date(r.created_at).toLocaleString()}
                      {r.published_at && (
                        <>
                          {" · "}published {new Date(r.published_at).toLocaleDateString()}
                        </>
                      )}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    {r.ats_platform && (
                      <Badge variant="outline">{r.ats_platform}</Badge>
                    )}
                    <Badge className={STATUS_COLORS[r.status] ?? ""}>{r.status}</Badge>
                  </div>
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
