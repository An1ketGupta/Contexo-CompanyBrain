"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import useSWR from "swr";
import { toast } from "sonner";
import {
  Check,
  Copy,
  ExternalLink,
  Loader2,
  Pencil,
  Trash2,
} from "lucide-react";

import { Markdown } from "@/components/chat/markdown";

import { Badge } from "@/components/ui/badge";
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

interface NaukriSearch {
  label: string;
  url: string;
  description: string;
  query_summary?: string | null;
}

interface NaukriTaxonomy {
  functional_area_id: string | null;
  functional_area_name: string | null;
  role_category_id: string | null;
  role_category_name: string | null;
  industry_type_id: string | null;
  industry_type_name: string | null;
  experience_min_years: number | null;
  experience_max_years: number | null;
  key_skills: string[];
}

type AtsPlatform = "greenhouse" | "lever" | "ashby" | "naukri";
type DestinationType = "ats" | "job_board";

interface AtsPosting {
  platform: AtsPlatform;
  destination_type?: DestinationType | null;
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
  sourcing_templates: SourcingTemplate[];
  linkedin_search_urls: LinkedinSearch[];
  naukri_search_urls: NaukriSearch[];
  naukri_taxonomy: NaukriTaxonomy | null;
  hiring_manager_email: string | null;
  slack_channel: string | null;
  slack_post_error: string | null;
  status: "draft" | "published" | "failed";
  error_message: string | null;
}

