"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import useSWR from "swr";
import { toast } from "sonner";
import {
  Archive,
  ArchiveRestore,
  Banknote,
  Briefcase,
  Building2,
  Check,
  Clock,
  Copy,
  ExternalLink,
  FileText,
  GraduationCap,
  Loader2,
  MapPin,
  Pencil,
  RefreshCw,
  Search,
  Trash2,
  Users,
} from "lucide-react";

import { Markdown } from "@/components/chat/markdown";
import { cn } from "@/lib/utils";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  StatusPill as KitStatusPill,
  type PillTone,
} from "@/components/actual/kit";
import { NotionParentPicker } from "@/components/recruiting/notion-parent-picker";
import { SlackChannelPicker } from "@/components/recruiting/slack-channel-picker";

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

interface LinkedinSearch {
  label: string;
  url: string;
  description: string;
}

type AtsPlatform = "greenhouse" | "lever" | "ashby";

interface AtsPosting {
  platform: AtsPlatform;
  destination_type?: "ats" | null;
  job_id: string | null;
  url: string | null;
  error: string | null;
}

interface Requisition {
  id: string;
  role_request: string;
  context_notes: string | null;
  location: string | null;
  department: string | null;
  interview_details: string | null;
  seniority_level: string | null;
  disclosed_compensation: string | null;
  stack: string | null;
  working_hours: string | null;
  grounded: boolean;
  jd_variants: JdVariant[];
  selected_variant_index: number | null;
  ats_platform: AtsPlatform | null;
  ats_job_id: string | null;
  ats_url: string | null;
  ats_postings: AtsPosting[];
  notion_tracker_url: string | null;
  notion_candidates_db_id: string | null;
  candidates_last_synced_at: string | null;
  candidates_last_sync_error: string | null;
  hiring_completed_at: string | null;
  archived_at: string | null;
  sourcing_templates: SourcingTemplate[];
  linkedin_search_urls: LinkedinSearch[];
  hiring_manager_email: string | null;
  slack_channel: string | null;
  slack_post_error: string | null;
  status: "draft" | "published" | "failed";
  error_message: string | null;
}

const POSTING_DESTINATIONS: {
  value: AtsPlatform;
  label: string;
}[] = [
  { value: "greenhouse", label: "Greenhouse" },
  { value: "lever", label: "Lever" },
  { value: "ashby", label: "Ashby" },
];

// Back-compat alias — historical callers in this file used ATS_PLATFORMS.
// Keep the old name so we don't sprawl rename diff noise into unrelated UI.
const ATS_PLATFORMS = POSTING_DESTINATIONS;

interface AtsDept {
  id: string;
  name: string;
}

