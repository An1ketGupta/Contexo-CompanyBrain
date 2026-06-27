"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import useSWR from "swr";
import { toast } from "sonner";
import {
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
import { NotionParentPicker } from "@/components/recruiting/notion-parent-picker";

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

type AtsPlatform = "greenhouse" | "lever" | "ashby";

interface AtsPosting {
  platform: AtsPlatform;
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
  grounded: boolean;
  jd_variants: JdVariant[];
  selected_variant_index: number | null;
  ats_platform: AtsPlatform | null;
  ats_job_id: string | null;
  ats_url: string | null;
  ats_postings: AtsPosting[];
  notion_tracker_url: string | null;
  sourcing_templates: SourcingTemplate[];
  linkedin_search_urls: string[];
  hiring_manager_email: string | null;
  slack_channel: string | null;
  status: "draft" | "published" | "failed";
  error_message: string | null;
}

const ATS_PLATFORMS: { value: AtsPlatform; label: string }[] = [
  { value: "greenhouse", label: "Greenhouse" },
  { value: "lever", label: "Lever" },
  { value: "ashby", label: "Ashby" },
];

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

  const [selectedIdx, setSelectedIdx] = useState<number>(0);
  const [selectedAts, setSelectedAts] = useState<Set<AtsPlatform>>(
    () => new Set<AtsPlatform>(["greenhouse"]),
  );
  const [hiringManagerEmail, setHiringManagerEmail] = useState("");
  const [slackChannel, setSlackChannel] = useState("");
  // null = "use the org default"; non-null = per-requisition override.
  const [notionParentOverride, setNotionParentOverride] = useState<
    { id: string; title: string } | null
  >(null);
  const [notionPickerOpen, setNotionPickerOpen] = useState(false);
  const [locationOverride, setLocationOverride] = useState("");
  const [departmentOverride, setDepartmentOverride] = useState("");
  const [deptSelections, setDeptSelections] = useState<Record<string, AtsDept>>({});
  const [publishing, setPublishing] = useState(false);
  const [publishError, setPublishError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);

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
    if (data.slack_channel && !slackChannel) setSlackChannel(data.slack_channel);
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
  const { data: deptsByPlatform } = useSWR<Record<AtsPlatform, AtsDept[]>>(
    selectedAtsList.length > 0 ? `/api/integrations/ats/departments|${selectedAtsList.join(",")}` : null,
    async () => {
      const entries = await Promise.all(
        selectedAtsList.map(async (p) => {
          const res = await fetch(`/api/integrations/ats/${p}/departments`);
          return [p, res.ok ? await res.json() : []] as const;
        }),
      );
      return Object.fromEntries(entries) as Record<AtsPlatform, AtsDept[]>;
    },
    { revalidateOnFocus: false },
  );

  const handlePublish = async () => {
    if (!id) return;
    if (selectedAtsList.length === 0) {
      setPublishError("Select at least one ATS platform.");
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
          slack_channel: slackChannel || null,
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
              <Label>ATS platforms</Label>
              <p className="text-xs text-muted-foreground">
                Pick one or more. The job posts to every checked platform in
                parallel; if any one fails the others still go through.
              </p>
              <div className="flex flex-wrap gap-2 pt-1">
                {ATS_PLATFORMS.map((p) => {
                  const active = selectedAts.has(p.value);
                  return (
                    <label
                      key={p.value}
                      className={`flex cursor-pointer items-center gap-2 rounded border px-3 py-2 text-sm transition ${
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
            <div className="space-y-2">
              <Label htmlFor="location-pub">Location</Label>
              <Input
                id="location-pub"
                value={locationOverride}
                onChange={(e) => {
                  setLocationOverride(e.target.value);
                  setMappingOverrides({} as Record<AtsPlatform, Record<string, unknown>>);
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
                  setMappingOverrides({} as Record<AtsPlatform, Record<string, unknown>>);
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
            <div className="space-y-2">
              <Label htmlFor="slack">Slack channel id (optional)</Label>
              <Input
                id="slack"
                placeholder="C0123456789"
                value={slackChannel}
                onChange={(e) => setSlackChannel(e.target.value)}
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

          {/* Department selection — one dropdown per selected ATS */}
          {selectedAtsList.some((p) => (deptsByPlatform?.[p]?.length ?? 0) > 0) && (
            <div className="mt-4 space-y-3">
              {selectedAtsList.map((p) => {
                const depts = deptsByPlatform?.[p] ?? [];
                if (!depts.length) return null;
                return (
                  <div key={p} className="space-y-1">
                    <Label className="text-xs text-muted-foreground capitalize">
                      {p} — Department
                    </Label>
                    <select
                      className="w-full rounded border border-input bg-background px-3 py-2 text-sm"
                      value={deptSelections[p]?.id ?? ""}
                      onChange={(e) => {
                        const dept = depts.find((d) => d.id === e.target.value);
                        if (dept) setDeptSelections((prev) => ({ ...prev, [p]: dept }));
                        else setDeptSelections((prev) => { const next = { ...prev }; delete next[p]; return next; });
                      }}
                    >
                      <option value="">Select department (optional)</option>
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
                : `Publish to ${selectedAtsList.length || 0} ATS${selectedAtsList.length === 1 ? "" : "s"}`}
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
                className="block rounded border border-border bg-card p-4 hover:bg-zinc-50"
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
              className="block rounded border border-border bg-card p-4 hover:bg-zinc-50"
            >
              <div className="text-xs text-muted-foreground">Notion hiring tracker</div>
              <div className="mt-1 font-medium">{data.notion_tracker_url}</div>
            </a>
          )}

          {data.linkedin_search_urls?.length > 0 && (
            <div className="rounded border border-border bg-card p-4">
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
            <div className="rounded border border-border bg-card p-4">
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

function buildDeptOverride(platform: AtsPlatform, dept: AtsDept): Record<string, unknown> {
  if (platform === "greenhouse") return { department_id: Number(dept.id) };
  if (platform === "ashby") return { departmentId: dept.id };
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