// Grouped by kind so the publish form renders ATSes + Job Boards as two
// labeled sections. The kind tag mirrors the backend's destination_type.
const POSTING_DESTINATIONS: {
  value: AtsPlatform;
  label: string;
  kind: DestinationType;
}[] = [
  { value: "greenhouse", label: "Greenhouse", kind: "ats" },
  { value: "lever", label: "Lever", kind: "ats" },
  { value: "ashby", label: "Ashby", kind: "ats" },
  { value: "naukri", label: "Naukri.com", kind: "job_board" },
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
  // Naukri-only taxonomy state. Lifted out of NaukriTaxonomyFields so the
  // submit handler can serialise it without prop-drilling refs.
  const [naukriFunctionalArea, setNaukriFunctionalArea] = useState<AtsDept | null>(null);
  const [naukriRoleCategory, setNaukriRoleCategory] = useState<AtsDept | null>(null);
  const [naukriIndustry, setNaukriIndustry] = useState<AtsDept | null>(null);
  const [naukriExpMin, setNaukriExpMin] = useState<string>("");
  const [naukriExpMax, setNaukriExpMax] = useState<string>("");
  const [naukriKeySkills, setNaukriKeySkills] = useState<string>("");
  const [publishing, setPublishing] = useState(false);
  const [publishError, setPublishError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [syncing, setSyncing] = useState(false);
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
  const { data: naukriRoleCategoriesList } = useSWR<AtsDept[]>(
    selectedAts.has("naukri")
      ? "/api/integrations/ats/naukri/taxonomy/role_categories"
      : null,
    fetcher,
    { revalidateOnFocus: false },
  );
  const { data: naukriFunctionalAreas } = useSWR<AtsDept[]>(
    selectedAts.has("naukri")
      ? "/api/integrations/ats/naukri/taxonomy/functional_areas"
      : null,
    fetcher,
    { revalidateOnFocus: false },
  );
  const { data: naukriIndustries } = useSWR<AtsDept[]>(
    selectedAts.has("naukri")
      ? "/api/integrations/ats/naukri/taxonomy/industries"
      : null,
    fetcher,
    { revalidateOnFocus: false },
  );
  // Naukri connection status — surfaces whether the recruiter has finished
  // the Settings → Integrations connect step. Empty taxonomy dropdowns
  // (the symptom the user just hit) almost always mean "not connected yet".
  const { data: naukriConnectionStatus, mutate: mutateNaukriStatus } =
    useSWR<{ connected: boolean; mapping_cached_at: string | null }>(
      selectedAts.has("naukri") ? "/api/integrations/ats/status" : null,
      async (url: string) => {
        const r = await fetch(url);
        if (!r.ok) throw new Error(`Failed (${r.status})`);
        const body = await r.json();
        return body.naukri ?? { connected: false, mapping_cached_at: null };
      },
      { revalidateOnFocus: false },
    );
  const naukriConnected = naukriConnectionStatus?.connected === true;
  // After all three taxonomy SWRs settle, check whether any returned data.
  // We treat "all loaded AND all empty" as the empty-state signal — partial
  // emptiness (e.g. industries [] but functional_areas non-empty) is still
  // a misconfiguration worth showing the recruiter.
  const naukriTaxonomyLoaded =
    naukriFunctionalAreas !== undefined &&
    naukriIndustries !== undefined &&
    naukriRoleCategoriesList !== undefined;
  const naukriTaxonomyEmpty =
    naukriTaxonomyLoaded &&
    (naukriFunctionalAreas?.length ?? 0) === 0 &&
    (naukriIndustries?.length ?? 0) === 0 &&
    (naukriRoleCategoriesList?.length ?? 0) === 0;
  const deptsByPlatform: Record<AtsPlatform, AtsDept[]> = {
    greenhouse: greenhouseDepts ?? [],
    lever: leverDepts ?? [],
    ashby: ashbyDepts ?? [],
    naukri: naukriRoleCategoriesList ?? [],
  };

  const handlePublish = async () => {
    if (!id) return;
    if (selectedAtsList.length === 0) {
      setPublishError("Select at least one destination.");
      return;
    }
    // Naukri requires explicit taxonomy choices BEFORE we hit the network.
    // Surfacing the validation here keeps the recruiter inside the form
    // instead of bouncing on a 400 from the backend.
    if (selectedAts.has("naukri")) {
      if (!naukriFunctionalArea) {
        setPublishError("Naukri: pick a Functional Area.");
        return;
      }
      if (!naukriRoleCategory) {
        setPublishError("Naukri: pick a Role Category.");
        return;
      }
      if (!naukriIndustry) {
        setPublishError("Naukri: pick an Industry Type.");
        return;
      }
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
      // Build Naukri taxonomy payload only when Naukri is in the publish set.
      // Backend rejects naukri_taxonomy=null when "naukri" is selected.
      let naukriTaxonomyPayload: NaukriTaxonomy | null = null;
      if (selectedAts.has("naukri")) {
        const expMin = naukriExpMin.trim()
          ? Math.max(0, Math.min(40, Number(naukriExpMin)))
          : null;
        const expMax = naukriExpMax.trim()
          ? Math.max(0, Math.min(40, Number(naukriExpMax)))
          : null;
        const skills = naukriKeySkills
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean)
          .slice(0, 12);
        naukriTaxonomyPayload = {
          functional_area_id: naukriFunctionalArea?.id ?? null,
          functional_area_name: naukriFunctionalArea?.name ?? null,
          role_category_id: naukriRoleCategory?.id ?? null,
          role_category_name: naukriRoleCategory?.name ?? null,
          industry_type_id: naukriIndustry?.id ?? null,
          industry_type_name: naukriIndustry?.name ?? null,
          experience_min_years: Number.isFinite(expMin as number) ? expMin : null,
          experience_max_years: Number.isFinite(expMax as number) ? expMax : null,
          key_skills: skills,
        };
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
          naukri_taxonomy: naukriTaxonomyPayload,
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

  const canEdit = data.status === "draft" || data.status === "failed";

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6 md:p-8">
      <header>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <h1 className="text-2xl font-semibold tracking-tight">
              {data.role_request}
            </h1>
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
              {(data.ats_postings.length > 0
                ? data.ats_postings.map((p) => p.platform)
                : data.ats_platform
                  ? [data.ats_platform]
                  : []
              ).map((p) => (
                <Badge key={p} variant="outline">
                  {p}
                </Badge>
              ))}
              {data.location && (
                <Badge variant="outline">{data.location}</Badge>
              )}
              {data.department && (
                <Badge variant="outline">{data.department}</Badge>
              )}
              {data.stack && (
                <Badge variant="outline">{data.stack}</Badge>
              )}
            </div>
          </div>
          {canEdit && (
            <div className="flex gap-2">
              <Button
                size="sm"
                variant="outline"
                onClick={() => setEditing((v) => !v)}
              >
                <Pencil className="h-3.5 w-3.5" />
                {editing ? "Close" : "Edit"}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={handleDelete}
                disabled={deleting}
              >
                {deleting ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Trash2 className="h-3.5 w-3.5" />
                )}
                Delete
              </Button>
            </div>
          )}
        </div>

        {data.error_message && (
          <div className="mt-3 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
            {data.error_message}
          </div>
        )}
        {data.grounded === false && (
          <div className="mt-3 rounded border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-200">
            <div className="font-medium">No company context found</div>
            <p className="mt-1">
              No matching documents in your KB for this role. The JD variants
              below are model-generated and may contain plausible-sounding but
              fabricated comp ranges, stack, or reporting lines. Review
              carefully before publishing.
            </p>
          </div>
        )}
      </header>

      {editing && canEdit && (
        <EditRequisitionForm
          requisition={data}
          onSaved={async () => {
            await mutate();
            setEditing(false);
          }}
        />
      )}

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
          <div className="mt-4 rounded border border-border bg-card p-6">
            <Markdown>{activeVariant.text}</Markdown>
          </div>
        )}
      </section>

      {/* Publish form */}
      {!isPublished && (
        <section className="rounded border border-border bg-card p-6">
          <h2 className="mb-4 text-sm font-medium">Publish</h2>
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2 md:col-span-2">
              <Label>Posting destinations</Label>
              <p className="text-xs text-muted-foreground">
                Pick one or more. The job posts to every checked destination in
                parallel; if any one fails the others still go through.
              </p>
              {/* Grouped by kind so the recruiter sees ATSes (internal
                  hiring systems) and Job boards (external candidate reach)
                  as visually distinct sets. Backed by the destination_type
                  metadata from posting_registry on the server. */}
              {(["ats", "job_board"] as const).map((kind) => {
                const items = POSTING_DESTINATIONS.filter((p) => p.kind === kind);
                if (items.length === 0) return null;
                return (
                  <div key={kind} className="space-y-1 pt-1">
                    <div className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/70">
                      {kind === "ats" ? "ATS platforms" : "Job boards"}
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {items.map((p) => {
                        const active = selectedAts.has(p.value);
                        return (
                          <label
                            key={p.value}
                            className={`relative flex cursor-pointer items-center gap-2 rounded border px-3 py-2 text-sm transition ${
                              active
                                ? "border-foreground bg-foreground text-background"
                                : "border-input bg-background hover:border-foreground/50"
                            }`}
                          >
                            <input
                              type="checkbox"
                              className="sr-only"
                              checked={active}
                              onChange={() => toggleAts(p.value)}
                            />
                            <span
                              aria-hidden
                              className={`h-3 w-3 rounded-sm border ${
                                active
                                  ? "border-background bg-background"
                                  : "border-foreground/40"
                              }`}
                            />
                            {p.label}
                          </label>
                        );
                      })}
                    </div>
                  </div>
                );
              })}
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

          {/* Department selection — one dropdown per selected ATS.
              For Naukri this dropdown is the Role Category (Naukri's closest
              analogue to "department"); the other two Naukri-required
              taxonomy fields (Functional Area + Industry) live in the
              dedicated section below. */}
          {selectedAtsList.some((p) => deptsByPlatform[p].length > 0) && (
            <div className="mt-4 space-y-3">
              {selectedAtsList.map((p) => {
                const depts = deptsByPlatform[p];
                if (!depts.length) return null;
                const label =
                  p === "naukri" ? "Naukri — Role Category" : `${p} — Department`;
                return (
                  <div key={p} className="space-y-1">
                    <Label className="text-xs text-muted-foreground capitalize">
                      {label}
                    </Label>
                    <select
                      className="w-full rounded border border-input bg-background px-3 py-2 text-sm"
                      value={
                        p === "naukri"
                          ? naukriRoleCategory?.id ?? ""
                          : deptSelections[p]?.id ?? ""
                      }
                      onChange={(e) => {
                        const dept = depts.find((d) => d.id === e.target.value);
                        if (p === "naukri") {
                          setNaukriRoleCategory(dept ?? null);
                        } else if (dept) {
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
                        {p === "naukri" ? "Select Role Category" : "Select department (optional)"}
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

          {/* Naukri-only taxonomy section — only visible when Naukri is in
              the publish set. Three required dropdowns (Functional Area,
              Role Category, Industry) plus optional experience band + key
              skills. Backend rejects the publish if these aren't set. */}
          {selectedAts.has("naukri") && (
            <div className="mt-5 space-y-3 rounded border border-dashed border-border bg-muted/20 p-4">
              <div className="flex items-center justify-between">
                <Label className="text-xs font-semibold uppercase tracking-wider text-muted-foreground/70">
                  Naukri taxonomy
                </Label>
                <a
                  href="https://www.naukri.com/recruiter/help/hotvacancy"
                  target="_blank"
                  rel="noreferrer"
                  className="text-[11px] text-muted-foreground hover:underline"
                >
                  About these fields
                </a>
              </div>
              <p className="text-xs text-muted-foreground">
                Required by Naukri at post time. Pick the closest match — the
                wrong selection routes the JD to the wrong candidate audience.
              </p>

              {/* Empty-state banner: dropdowns are empty because either the
                  recruiter hasn't connected Naukri or the mapping cache
                  wasn't warmed. Show actionable guidance instead of three
                  silently-empty selects. */}
              {naukriTaxonomyEmpty && !naukriConnected && (
                <div className="rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm dark:border-amber-500/40 dark:bg-amber-500/10">
                  <p className="font-medium text-amber-900 dark:text-amber-200">
                    Connect Naukri to load the taxonomy
                  </p>
                  <p className="mt-1 text-xs text-amber-800 dark:text-amber-300">
                    The Functional Area, Role Category, and Industry lists
                    come from Naukri at connect time. Until you add an API
                    key in Settings → Integrations, these dropdowns will be
                    empty and Naukri publishes will fail.
                  </p>
                  <a
                    href="/settings/integrations"
                    className="mt-2 inline-block text-xs font-medium text-amber-900 underline hover:no-underline dark:text-amber-200"
                  >
                    Open Settings → Integrations →
                  </a>
                </div>
              )}
              {naukriTaxonomyEmpty && naukriConnected && (
                <div className="rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm dark:border-amber-500/40 dark:bg-amber-500/10">
                  <p className="font-medium text-amber-900 dark:text-amber-200">
                    Naukri is connected, but the taxonomy cache is empty
                  </p>
                  <p className="mt-1 text-xs text-amber-800 dark:text-amber-300">
                    The connect step warms the cache automatically — this
                    usually means the Naukri taxonomy call failed (e.g.
                    network blip or mock server wasn&apos;t running).
                    Refresh below to retry without re-pasting your API key.
                  </p>
                  <button
                    type="button"
                    onClick={async () => {
                      const res = await fetch(
                        "/api/integrations/ats/naukri/refresh-mapping",
                        { method: "POST" },
                      );
                      if (!res.ok) {
                        toast.error("Refresh failed — check the mock server is running.");
                        return;
                      }
                      toast.success("Naukri taxonomy refreshed");
                      mutateNaukriStatus();
                    }}
                    className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-amber-900 underline hover:no-underline dark:text-amber-200"
                  >
                    Refresh mapping cache
                  </button>
                </div>
              )}

              <div className="grid gap-3 md:grid-cols-2">
                <div className="space-y-1">
                  <Label className="text-xs">Functional Area</Label>
                  <select
                    className="w-full rounded border border-input bg-background px-3 py-2 text-sm"
                    value={naukriFunctionalArea?.id ?? ""}
                    onChange={(e) => {
                      const opt = (naukriFunctionalAreas ?? []).find(
                        (d) => d.id === e.target.value,
                      );
                      setNaukriFunctionalArea(opt ?? null);
                    }}
                  >
                    <option value="">Select Functional Area</option>
                    {(naukriFunctionalAreas ?? []).map((d) => (
                      <option key={d.id} value={d.id}>{d.name}</option>
                    ))}
                  </select>
                </div>

                <div className="space-y-1">
                  <Label className="text-xs">Industry Type</Label>
                  <select
                    className="w-full rounded border border-input bg-background px-3 py-2 text-sm"
                    value={naukriIndustry?.id ?? ""}
                    onChange={(e) => {
                      const opt = (naukriIndustries ?? []).find(
                        (d) => d.id === e.target.value,
                      );
                      setNaukriIndustry(opt ?? null);
                    }}
                  >
                    <option value="">Select Industry</option>
                    {(naukriIndustries ?? []).map((d) => (
                      <option key={d.id} value={d.id}>{d.name}</option>
                    ))}
                  </select>
                </div>

                <div className="space-y-1">
                  <Label htmlFor="naukri-exp-min" className="text-xs">
                    Experience (min years)
                  </Label>
                  <Input
                    id="naukri-exp-min"
                    type="number"
                    min={0}
                    max={40}
                    placeholder="e.g. 3"
                    value={naukriExpMin}
                    onChange={(e) => setNaukriExpMin(e.target.value)}
                  />
                </div>

                <div className="space-y-1">
                  <Label htmlFor="naukri-exp-max" className="text-xs">
                    Experience (max years)
                  </Label>
                  <Input
                    id="naukri-exp-max"
                    type="number"
                    min={0}
                    max={40}
                    placeholder="e.g. 6"
                    value={naukriExpMax}
                    onChange={(e) => setNaukriExpMax(e.target.value)}
                  />
                </div>

                <div className="space-y-1 md:col-span-2">
                  <Label htmlFor="naukri-skills" className="text-xs">
                    Key skills (comma-separated, max 12)
                  </Label>
                  <Input
                    id="naukri-skills"
                    placeholder="python, fastapi, postgres, kafka"
                    value={naukriKeySkills}
                    onChange={(e) => setNaukriKeySkills(e.target.value)}
                  />
                  <p className="text-[11px] text-muted-foreground">
                    Used both for the Naukri job ad and to seed the Resdex
                    candidate-search shortcuts we generate on publish.
                  </p>
                </div>
              </div>
            </div>
          )}

          {publishError && (
            <div className="mt-4 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
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

      {/* Published outputs */}
      {isPublished && (
        <section className="space-y-4">
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
              <a
                key={p.platform}
                href={p.url}
                target="_blank"
                rel="noreferrer"
                className="block rounded border border-border bg-card p-4 transition hover:bg-accent/40"
              >
                <div className="text-xs text-muted-foreground">
                  {p.platform.charAt(0).toUpperCase() + p.platform.slice(1)} posting
                </div>
                <div className="mt-1 font-medium">{p.url}</div>
              </a>
            ) : (
              <div
                key={p.platform}
                className="rounded border border-red-200 bg-red-50 p-4 dark:border-red-900 dark:bg-red-950/40"
              >
                <div className="text-xs text-red-700 dark:text-red-300">
                  {p.platform.charAt(0).toUpperCase() + p.platform.slice(1)} —
                  publish failed
                </div>
                <div className="mt-1 break-all text-sm text-red-800 dark:text-red-200">
                  {p.error}
                </div>
              </div>
            ),
          )}
          {data.notion_tracker_url && (
            <a
              href={data.notion_tracker_url}
              target="_blank"
              rel="noreferrer"
              className="block rounded border border-border bg-card p-4 transition hover:bg-accent/40"
            >
              <div className="text-xs text-muted-foreground">Notion hiring tracker</div>
              <div className="mt-1 font-medium">{data.notion_tracker_url}</div>
            </a>
          )}

          {isPublished && (
            <div className="rounded border border-border bg-card p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <div className="text-xs text-muted-foreground">
                    Candidate sync
                  </div>
                  <div className="mt-1 text-sm">
                    {data.candidates_last_synced_at
                      ? `Last synced ${new Date(data.candidates_last_synced_at).toLocaleString()}`
                      : "Pulls candidates from every connected ATS into the Notion tracker."}
                  </div>
                </div>
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
                    "Sync candidates"
                  )}
                </Button>
              </div>

              {!data.notion_candidates_db_id && (
                <div className="mt-2 text-xs text-amber-700 dark:text-amber-300">
                  This requisition was published before candidate sync was
                  available. Republish to add a Notion candidate database.
                </div>
              )}

              {syncSummary && (
                <div className="mt-3 space-y-1 text-xs text-muted-foreground">
                  <div>
                    <span className="font-medium text-foreground">
                      {syncSummary.total}
                    </span>{" "}
                    candidates synced ·{" "}
                    <span className="font-medium text-foreground">
                      {syncSummary.new}
                    </span>{" "}
                    new ·{" "}
                    <span className="font-medium text-foreground">
                      {syncSummary.updated}
                    </span>{" "}
                    updated
                  </div>
                  {Object.entries(syncSummary.per_platform).map(
                    ([platform, info]) => (
                      <div key={platform}>
                        <span className="capitalize">{platform}</span>:{" "}
                        {info.error ? (
                          <span className="text-red-600 dark:text-red-300">
                            {info.error}
                          </span>
                        ) : (
                          `${info.count} candidate${info.count === 1 ? "" : "s"}`
                        )}
                      </div>
                    ),
                  )}
                </div>
              )}

              {data.candidates_last_sync_error && !syncSummary && (
                <div className="mt-2 break-words text-xs text-red-700 dark:text-red-300">
                  Last sync had errors: {data.candidates_last_sync_error}
                </div>
              )}
            </div>
          )}

          {data.slack_post_error && (
            <div className="rounded border border-amber-300 bg-amber-50 p-4 dark:border-amber-500/40 dark:bg-amber-500/10">
              <div className="text-xs font-medium text-amber-900 dark:text-amber-200">
                Slack announcement didn&apos;t post
              </div>
              <div className="mt-1 text-sm text-amber-800 dark:text-amber-200">
                {data.slack_post_error === "slack_not_in_channel"
                  ? "The bot isn't a member of the channel. Invite it (/invite @NirnayaIQ) and republish from a new requisition."
                  : data.slack_post_error}
              </div>
              {data.slack_channel && (
                <div className="mt-2 text-xs text-amber-700 dark:text-amber-300">
                  Channel id: <code>{data.slack_channel}</code>
                </div>
              )}
            </div>
          )}

          {data.linkedin_search_urls?.length > 0 && (
            <div className="rounded border border-border bg-card p-4">
              <div className="text-xs text-muted-foreground">
                LinkedIn search shortcuts
              </div>
              <TooltipProvider delayDuration={150}>
                <ul className="mt-3 space-y-2 text-sm">
                  {data.linkedin_search_urls.map((s, i) => (
                    <LinkedinSearchRow key={i} search={s} />
                  ))}
                </ul>
              </TooltipProvider>
            </div>
          )}

          {data.naukri_search_urls?.length > 0 && (
            <div className="rounded border border-border bg-card p-4">
              <div className="flex items-center justify-between">
                <div className="text-xs text-muted-foreground">
                  Naukri Resdex search shortcuts
                </div>
                <span className="rounded bg-indigo-100 px-1.5 py-0.5 text-[10px] font-medium text-indigo-700 dark:bg-indigo-500/20 dark:text-indigo-300">
                  India · Resdex
                </span>
              </div>
              <p className="mt-1 text-[11px] text-muted-foreground">
                Pre-filtered candidate-search URLs tuned for the Indian
                market. Click to land in Resdex with the filters applied —
                you'll need an active Resdex subscription to browse results.
              </p>
              <TooltipProvider delayDuration={150}>
                <ul className="mt-3 space-y-2 text-sm">
                  {data.naukri_search_urls.map((s, i) => (
                    <NaukriSearchRow key={i} search={s} />
                  ))}
                </ul>
              </TooltipProvider>
            </div>
          )}

          {data.sourcing_templates?.length > 0 && (
            <div className="rounded border border-border bg-card p-4">
              <div className="text-xs text-muted-foreground">
                Sourcing drafts (copy-paste into LinkedIn)
              </div>
              <ul className="mt-3 space-y-3 text-sm">
                {data.sourcing_templates.map((t, i) => (
                  <li
                    key={i}
                    className="rounded border border-border/60 bg-muted/40 p-3 text-foreground"
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
            </div>
          )}
        </section>
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
    <li className="group flex items-start gap-2 rounded border border-transparent p-2 transition hover:border-border hover:bg-accent/30">
      <div className="min-w-0 flex-1">
        <a
          href={search.url}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1 font-medium text-blue-600 hover:underline"
        >
          {search.label}
          <ExternalLink className="h-3 w-3" />
        </a>
        <p className="mt-0.5 text-xs text-muted-foreground">
          {search.description}
        </p>
      </div>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="h-7 w-7 shrink-0 opacity-0 transition group-hover:opacity-100 focus-visible:opacity-100"
            onClick={copy}
            aria-label="Copy LinkedIn search URL"
          >
            {copied ? (
              <Check className="h-3.5 w-3.5 text-emerald-600" />
            ) : (
              <Copy className="h-3.5 w-3.5" />
            )}
          </Button>
        </TooltipTrigger>
        <TooltipContent>{copied ? "Copied" : "Copy URL"}</TooltipContent>
      </Tooltip>
    </li>
  );
}

function NaukriSearchRow({ search }: { search: NaukriSearch }) {
  // Same UX as LinkedinSearchRow — title + description + copy button. Extra
  // line for query_summary (renders the active filter set inline so the
  // recruiter doesn't have to parse the URL).
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
    <li className="group flex items-start gap-2 rounded border border-transparent p-2 transition hover:border-border hover:bg-accent/30">
      <div className="min-w-0 flex-1">
        <a
          href={search.url}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1 font-medium text-indigo-600 hover:underline"
        >
          {search.label}
          <ExternalLink className="h-3 w-3" />
        </a>
        <p className="mt-0.5 text-xs text-muted-foreground">
          {search.description}
        </p>
        {search.query_summary && (
          <p className="mt-0.5 text-[11px] text-muted-foreground/80">
            <span className="font-medium">Filters:</span> {search.query_summary}
          </p>
        )}
      </div>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="h-7 w-7 shrink-0 opacity-0 transition group-hover:opacity-100 focus-visible:opacity-100"
            onClick={copy}
            aria-label="Copy Naukri search URL"
          >
            {copied ? (
              <Check className="h-3.5 w-3.5 text-emerald-600" />
            ) : (
              <Copy className="h-3.5 w-3.5" />
            )}
          </Button>
        </TooltipTrigger>
        <TooltipContent>{copied ? "Copied" : "Copy URL"}</TooltipContent>
      </Tooltip>
    </li>
  );
}

function buildDeptOverride(platform: AtsPlatform, dept: AtsDept): Record<string, unknown> {
  if (platform === "greenhouse") return { department_id: Number(dept.id) };
  if (platform === "ashby") return { departmentId: dept.id };
  // Naukri doesn't ship a department concept — the Role Category mapping
  // is layered separately via naukri_taxonomy. The "team" key remains the
  // best signal for Lever which accepts free-text team strings.
  return { team: dept.name };
}

function EditRequisitionForm({
  requisition,
  onSaved,
}: {
  requisition: Requisition;
  onSaved: () => Promise<void>;
}) {
  const [roleRequest, setRoleRequest] = useState(requisition.role_request);
  const [location, setLocation] = useState(requisition.location ?? "");
  const [department, setDepartment] = useState(requisition.department ?? "");
  const [contextNotes, setContextNotes] = useState(requisition.context_notes ?? "");
  const [regenerate, setRegenerate] = useState(false);
  const [saving, setSaving] = useState(false);

  const save = async () => {
    setSaving(true);
    try {
      const res = await fetch(`/api/recruiting/requisitions/${requisition.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          role_request: roleRequest !== requisition.role_request ? roleRequest : null,
          location: location !== (requisition.location ?? "") ? location : null,
          department:
            department !== (requisition.department ?? "") ? department : null,
          context_notes:
            contextNotes !== (requisition.context_notes ?? "") ? contextNotes : null,
          regenerate_variants: regenerate,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        toast.error(body?.message || body?.detail || "Save failed");
        return;
      }
      toast.success(regenerate ? "Variants regenerated" : "Requisition updated");
      await onSaved();
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="rounded border border-border bg-card p-6">
      <h2 className="mb-4 text-sm font-medium">Edit requisition</h2>
      <div className="grid gap-4 md:grid-cols-2">
        <div className="space-y-2 md:col-span-2">
          <Label htmlFor="role-edit">Role request</Label>
          <Textarea
            id="role-edit"
            rows={2}
            value={roleRequest}
            onChange={(e) => setRoleRequest(e.target.value)}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="loc-edit">Location</Label>
          <Input
            id="loc-edit"
            value={location}
            onChange={(e) => setLocation(e.target.value)}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="dept-edit">Department</Label>
          <Input
            id="dept-edit"
            value={department}
            onChange={(e) => setDepartment(e.target.value)}
          />
        </div>
        <div className="space-y-2 md:col-span-2">
          <Label htmlFor="ctx-edit">Hire-specific context</Label>
          <Textarea
            id="ctx-edit"
            rows={4}
            value={contextNotes}
            onChange={(e) => setContextNotes(e.target.value)}
          />
        </div>
      </div>

      <label className="mt-4 flex items-center gap-2 text-xs">
        <input
          type="checkbox"
          checked={regenerate}
          onChange={(e) => setRegenerate(e.target.checked)}
        />
        Regenerate all JD variants
      </label>

      <div className="mt-4 flex justify-end">
        <Button onClick={save} disabled={saving}>
          {saving ? (
            <>
              <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" /> Saving…
            </>
          ) : (
            "Save changes"
          )}
        </Button>
      </div>
    </section>
  );
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
      <div className="flex items-center justify-between rounded border border-border bg-muted/30 px-3 py-2 text-sm">
        <div className="min-w-0">
          <span className="truncate font-medium">{effective.title}</span>
          <span className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium text-amber-800 dark:bg-amber-500/20 dark:text-amber-200">
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
      <div className="flex items-center justify-between rounded border border-border bg-muted/30 px-3 py-2 text-sm">
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
      <div className="rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm dark:border-amber-500/40 dark:bg-amber-500/10">
        <p className="font-medium text-amber-900 dark:text-amber-200">
          Default Notion parent is no longer accessible
        </p>
        <p className="mt-1 text-xs text-amber-800 dark:text-amber-300">
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
      <div className="flex items-center justify-between rounded border border-border bg-muted/30 px-3 py-2 text-sm">
        <div className="min-w-0 flex items-center gap-1">
          <span className="text-muted-foreground">#</span>
          <span className="truncate font-medium">{effective.name}</span>
          <span className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium text-amber-800 dark:bg-amber-500/20 dark:text-amber-200">
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
      <div className="flex items-center justify-between rounded border border-border bg-muted/30 px-3 py-2 text-sm">
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
      <div className="rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm dark:border-amber-500/40 dark:bg-amber-500/10">
        <p className="font-medium text-amber-900 dark:text-amber-200">
          Default Slack channel is no longer reachable
        </p>
        <p className="mt-1 text-xs text-amber-800 dark:text-amber-300">
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
