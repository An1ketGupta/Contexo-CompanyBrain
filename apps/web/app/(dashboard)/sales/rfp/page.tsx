"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import useSWR from "swr";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

interface RfpRow {
  id: string;
  source_filename: string;
  status: string;
  gap_count: number;
  created_at: string;
  generated_at: string | null;
}

const fetcher = async (url: string): Promise<{ rfps: RfpRow[] }> => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed (${res.status})`);
  return res.json();
};

const STATUS_COLORS: Record<string, string> = {
  extracted: "bg-blue-100 text-blue-700",
  reviewed: "bg-amber-100 text-amber-700",
  generating: "bg-amber-100 text-amber-700",
  ready: "bg-emerald-100 text-emerald-700",
  failed: "bg-red-100 text-red-700",
};

export default function RfpListPage() {
  const router = useRouter();
  const { data, error, isLoading, mutate } = useSWR<{ rfps: RfpRow[] }>(
    "/api/sales/rfp",
    fetcher,
    { revalidateOnFocus: false },
  );

  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);

  const handleUpload = async (file: File) => {
    setUploadError(null);
    setUploading(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const res = await fetch("/api/sales/rfp/upload", { method: "POST", body: fd });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.detail || `Failed (${res.status})`);
      }
      const created = await res.json();
      await mutate();
      router.push(`/sales/rfp/${created.id}`);
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6 md:p-8">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">RFP responses</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Upload an RFP (PDF or DOCX). We&apos;ll extract requirements, match each
            to your KB, and assemble a Word doc with gaps flagged for legal.
          </p>
        </div>
        <label className="cursor-pointer">
          <input
            type="file"
            accept=".pdf,.docx,.doc,.txt,.md"
            className="hidden"
            disabled={uploading}
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) handleUpload(f);
            }}
          />
          <Button asChild>
            <span>{uploading ? "Uploading…" : "Upload RFP"}</span>
          </Button>
        </label>
      </header>

      {uploadError && (
        <div className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          {uploadError}
        </div>
      )}

      {isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-16 w-full" />
          ))}
        </div>
      ) : error ? (
        <div className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          Failed to load RFPs.
        </div>
      ) : !data?.rfps?.length ? (
        <div className="rounded border border-dashed p-8 text-center text-sm text-muted-foreground">
          No RFPs uploaded yet.
        </div>
      ) : (
        <ul className="space-y-3">
          {data.rfps.map((r) => (
            <li
              key={r.id}
              className="rounded border bg-white p-4 transition hover:bg-zinc-50"
            >
              <Link href={`/sales/rfp/${r.id}`} className="block">
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0 flex-1">
                    <h2 className="truncate font-medium">{r.source_filename}</h2>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {new Date(r.created_at).toLocaleString()}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    {r.gap_count > 0 && (
                      <Badge className="bg-amber-100 text-amber-700">
                        {r.gap_count} gaps
                      </Badge>
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
