"use client";

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import useSWR from "swr";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";

interface Requirement {
  id: string;
  requirement_text: string;
  category: string | null;
}

interface Match {
  requirement_id: string;
  status: "matched" | "gap";
  source_doc?: string | null;
  response_text?: string | null;
  flag_message?: string | null;
  confidence?: number | null;
}

interface Rfp {
  id: string;
  source_filename: string;
  requirements: Requirement[];
  matches: Match[];
  status: "extracted" | "reviewed" | "generating" | "ready" | "failed";
  output_file_path: string | null;
  gap_count: number;
  error_message: string | null;
}

const fetcher = async (url: string): Promise<Rfp> => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed (${res.status})`);
  return res.json();
};

export default function RfpDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params?.id;

  const { data, error, isLoading, mutate } = useSWR<Rfp>(
    id ? `/api/sales/rfp/${id}` : null,
    fetcher,
    {
      revalidateOnFocus: false,
      refreshInterval: (latest) =>
        latest?.status === "generating" ? 3000 : 0,
    },
  );

  const [requirements, setRequirements] = useState<Requirement[]>([]);
  const [saving, setSaving] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  useEffect(() => {
    if (data?.requirements) {
      setRequirements(data.requirements);
    }
  }, [data?.requirements]);

  const handleSaveRequirements = async () => {
    if (!id) return;
    setActionError(null);
    setSaving(true);
    try {
      const res = await fetch(`/api/sales/rfp/${id}/requirements`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ requirements }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.detail || `Failed (${res.status})`);
      }
      await mutate();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  const handleGenerate = async () => {
    if (!id) return;
    setActionError(null);
    setGenerating(true);
    try {
      const res = await fetch(`/api/sales/rfp/${id}/generate`, { method: "POST" });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.detail || `Failed (${res.status})`);
      }
      await mutate();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Generation failed");
    } finally {
      setGenerating(false);
    }
  };

  if (isLoading) {
    return (
      <div className="mx-auto max-w-5xl space-y-3 p-6 md:p-8">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }
  if (error || !data) {
    return (
      <div className="mx-auto max-w-3xl p-6 md:p-8">
        <div className="rounded border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          Failed to load RFP.
        </div>
      </div>
    );
  }

  const matchById = new Map(data.matches?.map((m) => [m.requirement_id, m]) ?? []);
  const isReady = data.status === "ready";
  const isGenerating = data.status === "generating";
  const isLocked = isReady || isGenerating;

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6 md:p-8">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">
            {data.source_filename}
          </h1>
          <div className="mt-2 flex items-center gap-2">
            <Badge>{data.status}</Badge>
            {data.gap_count > 0 && (
              <Badge className="bg-amber-100 text-amber-700">
                {data.gap_count} gaps flagged for legal
              </Badge>
            )}
          </div>
        </div>
        {isReady && (
          <a href={`/api/sales/rfp/${data.id}/download`} download>
            <Button>Download .docx</Button>
          </a>
        )}
      </header>

      {data.error_message && (
        <div className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          {data.error_message}
        </div>
      )}

      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-medium">
            {isLocked ? "Requirements + responses" : "Review extracted requirements"}
          </h2>
          {!isLocked && (
            <Button
              size="sm"
              variant="outline"
              onClick={() =>
                setRequirements([
                  ...requirements,
                  {
                    id: `R${requirements.length + 1}`,
                    requirement_text: "",
                    category: null,
                  },
                ])
              }
            >
              + Add row
            </Button>
          )}
        </div>

        <ul className="space-y-3">
          {requirements.map((req, idx) => {
            const m = matchById.get(req.id);
            return (
              <li key={`${req.id}-${idx}`} className="rounded border bg-white p-4">
                <div className="flex items-start gap-3">
                  <div className="w-16 shrink-0 text-xs font-medium text-muted-foreground">
                    {req.id}
                  </div>
                  {isLocked ? (
                    <div className="flex-1 space-y-2">
                      <div className="text-sm">{req.requirement_text}</div>
                      {req.category && (
                        <div className="text-xs text-muted-foreground">
                          Category: {req.category}
                        </div>
                      )}
                      {m && (
                        <div className="mt-2 rounded bg-zinc-50 p-3 text-sm">
                          {m.status === "matched" ? (
                            <>
                              <p>{m.response_text}</p>
                              {m.source_doc && (
                                <p className="mt-2 text-xs text-muted-foreground">
                                  Source: {m.source_doc}
                                </p>
                              )}
                            </>
                          ) : (
                            <p className="text-amber-800">
                              ⚠ {m.flag_message}
                            </p>
                          )}
                        </div>
                      )}
                    </div>
                  ) : (
                    <div className="flex-1 space-y-2">
                      <Input
                        value={req.requirement_text}
                        onChange={(e) => {
                          const next = [...requirements];
                          next[idx] = { ...req, requirement_text: e.target.value };
                          setRequirements(next);
                        }}
                      />
                      <Input
                        placeholder="Category (e.g. security, integration)"
                        value={req.category ?? ""}
                        onChange={(e) => {
                          const next = [...requirements];
                          next[idx] = { ...req, category: e.target.value || null };
                          setRequirements(next);
                        }}
                      />
                    </div>
                  )}
                  {!isLocked && (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() =>
                        setRequirements(requirements.filter((_, i) => i !== idx))
                      }
                    >
                      Remove
                    </Button>
                  )}
                </div>
              </li>
            );
          })}
        </ul>

        {actionError && (
          <div className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
            {actionError}
          </div>
        )}

        {!isLocked && (
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={handleSaveRequirements} disabled={saving}>
              {saving ? "Saving…" : "Save edits"}
            </Button>
            <Button onClick={handleGenerate} disabled={generating || saving}>
              {generating ? "Generating…" : "Generate response"}
            </Button>
          </div>
        )}

        {isGenerating && (
          <div className="rounded border bg-amber-50 p-3 text-sm text-amber-800">
            Generating response… refreshing every 3s.
          </div>
        )}
      </section>
    </div>
  );
}
