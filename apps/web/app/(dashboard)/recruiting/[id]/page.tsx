"use client";

import { useParams } from "next/navigation";
import { useMemo, useState } from "react";
import useSWR from "swr";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";

interface JdVariant {
  tone: string;
  text: string;
}

interface SourcingTemplate {
  channel: string;
  subject?: string;
  body: string;
  notes?: string;
}

interface Requisition {
  id: string;
  role_request: string;
  jd_variants: JdVariant[];
  selected_variant_index: number | null;
  ats_platform: "greenhouse" | "lever" | "ashby" | null;
  ats_job_id: string | null;
  ats_url: string | null;
  notion_tracker_url: string | null;
  sourcing_templates: SourcingTemplate[];
  linkedin_search_urls: string[];
  hiring_manager_email: string | null;
  slack_channel: string | null;
  status: "draft" | "published" | "failed";
  error_message: string | null;
}

const fetcher = async (url: string): Promise<Requisition> => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed (${res.status})`);
  return res.json();
};

export default function RequisitionDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params?.id;

  const { data, error, isLoading, mutate } = useSWR<Requisition>(
    id ? `/api/recruiting/requisitions/${id}` : null,
    fetcher,
    { revalidateOnFocus: false },
  );

  const [selectedIdx, setSelectedIdx] = useState<number>(0);
  const [ats, setAts] = useState<"greenhouse" | "lever" | "ashby">("greenhouse");
  const [hiringManagerEmail, setHiringManagerEmail] = useState("");
  const [slackChannel, setSlackChannel] = useState("");
  const [notionParentId, setNotionParentId] = useState("");
  const [publishing, setPublishing] = useState(false);
  const [publishError, setPublishError] = useState<string | null>(null);

  const isPublished = data?.status === "published";

  const activeVariant = useMemo<JdVariant | null>(() => {
    if (!data?.jd_variants?.length) return null;
    const idx = isPublished
      ? (data.selected_variant_index ?? 0)
      : selectedIdx;
    return data.jd_variants[idx] ?? null;
  }, [data, selectedIdx, isPublished]);

  const handlePublish = async () => {
    if (!id) return;
    setPublishError(null);
    setPublishing(true);
    try {
      const res = await fetch(`/api/recruiting/requisitions/${id}/publish`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          selected_variant_index: selectedIdx,
          ats_platform: ats,
          hiring_manager_email: hiringManagerEmail || null,
          slack_channel: slackChannel || null,
          notion_parent_page_id: notionParentId || null,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.detail || `Failed (${res.status})`);
      }
      await mutate();
    } catch (err) {
      setPublishError(err instanceof Error ? err.message : "Publish failed");
    } finally {
      setPublishing(false);
    }
  };

  if (isLoading) {
    return (
      <div className="mx-auto max-w-5xl space-y-4 p-6 md:p-8">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-60 w-full" />
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="mx-auto max-w-3xl p-6 md:p-8">
        <div className="rounded border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          Failed to load requisition.
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6 md:p-8">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">{data.role_request}</h1>
        <div className="mt-2 flex items-center gap-2">
          <Badge
            className={
              data.status === "published"
                ? "bg-emerald-100 text-emerald-700"
                : data.status === "failed"
                  ? "bg-red-100 text-red-700"
                  : "bg-zinc-100 text-zinc-700"
            }
          >
            {data.status}
          </Badge>
          {data.ats_platform && (
            <Badge variant="outline">{data.ats_platform}</Badge>
          )}
        </div>
        {data.error_message && (
          <div className="mt-3 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
            {data.error_message}
          </div>
        )}
      </header>

      {/* Variant tabs */}
      <section>
        <h2 className="mb-2 text-sm font-medium text-muted-foreground">
          {isPublished ? "Published JD" : "Pick a variant"}
        </h2>
        <div className="flex flex-wrap gap-2">
          {data.jd_variants.map((v, i) => {
            const idx = isPublished ? (data.selected_variant_index ?? 0) : selectedIdx;
            const isActive = i === idx;
            return (
              <button
                key={i}
                type="button"
                disabled={isPublished}
                onClick={() => setSelectedIdx(i)}
                className={`rounded border px-3 py-1 text-xs font-medium transition ${
                  isActive
                    ? "border-zinc-900 bg-zinc-900 text-white"
                    : "border-zinc-300 bg-white text-zinc-700 hover:border-zinc-400"
                } ${isPublished ? "cursor-not-allowed opacity-60" : ""}`}
              >
                {v.tone}
              </button>
            );
          })}
        </div>

        {activeVariant && (
          <div className="mt-4 rounded border bg-white p-6">
            <pre className="whitespace-pre-wrap font-sans text-sm leading-6">
              {activeVariant.text}
            </pre>
          </div>
        )}
      </section>

      {/* Publish form */}
      {!isPublished && (
        <section className="rounded border bg-white p-6">
          <h2 className="mb-4 text-sm font-medium">Publish</h2>
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="ats">ATS platform</Label>
              <select
                id="ats"
                value={ats}
                onChange={(e) => setAts(e.target.value as typeof ats)}
                className="w-full rounded border border-input bg-background px-3 py-2 text-sm"
              >
                <option value="greenhouse">Greenhouse</option>
                <option value="lever">Lever</option>
                <option value="ashby">Ashby</option>
              </select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="hm">Hiring manager email (optional)</Label>
              <Input
                id="hm"
                type="email"
                value={hiringManagerEmail}
                onChange={(e) => setHiringManagerEmail(e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="slack">Slack channel id (optional)</Label>
              <Input
                id="slack"
                placeholder="C0123456789"
                value={slackChannel}
                onChange={(e) => setSlackChannel(e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="notion">Notion parent page id (optional)</Label>
              <Input
                id="notion"
                placeholder="Tracker created under this page"
                value={notionParentId}
                onChange={(e) => setNotionParentId(e.target.value)}
              />
            </div>
          </div>

          {publishError && (
            <div className="mt-4 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
              {publishError}
            </div>
          )}

          <div className="mt-4 flex justify-end">
            <Button onClick={handlePublish} disabled={publishing}>
              {publishing ? "Publishing…" : "Publish to ATS"}
            </Button>
          </div>
        </section>
      )}

      {/* Published outputs */}
      {isPublished && (
        <section className="space-y-4">
          {data.ats_url && (
            <a
              href={data.ats_url}
              target="_blank"
              rel="noreferrer"
              className="block rounded border bg-white p-4 hover:bg-zinc-50"
            >
              <div className="text-xs text-muted-foreground">ATS posting</div>
              <div className="mt-1 font-medium">{data.ats_url}</div>
            </a>
          )}
          {data.notion_tracker_url && (
            <a
              href={data.notion_tracker_url}
              target="_blank"
              rel="noreferrer"
              className="block rounded border bg-white p-4 hover:bg-zinc-50"
            >
              <div className="text-xs text-muted-foreground">Notion hiring tracker</div>
              <div className="mt-1 font-medium">{data.notion_tracker_url}</div>
            </a>
          )}

          {data.linkedin_search_urls?.length > 0 && (
            <div className="rounded border bg-white p-4">
              <div className="text-xs text-muted-foreground">LinkedIn search shortcuts</div>
              <ul className="mt-2 space-y-1 text-sm">
                {data.linkedin_search_urls.map((u, i) => (
                  <li key={i}>
                    <a
                      href={u}
                      target="_blank"
                      rel="noreferrer"
                      className="text-blue-600 hover:underline"
                    >
                      Search variant {i + 1} ↗
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {data.sourcing_templates?.length > 0 && (
            <div className="rounded border bg-white p-4">
              <div className="text-xs text-muted-foreground">
                Sourcing drafts (copy-paste into LinkedIn)
              </div>
              <ul className="mt-3 space-y-3 text-sm">
                {data.sourcing_templates.map((t, i) => (
                  <li key={i} className="rounded bg-zinc-50 p-3">
                    {t.subject && <div className="text-xs font-medium">{t.subject}</div>}
                    <pre className="mt-1 whitespace-pre-wrap font-sans">{t.body}</pre>
                    {t.notes && (
                      <p className="mt-2 text-xs text-muted-foreground">{t.notes}</p>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>
      )}
    </div>
  );
}