const fetcher = async <T,>(url: string): Promise<T> => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed (${res.status})`);
  return res.json();
};

export default function RequisitionDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const id = params?.id;

  const { data, error, isLoading, mutate } = useSWR<Requisition>(
    id ? `/api/recruiting/requisitions/${id}` : null,
    fetcher,
    { revalidateOnFocus: false },
  );

  // Org-level default Notion parent — used as the implicit parent unless the
  // user explicitly overrides it for this requisition via the picker.
  const { data: notionStatus, mutate: mutateNotionStatus } = useSWR<{
    connected: boolean;
    parent_id: string | null;
    parent_title: string | null;
    accessible: boolean;
    accessibility_error: string | null;
  }>("/api/recruiting/notion-parent", fetcher, { revalidateOnFocus: false });

  const { data: slackStatus, mutate: mutateSlackStatus } = useSWR<{
    connected: boolean;
    channel_id: string | null;
    channel_name: string | null;
    accessible: boolean;
    accessibility_error: string | null;
  }>("/api/recruiting/slack-channel", fetcher, { revalidateOnFocus: false });

  const [selectedIdx, setSelectedIdx] = useState<number>(0);
  const [selectedAts, setSelectedAts] = useState<Set<AtsPlatform>>(
    () => new Set<AtsPlatform>(["greenhouse"]),
  );
  const [hiringManagerEmail, setHiringManagerEmail] = useState("");
  // null = "use the org default"; non-null = per-requisition override.
  const [notionParentOverride, setNotionParentOverride] = useState<
    { id: string; title: string } | null
  >(null);
  const [notionPickerOpen, setNotionPickerOpen] = useState(false);
  // Same shape for Slack: null falls back to the org default channel.
  const [slackChannelOverride, setSlackChannelOverride] = useState<
    { id: string; name: string } | null
  >(null);
  const [slackPickerOpen, setSlackPickerOpen] = useState(false);
  const [locationOverride, setLocationOverride] = useState("");
  const [departmentOverride, setDepartmentOverride] = useState("");
  const [deptSelections, setDeptSelections] = useState<Record<string, AtsDept>>({});
  const [publishing, setPublishing] = useState(false);
  const [publishError, setPublishError] = useState<string | null>(null);
  // Inline JD editing — the Edit button swaps the rendered variant for a
  // markdown textarea instead of re-prompting the agent.
  const [editingJd, setEditingJd] = useState(false);
  const [jdDraft, setJdDraft] = useState("");
  const [savingJd, setSavingJd] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [markingHired, setMarkingHired] = useState(false);
  const [syncSummary, setSyncSummary] = useState<{
    total: number;
    new: number;
    updated: number;
    per_platform: Record<string, { count: number; error: string | null }>;
    errors: string[];
  } | null>(null);

  const toggleAts = (p: AtsPlatform) => {
    setSelectedAts((prev) => {
      const next = new Set(prev);
      if (next.has(p)) {
        next.delete(p);
      } else {
        next.add(p);
      }
      // Always keep at least one selected — disabling submit is enough,
      // but a totally empty checkbox group is bad UX.
      return next.size === 0 ? prev : next;
    });
    setDeptSelections((prev) => { const next = { ...prev }; delete next[p]; return next; });
  };

  // Seed location/department overrides from the persisted row so the user
  // can correct what was generated without retyping from scratch.
  useEffect(() => {
    if (!data) return;
    if (data.location && !locationOverride) setLocationOverride(data.location);
    if (data.department && !departmentOverride)
      setDepartmentOverride(data.department);
    if (data.hiring_manager_email && !hiringManagerEmail)
      setHiringManagerEmail(data.hiring_manager_email);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data?.id]);

  const isPublished = data?.status === "published";

  const activeVariant = useMemo<JdVariant | null>(() => {
    if (!data?.jd_variants?.length) return null;
    const idx = isPublished
      ? (data.selected_variant_index ?? 0)
      : selectedIdx;
    return data.jd_variants[idx] ?? null;
  }, [data, selectedIdx, isPublished]);

  const selectedAtsList = useMemo(
    () => ATS_PLATFORMS.filter((p) => selectedAts.has(p.value)).map((p) => p.value),
    [selectedAts],
  );
  // Fetch each platform's departments independently so toggling one platform
  // never invalidates the cached data for the others (which caused the scroll
  // jump — the whole dept section would disappear then reappear taller).
  const { data: greenhouseDepts } = useSWR<AtsDept[]>(
    selectedAts.has("greenhouse") ? "/api/integrations/ats/greenhouse/departments" : null,
    fetcher,
    { revalidateOnFocus: false },
  );
  const { data: leverDepts } = useSWR<AtsDept[]>(
    selectedAts.has("lever") ? "/api/integrations/ats/lever/departments" : null,
    fetcher,
    { revalidateOnFocus: false },
  );
  const { data: ashbyDepts } = useSWR<AtsDept[]>(
    selectedAts.has("ashby") ? "/api/integrations/ats/ashby/departments" : null,
    fetcher,
    { revalidateOnFocus: false },
  );
  // Connection status for every posting destination — surfaces whether the
  // recruiter has finished the Settings → Integrations connect step. Fetched
  // unconditionally (not gated on selection) so we can warn on a destination
  // the moment it's checked, before the recruiter ever hits Publish.
  // After all three taxonomy SWRs settle, check whether any returned data.
  // We treat "all loaded AND all empty" as the empty-state signal — partial
  // emptiness (e.g. industries [] but functional_areas non-empty) is still
  // a misconfiguration worth showing the recruiter.
  const deptsByPlatform: Record<AtsPlatform, AtsDept[]> = {
    greenhouse: greenhouseDepts ?? [],
    lever: leverDepts ?? [],
    ashby: ashbyDepts ?? [],
  };

  const handlePublish = async () => {
    if (!id) return;
    if (selectedAtsList.length === 0) {
      setPublishError("Select at least one destination.");
      return;
    }
    setPublishError(null);
    setPublishing(true);
    try {
      // Idempotency-Key per attempt. Browser-generated so a flaky network
      // retry hits the cached response instead of double-creating the ATS job.
      const idempotencyKey =
        typeof crypto !== "undefined" && "randomUUID" in crypto
          ? crypto.randomUUID()
          : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
      const overridesPayload: Record<string, Record<string, unknown>> = {};
      for (const p of selectedAtsList) {
        const dept = deptSelections[p];
        if (dept) overridesPayload[p] = buildDeptOverride(p, dept);
      }
      const res = await fetch(`/api/recruiting/requisitions/${id}/publish`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": idempotencyKey,
        },
        body: JSON.stringify({
          selected_variant_index: selectedIdx,
          ats_platforms: selectedAtsList,
          hiring_manager_email: hiringManagerEmail || null,
          slack_channel: slackChannelOverride?.id ?? null,
          // When the user picks an override, send it; otherwise leave null
          // and the backend falls back to the org default Notion parent.
          notion_parent_page_id: notionParentOverride?.id ?? null,
          location_override: locationOverride || null,
          department_override: departmentOverride || null,
          mapping_overrides:
            Object.keys(overridesPayload).length > 0 ? overridesPayload : null,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.message || body?.detail || `Failed (${res.status})`);
      }
      toast.success(
        selectedAtsList.length > 1
          ? `Published to ${selectedAtsList.length} ATS platforms`
          : "Published to ATS",
      );
      await mutate();
    } catch (err) {
      setPublishError(err instanceof Error ? err.message : "Publish failed");
    } finally {
      setPublishing(false);
    }
  };

  const handleSyncCandidates = async () => {
    if (!id) return;
    setSyncing(true);
    setSyncSummary(null);
    try {
      const res = await fetch(
        `/api/recruiting/requisitions/${id}/sync-candidates`,
        { method: "POST" },
      );
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(
          body?.detail === "notion_candidates_db_missing"
            ? "No Notion candidate database. Re-publish this requisition to set one up."
            : body?.message || body?.detail || `Sync failed (${res.status})`,
        );
      }
      setSyncSummary({
        total: body.total_candidates ?? 0,
        new: body.new_candidates ?? 0,
        updated: body.updated_candidates ?? 0,
        per_platform: body.per_platform ?? {},
        errors: body.errors ?? [],
      });
      if ((body.total_candidates ?? 0) === 0) {
        toast.info("No candidates yet — check back once people apply.");
      } else {
        toast.success(
          `Synced ${body.total_candidates} candidate${body.total_candidates === 1 ? "" : "s"} (${body.new_candidates} new)`,
        );
      }
      await mutate();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Sync failed");
    } finally {
      setSyncing(false);
    }
  };

  const handleMarkHiringCompleted = async () => {
    if (!id) return;
    setMarkingHired(true);
    try {
      const res = await fetch(`/api/recruiting/requisitions/${id}/mark-hired`, {
        method: "POST",
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(
          body?.message || body?.detail || `Failed (${res.status})`,
        );
      }
      await mutate();
      router.push("/onboarding");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to mark hiring completed");
      setMarkingHired(false);
    }
  };

  const handleSaveJd = async () => {
    if (!id) return;
    setSavingJd(true);
    try {
      const res = await fetch(`/api/recruiting/requisitions/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          jd_variant_edit: { variant_index: selectedIdx, text: jdDraft },
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(
          body?.message || body?.detail || `Save failed (${res.status})`,
        );
      }
      toast.success("JD updated");
      await mutate();
      setEditingJd(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSavingJd(false);
    }
  };

  const handleDelete = async () => {
    if (!id) return;
    if (!confirm("Delete this requisition? This cannot be undone.")) return;
    setDeleting(true);
    try {
      const res = await fetch(`/api/recruiting/requisitions/${id}`, {
        method: "DELETE",
      });
      if (!res.ok && res.status !== 204) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.message || body?.detail || `Failed (${res.status})`);
      }
      toast.success("Requisition deleted");
      router.push("/recruiting");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Delete failed");
    } finally {
      setDeleting(false);
    }
  };

  const handleArchiveToggle = async () => {
    if (!id || !data) return;
    const archived = Boolean(data.archived_at);
    setArchiving(true);
    try {
      const res = await fetch(
        `/api/recruiting/requisitions/${id}/${archived ? "unarchive" : "archive"}`,
        { method: "POST" },
      );
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.message || body?.detail || `Failed (${res.status})`);
      }
      toast.success(archived ? "Requisition restored" : "Requisition archived");
      await mutate();
    } catch (err) {
      toast.error(
        err instanceof Error
          ? err.message
          : archived
            ? "Restore failed"
            : "Archive failed",
      );
    } finally {
      setArchiving(false);
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
        <div className="rounded-2xl border border-destructive/30 bg-destructive-soft p-4 text-sm font-medium text-destructive">
          Failed to load requisition.
        </div>
      </div>
    );
  }

  const isArchived = Boolean(data.archived_at);
  // An archived requisition is retired: no edits, no publish. Restore first.
  const canEdit = (data.status === "draft" || data.status === "failed") && !isArchived;

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6 md:p-8">
      <header className="space-y-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1 space-y-2">
            <div className="flex items-center gap-3">
              <StatusPill status={data.status} />
              <div className="flex flex-wrap gap-1.5">
                {(data.ats_postings.length > 0
                  ? data.ats_postings.map((p) => p.platform)
                  : data.ats_platform
                    ? [data.ats_platform]
                    : []
                ).map((p) => (
                  <PlatformChip key={p} platform={p} />
                ))}
              </div>
            </div>
            <h1 className="text-3xl font-extrabold leading-tight tracking-tight">
              {data.role_request}
            </h1>
          </div>
          <div className="flex shrink-0 gap-1">
            {canEdit && (
              <Button
                size="sm"
                variant="outline"
                disabled={!activeVariant}
                onClick={() => {
                  if (editingJd) {
                    setEditingJd(false);
                    return;
                  }
                  setJdDraft(activeVariant?.text ?? "");
                  setEditingJd(true);
                }}
              >
                <Pencil className="h-3.5 w-3.5" />
                {editingJd ? "Cancel" : "Edit"}
              </Button>
            )}
            {/* Archive works for any status — it's the only retire path a
                published requisition has, since delete refuses those. */}
            <Button
              size="sm"
              variant={isArchived ? "outline" : "ghost"}
              onClick={handleArchiveToggle}
              disabled={archiving}
              className={isArchived ? undefined : "text-muted-foreground"}
            >
              {archiving ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : isArchived ? (
                <ArchiveRestore className="h-3.5 w-3.5" />
              ) : (
                <Archive className="h-3.5 w-3.5" />
              )}
              {isArchived ? "Restore" : "Archive"}
            </Button>
            {canEdit && (
              <Button
                size="sm"
                variant="ghost"
                onClick={handleDelete}
                disabled={deleting}
                className="text-muted-foreground hover:text-destructive"
              >
                {deleting ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Trash2 className="h-3.5 w-3.5" />
                )}
              </Button>
            )}
          </div>
        </div>

        {(data.location ||
          data.department ||
          data.seniority_level ||
          data.disclosed_compensation ||
          data.working_hours ||
          data.stack) && (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-muted-foreground">
            {data.location && (
              <MetaItem icon={MapPin}>{data.location}</MetaItem>
            )}
            {data.department && (
              <MetaItem icon={Building2}>{data.department}</MetaItem>
            )}
            {data.seniority_level && (
              <MetaItem icon={GraduationCap}>
                <span className="capitalize">{data.seniority_level}</span>
              </MetaItem>
            )}
            {data.disclosed_compensation && (
              <MetaItem icon={Banknote}>{data.disclosed_compensation}</MetaItem>
            )}
            {data.working_hours && (
              <MetaItem icon={Clock}>{data.working_hours}</MetaItem>
            )}
            {data.stack && (
              <MetaItem icon={Briefcase}>{data.stack}</MetaItem>
            )}
          </div>
        )}

        {isArchived && (
          <div className="mt-3 flex items-start gap-2 rounded-xl border border-border bg-muted/50 p-3 text-sm text-muted-foreground">
            <Archive className="mt-0.5 h-4 w-4 shrink-0" />
            <div>
              <div className="font-bold text-foreground">Archived</div>
              <p className="mt-1">
                Hidden from the requisitions list since{" "}
                {new Date(data.archived_at!).toLocaleString()}. Nothing was
                deleted — any live ATS postings are untouched. Restore it to
                edit or publish again.
              </p>
            </div>
          </div>
        )}
        {data.error_message && (
          <div className="mt-3 rounded-xl border border-destructive/30 bg-destructive-soft p-3 text-sm font-medium text-destructive">
            {data.error_message}
          </div>
        )}
        {data.grounded === false && (
          <div className="mt-3 rounded-xl border border-amber/30 bg-amber-tint p-3 text-sm text-black">
            <div className="font-bold">No matching documents found for this role</div>
            <p className="mt-1">
              The JD variants
              below are model-generated and may contain plausible-sounding. Review
              carefully before publishing.
            </p>
          </div>
        )}
      </header>

      <section>
        <div className="mb-3 flex items-center justify-between gap-3">
          <h2 className="text-sm font-medium text-muted-foreground">
            {isPublished ? "Published JD" : "Pick a variant"}
          </h2>
          <div className="inline-flex rounded-md border border-border bg-muted/40 p-0.5">
            {data.jd_variants.map((v, i) => {
              const idx = isPublished ? (data.selected_variant_index ?? 0) : selectedIdx;
              const isActive = i === idx;
              return (
                <button
                  key={i}
                  type="button"
                  disabled={isPublished || editingJd}
                  onClick={() => setSelectedIdx(i)}
                  className={`rounded px-3 py-1 text-xs font-medium transition ${
                    isActive
                      ? "bg-background text-foreground shadow-sm"
                      : "text-muted-foreground hover:text-foreground"
                  } ${isPublished ? "cursor-not-allowed" : ""}`}
                >
                  {v.tone}
                </button>
              );
            })}
          </div>
        </div>

        {activeVariant && (
          <div className="rounded-2xl border border-border bg-card p-6 sm:p-8">
            {editingJd && canEdit ? (
              <div className="mx-auto max-w-3xl space-y-3">
                <Textarea
                  value={jdDraft}
                  onChange={(e) => setJdDraft(e.target.value)}
                  rows={24}
                  className="font-mono text-sm leading-relaxed"
                />
                <p className="text-xs text-muted-foreground">
                  Markdown supported. This edits only the &ldquo;
                  {activeVariant.tone}&rdquo; variant — the other variants stay
                  as generated.
                </p>
                <div className="flex justify-end gap-2">
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => setEditingJd(false)}
                    disabled={savingJd}
                  >
                    Cancel
                  </Button>
                  <Button
                    size="sm"
                    onClick={handleSaveJd}
                    disabled={savingJd || !jdDraft.trim()}
                  >
                    {savingJd ? (
                      <>
                        <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />{" "}
                        Saving…
                      </>
                    ) : (
                      "Save JD"
                    )}
                  </Button>
                </div>
              </div>
            ) : (
              <div className="prose prose-sm dark:prose-invert mx-auto max-w-3xl">
                <Markdown>{activeVariant.text}</Markdown>
              </div>
            )}
          </div>
        )}
      </section>

      {/* Publish form — hidden while archived; the API rejects publishing an
          archived requisition, so don't render a form that can only 409. */}
      {!isPublished && !isArchived && (
        <section className="rounded-2xl border border-border bg-card p-6">
          <h2 className="mb-4 text-base font-extrabold tracking-tight">Publish</h2>
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-3 md:col-span-2">
              <div className="space-y-1">
                <Label>Posting destinations</Label>
              </div>
              <div className="space-y-2">
                <div className="font-mono text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/70">
                  ATS platforms
                </div>
                <div className="flex flex-wrap gap-2">
                      {POSTING_DESTINATIONS.map((p) => {
                        const active = selectedAts.has(p.value);
                        return (
                          <label
                            key={p.value}
                            className={cn(
                              // relative anchors the sr-only (absolute) checkbox inside
                              // the pill — otherwise it lands past the app root's height
                              // and focusing it scrolls the overflow-hidden layout.
                              "relative flex cursor-pointer items-center gap-2 rounded-full border px-3 py-2 text-sm font-medium transition-colors",
                              active
                                ? "border-accent bg-accent text-accent-foreground"
                                : "border-input bg-background text-foreground hover:border-foreground/40 hover:bg-muted/40",
                            )}
                          >
                            <input
                              type="checkbox"
                              className="sr-only"
                              checked={active}
                              onChange={() => toggleAts(p.value)}
                            />
                            <PlatformIcon platform={p.value} />
                            {p.label}
                            {active && (
                              <Check className="h-3.5 w-3.5 text-brand" aria-hidden />
                            )}
                          </label>
                        );
                      })}
                </div>
              </div>
            </div>
            <div className="space-y-2">
              <Label htmlFor="location-pub">Location</Label>
              <Input
                id="location-pub"
                value={locationOverride}
                onChange={(e) => {
                  setLocationOverride(e.target.value);
                  setDeptSelections({});
                }}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="dept-pub">Department</Label>
              <Input
                id="dept-pub"
                value={departmentOverride}
                onChange={(e) => {
                  setDepartmentOverride(e.target.value);
                  setDeptSelections({});
                }}
              />
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
            <div className="space-y-2 md:col-span-2">
              <Label>Slack announcement channel</Label>
              <SlackChannelField
                effective={
                  slackChannelOverride ??
                  (slackStatus?.channel_id && slackStatus.accessible
                    ? {
                        id: slackStatus.channel_id,
                        name: slackStatus.channel_name || "channel",
                      }
                    : null)
                }
                overridden={slackChannelOverride !== null}
                accessibilityError={
                  !slackChannelOverride && slackStatus?.channel_id
                    ? slackStatus.accessibility_error
                    : null
                }
                slackConnected={slackStatus?.connected ?? false}
                onChange={() => setSlackPickerOpen(true)}
                onClearOverride={() => setSlackChannelOverride(null)}
              />
            </div>
            <div className="space-y-2 md:col-span-2">
              <Label>Notion hiring tracker</Label>
              <NotionParentField
                effective={
                  notionParentOverride ??
                  (notionStatus?.parent_id && notionStatus.accessible
                    ? {
                        id: notionStatus.parent_id,
                        title: notionStatus.parent_title || "Notion parent",
                      }
                    : null)
                }
                overridden={notionParentOverride !== null}
                accessibilityError={
                  !notionParentOverride && notionStatus?.parent_id
                    ? notionStatus.accessibility_error
                    : null
                }
                notionConnected={notionStatus?.connected ?? false}
                onChange={() => setNotionPickerOpen(true)}
                onClearOverride={() => setNotionParentOverride(null)}
              />
            </div>
          </div>

          {/* Department selection — one dropdown per selected ATS. */}
          {selectedAtsList.some((p) => deptsByPlatform[p].length > 0) && (
            <div className="mt-4 space-y-3">
              {selectedAtsList.map((p) => {
                const depts = deptsByPlatform[p];
                if (!depts.length) return null;
                return (
                  <div key={p} className="space-y-1">
                    <Label className="text-xs text-muted-foreground capitalize">
                      {p} — Department
                    </Label>
                    <select
                      className="w-full rounded-lg border border-input bg-background px-3 py-2 text-sm"
                      value={deptSelections[p]?.id ?? ""}
                      onChange={(e) => {
                        const dept = depts.find((d) => d.id === e.target.value);
                        if (dept) {
                          setDeptSelections((prev) => ({ ...prev, [p]: dept }));
                        } else {
                          setDeptSelections((prev) => {
                            const next = { ...prev };
                            delete next[p];
                            return next;
                          });
                        }
                      }}
                    >
                      <option value="">
                        Select department (optional)
                      </option>
                      {depts.map((d) => (
                        <option key={d.id} value={d.id}>{d.name}</option>
                      ))}
                    </select>
                  </div>
                );
              })}
            </div>
          )}

          {publishError && (
            <div className="mt-4 rounded-xl border border-destructive/30 bg-destructive-soft p-3 text-sm font-medium text-destructive">
              {publishError}
            </div>
          )}

          <div className="mt-4 flex justify-end">
            <Button
              onClick={handlePublish}
              disabled={publishing || selectedAtsList.length === 0}
            >
              {publishing
                ? "Publishing…"
                : `Publish to ${selectedAtsList.length || 0} destination${selectedAtsList.length === 1 ? "" : "s"}`}
            </Button>
          </div>

          <NotionParentPicker
            open={notionPickerOpen}
            onOpenChange={setNotionPickerOpen}
            // "publish" scope returns the choice as a per-requisition override
            // rather than mutating the org-level default. Recruiter intent:
            // "use a different parent for this one role."
            scope="publish"
            onPicked={(p) => {
              setNotionParentOverride({ id: p.id, title: p.title });
              // Refresh status in case the picker also caused a re-share.
              mutateNotionStatus();
            }}
          />

          <SlackChannelPicker
            open={slackPickerOpen}
            onOpenChange={setSlackPickerOpen}
            scope="publish"
            onPicked={(c) => {
              setSlackChannelOverride({ id: c.id, name: c.name });
              mutateSlackStatus();
            }}
          />
        </section>
      )}

      {isPublished && (
        <>
          <section className="space-y-3">
            <SectionHeading icon={ExternalLink}>Live postings</SectionHeading>
            <div className="grid gap-3 sm:grid-cols-2">
              {(data.ats_postings.length > 0
                ? data.ats_postings
                : data.ats_url
                  ? [
                      {
                        platform: data.ats_platform as AtsPlatform,
                        job_id: data.ats_job_id,
                        url: data.ats_url,
                        error: null,
                      },
                    ]
                  : []
              ).map((p) =>
                p.url ? (
                  <PostingCard
                    key={p.platform}
                    platform={p.platform}
                    label={`${platformLabel(p.platform)} posting`}
                    url={p.url}
                  />
                ) : (
                  <div
                    key={p.platform}
                    className="rounded-2xl border border-destructive/30 bg-destructive-soft p-4"
                  >
                    <div className="flex items-center gap-2">
                      <PlatformIcon platform={p.platform} />
                      <span className="text-xs font-bold text-destructive">
                        {platformLabel(p.platform)} — publish failed
                      </span>
                    </div>
                    <div className="mt-2 break-all text-sm text-destructive">
                      {p.error}
                    </div>
                  </div>
                ),
              )}
              {data.notion_tracker_url && (
                <PostingCard
                  platform="notion"
                  label="Notion hiring tracker"
                  url={data.notion_tracker_url}
                />
              )}
            </div>
          </section>

          <section className="space-y-3">
            <SectionHeading icon={Users}>Candidate sync</SectionHeading>
            <div className="rounded-2xl border border-border bg-card p-5">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <p className="text-sm text-foreground">
                    {data.candidates_last_synced_at
                      ? `Last synced ${new Date(data.candidates_last_synced_at).toLocaleString()}`
                      : "Pull candidates from every connected ATS into the Notion tracker."}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={handleMarkHiringCompleted}
                    disabled={markingHired || Boolean(data.hiring_completed_at)}
                    title={
                      data.hiring_completed_at
                        ? `Hiring marked completed ${new Date(data.hiring_completed_at).toLocaleString()}`
                        : "Mark hiring as completed and open onboarding"
                    }
                  >
                    {markingHired ? (
                      <Loader2 className="mr-2 h-3 w-3 animate-spin" />
                    ) : data.hiring_completed_at ? (
                      <Check className="mr-2 h-3 w-3" />
                    ) : null}
                    Hiring completed
                  </Button>
                  <Button
                    size="sm"
                    onClick={handleSyncCandidates}
                    disabled={syncing || !data.notion_candidates_db_id}
                    title={
                      data.notion_candidates_db_id
                        ? "Pull the latest candidates from every connected ATS"
                        : "Re-publish this requisition to set up the candidate tracker"
                    }
                  >
                    {syncing ? (
                      <>
                        <Loader2 className="mr-2 h-3 w-3 animate-spin" />
                        Syncing…
                      </>
                    ) : (
                      <>
                        <RefreshCw className="mr-2 h-3 w-3" />
                        Sync candidates
                      </>
                    )}
                  </Button>
                </div>
              </div>

              {!data.notion_candidates_db_id && (
                <div className="mt-3 rounded-xl border border-amber/30 bg-amber-tint px-3 py-2 text-xs text-amber">
                  This requisition was Published before candidate sync was
                  available. Republish to add a Notion candidate database.
                </div>
              )}

              {syncSummary && (
                <div className="mt-4 space-y-3">
                  <div className="grid grid-cols-3 gap-3">
                    <StatTile label="Total" value={syncSummary.total} />
                    <StatTile label="New" value={syncSummary.new} accent="emerald" />
                    <StatTile label="Updated" value={syncSummary.updated} accent="blue" />
                  </div>
                  {Object.keys(syncSummary.per_platform).length > 0 && (
                    <div className="space-y-1 border-t border-border/60 pt-3 text-xs">
                      {Object.entries(syncSummary.per_platform).map(
                        ([platform, info]) => (
                          <div
                            key={platform}
                            className="flex items-center justify-between"
                          >
                            <span className="flex items-center gap-1.5 capitalize text-muted-foreground">
                              <PlatformIcon platform={platform as AtsPlatform} />
                              {platform}
                            </span>
                            {info.error ? (
                              <span className="font-medium text-destructive">
                                {info.error}
                              </span>
                            ) : (
                              <span className="font-medium text-foreground">
                                {info.count} candidate
                                {info.count === 1 ? "" : "s"}
                              </span>
                            )}
                          </div>
                        ),
                      )}
                    </div>
                  )}
                </div>
              )}

              {data.candidates_last_sync_error && !syncSummary && (
                <div className="mt-3 break-words rounded-xl border border-destructive/30 bg-destructive-soft px-3 py-2 text-xs font-medium text-destructive">
                  Last sync had errors: {data.candidates_last_sync_error}
                </div>
              )}
            </div>
          </section>

          {data.slack_post_error && (
            <div className="rounded-2xl border border-amber/30 bg-amber-tint p-4">
              <div className="text-xs font-bold text-amber">
                Slack announcement didn&apos;t post
              </div>
              <div className="mt-1 text-sm text-amber">
                {data.slack_post_error === "slack_not_in_channel"
                  ? "The bot isn't a member of the channel. Invite it (/invite @NirnayaIQ) and republish from a new requisition."
                  : data.slack_post_error}
              </div>
              {data.slack_channel && (
                <div className="mt-2 text-xs text-amber/90">
                  Channel id: <code>{data.slack_channel}</code>
                </div>
              )}
            </div>
          )}

          {data.linkedin_search_urls?.length > 0 && (
            <section className="space-y-3">
              <div className="flex items-end justify-between gap-3">
                <SectionHeading icon={Search}>
                  LinkedIn search shortcuts
                </SectionHeading>
                <span className="rounded-full border border-brand/15 bg-brand-tint px-2.5 py-1 text-[10px] font-bold uppercase tracking-[0.18em] text-brand">
                  {data.linkedin_search_urls.length} curated links
                </span>
              </div>
              <div className="rounded-3xl border border-border/70 bg-gradient-to-br from-card via-card to-muted/25 p-4 shadow-sm">
                <div className="mb-3 flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
                  <span className="rounded-full border border-border bg-background/80 px-2 py-0.5 font-medium text-foreground">
                    Prebuilt queries
                  </span>
                  <span>
                    Open the exact search in LinkedIn, then copy the URL when
                    you need to reuse it.
                  </span>
                </div>
                <TooltipProvider delayDuration={150}>
                  <ul className="grid gap-3 text-sm">
                    {data.linkedin_search_urls.map((s, i) => (
                      <LinkedinSearchRow key={i} search={s} />
                    ))}
                  </ul>
                </TooltipProvider>
              </div>
            </section>
          )}

          {data.sourcing_templates?.length > 0 && (
            <section className="space-y-3">
              <SectionHeading icon={FileText}>
                Sourcing drafts <span className="text-muted-foreground/70">· copy into LinkedIn</span>
              </SectionHeading>
              <ul className="space-y-3 text-sm">
                {data.sourcing_templates.map((t, i) => (
                  <li
                    key={i}
                    className="rounded-2xl border border-border bg-card p-4 text-foreground"
                  >
                    {t.subject && (
                      <div className="text-xs font-medium text-foreground">
                        {t.subject}
                      </div>
                    )}
                    <pre className="mt-1 whitespace-pre-wrap font-sans text-foreground">
                      {t.body}
                    </pre>
                    {t.notes && (
                      <p className="mt-2 text-xs text-muted-foreground">{t.notes}</p>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          )}
        </>
      )}
    </div>
  );
}

function LinkedinSearchRow({ search }: { search: LinkedinSearch }) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(search.url);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error("Couldn't copy URL");
    }
  };

  return (
    <li className="group rounded-2xl border border-border/70 bg-background/80 p-4 transition-all duration-200 hover:-translate-y-0.5 hover:border-brand/25 hover:bg-accent/20 hover:shadow-sm">
      <div className="flex items-start gap-3">
        <div className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-brand/10 bg-brand-tint text-brand">
          <ExternalLink className="h-4 w-4" />
        </div>
        <div className="min-w-0 flex-1">
          <a
            href={search.url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1.5 text-sm font-semibold text-foreground transition hover:text-brand hover:underline"
          >
            {search.label}
            <ExternalLink className="h-3.5 w-3.5 text-muted-foreground transition group-hover:text-brand" />
          </a>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">
            {search.description}
          </p>
          <p className="mt-2 truncate rounded-lg border border-border bg-muted/35 px-2.5 py-1 font-mono text-[10px] text-muted-foreground">
            {search.url}
          </p>
        </div>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="h-8 w-8 shrink-0 rounded-xl border border-border/70 bg-background/80 opacity-70 transition hover:border-brand/20 hover:bg-background group-hover:opacity-100 focus-visible:opacity-100"
              onClick={copy}
              aria-label="Copy LinkedIn search URL"
            >
              {copied ? (
                <Check className="h-3.5 w-3.5 text-success" />
              ) : (
                <Copy className="h-3.5 w-3.5" />
              )}
            </Button>
          </TooltipTrigger>
          <TooltipContent>{copied ? "Copied" : "Copy URL"}</TooltipContent>
        </Tooltip>
      </div>
    </li>
  );
}

type PlatformKey = AtsPlatform | "notion";

const PLATFORM_STYLE: Record<
  PlatformKey,
  { label: string; bg: string; text: string }
> = {
  greenhouse: { label: "Greenhouse", bg: "bg-success-tint", text: "text-success" },
  lever: { label: "Lever", bg: "bg-violet-tint", text: "text-violet" },
  ashby: { label: "Ashby", bg: "bg-amber-tint", text: "text-amber" },
  notion: { label: "Notion", bg: "bg-muted", text: "text-muted-foreground" },
};

function platformLabel(p: PlatformKey): string {
  return PLATFORM_STYLE[p]?.label ?? p;
}

function PlatformIcon({ platform }: { platform: PlatformKey }) {
  const style = PLATFORM_STYLE[platform];
  return (
    <span
      className={`flex h-5 w-5 items-center justify-center rounded text-[10px] font-semibold ${style.bg} ${style.text}`}
      aria-hidden
    >
      {style.label.charAt(0)}
    </span>
  );
}

function PlatformChip({ platform }: { platform: AtsPlatform }) {
  const style = PLATFORM_STYLE[platform];
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ${style.bg} ${style.text}`}
    >
      <PlatformIcon platform={platform} />
      {style.label}
    </span>
  );
}

function StatusPill({ status }: { status: "draft" | "published" | "failed" }) {
  const map = {
    published: { label: "Published", tone: "green" as PillTone },
    failed: { label: "Failed", tone: "red" as PillTone },
    draft: { label: "Draft", tone: "gray" as PillTone },
  } as const;
  // Tolerate any unexpected status (e.g. legacy 'Published' rows from before
  // migration 072) by folding case and falling back to the draft style.
  const key = (typeof status === "string"
    ? status.toLowerCase()
    : "draft") as keyof typeof map;
  const s = map[key] ?? map.draft;
  return <KitStatusPill tone={s.tone}>{s.label}</KitStatusPill>;
}

function MetaItem({
  icon: Icon,
  children,
}: {
  icon: typeof MapPin;
  children: React.ReactNode;
}) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <Icon className="h-3.5 w-3.5 shrink-0" />
      <span className="text-foreground">{children}</span>
    </span>
  );
}

function SectionHeading({
  icon: Icon,
  children,
}: {
  icon: typeof MapPin;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-center gap-2 font-mono text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
      <Icon className="h-3.5 w-3.5" />
      <span>{children}</span>
    </div>
  );
}

function PostingCard({
  platform,
  label,
  url,
}: {
  platform: PlatformKey;
  label: string;
  url: string;
}) {
  const [copied, setCopied] = useState(false);

  const copy = async (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error("Couldn't copy URL");
    }
  };

  return (
    <a
      href={url}
      target="_blank"
      rel="noreferrer"
      className="group flex items-center gap-3 rounded-2xl border border-border bg-card p-4 transition hover:border-foreground/30 hover:bg-accent/30"
    >
      <PlatformIcon platform={platform} />
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium text-foreground" title={url}>
          {label}
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-1">
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="h-7 w-7 opacity-0 transition group-hover:opacity-100 focus-visible:opacity-100"
          onClick={copy}
          aria-label="Copy URL"
        >
          {copied ? (
            <Check className="h-3.5 w-3.5 text-success" />
          ) : (
            <Copy className="h-3.5 w-3.5" />
          )}
        </Button>
        <ExternalLink className="h-3.5 w-3.5 text-muted-foreground transition group-hover:text-foreground" />
      </div>
    </a>
  );
}

function StatTile({
  label,
  value,
  accent,
}: {
  label: string;
  value: number;
  accent?: "emerald" | "blue";
}) {
  const tone =
    accent === "emerald"
      ? "text-success"
      : accent === "blue"
        ? "text-brand"
        : "text-foreground";
  return (
    <div className="rounded-xl border border-border bg-muted/40 px-3 py-2.5">
      <div className="font-mono text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
        {label}
      </div>
      <div className={`mt-1 text-xl font-extrabold tracking-tight ${tone}`}>{value}</div>
    </div>
  );
}

function buildDeptOverride(platform: AtsPlatform, dept: AtsDept): Record<string, unknown> {
  if (platform === "greenhouse") return { department_id: Number(dept.id) };
  if (platform === "ashby") return { departmentId: dept.id };
  // Lever accepts free-text team strings.
  return { team: dept.name };
}

function NotionParentField({
  effective,
  overridden,
  accessibilityError,
  notionConnected,
  onChange,
  onClearOverride,
}: {
  effective: { id: string; title: string } | null;
  overridden: boolean;
  accessibilityError: string | null;
  notionConnected: boolean;
  onChange: () => void;
  onClearOverride: () => void;
}) {
  // Five states, all surfaced inline so the publish form is self-describing:
  //   1. override set                              → "<title> · this requisition only"
  //   2. org default set + accessible              → "<title> · org default"
  //   3. org default set but inaccessible          → amber warning + Change
  //   4. no default, Notion connected              → "Pick a parent" prompt
  //   5. no default, Notion not connected          → "Set up Notion" prompt

  if (effective && overridden) {
    return (
      <div className="flex items-center justify-between rounded-xl border border-border bg-muted/40 px-3 py-2 text-sm">
        <div className="min-w-0">
          <span className="truncate font-medium">{effective.title}</span>
          <span className="ml-2 rounded-full bg-amber-tint px-2 py-0.5 text-[10px] font-bold text-amber">
            this requisition only
          </span>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={onClearOverride}
            className="text-xs text-muted-foreground hover:text-foreground"
          >
            Use org default
          </button>
          <button
            type="button"
            onClick={onChange}
            className="text-xs font-medium text-foreground hover:underline"
          >
            Change
          </button>
        </div>
      </div>
    );
  }

  if (effective) {
    return (
      <div className="flex items-center justify-between rounded-xl border border-border bg-muted/40 px-3 py-2 text-sm">
        <div className="min-w-0">
          <span className="truncate font-medium">{effective.title}</span>
          <span className="ml-2 text-xs text-muted-foreground">org default</span>
        </div>
        <button
          type="button"
          onClick={onChange}
          className="text-xs font-medium text-foreground hover:underline"
        >
          Change parent page
        </button>
      </div>
    );
  }

  if (accessibilityError) {
    return (
      <div className="rounded-xl border border-amber/30 bg-amber-tint px-3 py-2 text-sm">
        <p className="font-bold text-amber">
          Default Notion parent is no longer accessible
        </p>
        <p className="mt-1 text-xs text-amber/90">
          {accessibilityError}. The tracker won&apos;t be created unless you
          pick a different parent.
        </p>
        <button
          type="button"
          onClick={onChange}
          className="mt-2 text-xs font-medium text-foreground hover:underline"
        >
          Pick a parent page
        </button>
      </div>
    );
  }

  return (
    <div className="rounded border border-dashed border-border px-3 py-2 text-sm">
      <p className="text-muted-foreground">
        {notionConnected
          ? "No default Notion parent set — pick one to create a hiring tracker page."
          : "Connect Notion to create a hiring tracker page automatically."}
      </p>
      <button
        type="button"
        onClick={onChange}
        className="mt-2 text-xs font-medium text-foreground hover:underline"
      >
        {notionConnected ? "Pick a parent page" : "Set up Notion"}
      </button>
    </div>
  );
}

function SlackChannelField({
  effective,
  overridden,
  accessibilityError,
  slackConnected,
  onChange,
  onClearOverride,
}: {
  effective: { id: string; name: string } | null;
  overridden: boolean;
  accessibilityError: string | null;
  slackConnected: boolean;
  onChange: () => void;
  onClearOverride: () => void;
}) {
  // Same five-state UX as NotionParentField:
  //   1. override set                           → "<#name> · this requisition only"
  //   2. org default set + accessible           → "<#name> · org default"
  //   3. org default set but inaccessible       → amber warning + Change
  //   4. no default, Slack connected            → "Pick a channel" prompt
  //   5. no default, Slack not connected        → "Set up Slack" prompt

  if (effective && overridden) {
    return (
      <div className="flex items-center justify-between rounded-xl border border-border bg-muted/40 px-3 py-2 text-sm">
        <div className="min-w-0 flex items-center gap-1">
          <span className="text-muted-foreground">#</span>
          <span className="truncate font-medium">{effective.name}</span>
          <span className="ml-2 rounded-full bg-amber-tint px-2 py-0.5 text-[10px] font-bold text-amber">
            this requisition only
          </span>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={onClearOverride}
            className="text-xs text-muted-foreground hover:text-foreground"
          >
            Use org default
          </button>
          <button
            type="button"
            onClick={onChange}
            className="text-xs font-medium text-foreground hover:underline"
          >
            Change
          </button>
        </div>
      </div>
    );
  }

  if (effective) {
    return (
      <div className="flex items-center justify-between rounded-xl border border-border bg-muted/40 px-3 py-2 text-sm">
        <div className="min-w-0 flex items-center gap-1">
          <span className="text-muted-foreground">#</span>
          <span className="truncate font-medium">{effective.name}</span>
          <span className="ml-2 text-xs text-muted-foreground">org default</span>
        </div>
        <button
          type="button"
          onClick={onChange}
          className="text-xs font-medium text-foreground hover:underline"
        >
          Change channel
        </button>
      </div>
    );
  }

  if (accessibilityError) {
    return (
      <div className="rounded-xl border border-amber/30 bg-amber-tint px-3 py-2 text-sm">
        <p className="font-bold text-amber">
          Default Slack channel is no longer reachable
        </p>
        <p className="mt-1 text-xs text-amber/90">
          {accessibilityError}. The announcement won&apos;t post unless you
          pick a different channel.
        </p>
        <button
          type="button"
          onClick={onChange}
          className="mt-2 text-xs font-medium text-foreground hover:underline"
        >
          Pick a channel
        </button>
      </div>
    );
  }

  return (
    <div className="rounded border border-dashed border-border px-3 py-2 text-sm">
      <p className="text-muted-foreground">
        {slackConnected
          ? "No default Slack channel set — pick one to announce this opening."
          : "Connect Slack to announce new openings automatically."}
      </p>
      <button
        type="button"
        onClick={onChange}
        className="mt-2 text-xs font-medium text-foreground hover:underline"
      >
        {slackConnected ? "Pick a channel" : "Set up Slack"}
      </button>
    </div>
  );
}
