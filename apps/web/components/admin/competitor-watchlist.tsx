"use client";

import { useEffect, useState } from "react";
import useSWR from "swr";
import { Loader2, ShieldAlert, X } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { parseApiError, reportApiError } from "@/lib/errors";

// Two layers, surfaced as sub-sections of a single panel so the user
// understands the org list is the policy floor and the personal list is an
// optional overlay. Both lists save independently.
//
// Editor pattern: chip input. Type a name, press Enter / comma / Tab to
// commit, click X on a chip to remove. Save button is per-section and
// only enabled when the list differs from the server snapshot.

interface CompetitorListResponse {
  names: string[];
  max: number;
}

const jsonFetcher = async <T,>(url: string): Promise<T> => {
  const res = await fetch(url);
  if (res.status === 401) {
    if (typeof window !== "undefined") {
      const next = encodeURIComponent(
        window.location.pathname + window.location.search,
      );
      window.location.href = `/login?redirectedFrom=${next}`;
    }
    throw new Error("Unauthorized");
  }
  if (!res.ok) throw new Error(`Failed (${res.status})`);
  return res.json();
};

export function CompetitorWatchlist({ canEditOrg }: { canEditOrg: boolean }) {
  return (
    <section className="rounded-2xl border border-border bg-card">
      <header className="border-b border-border px-5 py-4">
        <h2 className="text-sm font-semibold tracking-tight text-foreground">
          Watched terms
        </h2>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Flag when AI outputs mention specific companies. We highlight matches
          inline, ask you to confirm before exporting flagged content, and log
          every hit to the mentions queue.
        </p>
      </header>
      <div className="space-y-6 px-5 py-4">
        <CompetitorListEditor
          endpoint="/api/organizations/me/competitors"
          label="Workspace list"
          help={
            canEditOrg
              ? "Applied to every chat and agent output in this workspace. Whole-word, case-insensitive."
              : "Workspace-wide list managed by your admins. Visible so you know what's being watched."
          }
          canEdit={canEditOrg}
          icon={<ShieldAlert className="h-4 w-4 text-amber" />}
        />
        <div className="border-t border-border" />
        <CompetitorListEditor
          endpoint="/api/users/me/competitors"
          label="Your personal watchlist"
          help="Extra terms layered on top of the workspace list — only your own outputs are scanned."
          canEdit={true}
          icon={null}
        />
      </div>
    </section>
  );
}

function CompetitorListEditor({
  endpoint,
  label,
  help,
  canEdit,
  icon,
}: {
  endpoint: string;
  label: string;
  help: string;
  canEdit: boolean;
  icon: React.ReactNode;
}) {
  const { data, error, isLoading, mutate } = useSWR<CompetitorListResponse>(
    endpoint,
    jsonFetcher,
    { revalidateOnFocus: false },
  );

  const initial = data?.names ?? [];
  const max = data?.max ?? 100;

  const [names, setNames] = useState<string[]>(initial);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);

  // Sync local state when server-side list arrives or refreshes.
  useEffect(() => {
    setNames(initial);
    // Intentionally use the joined snapshot as the dep so a refetch with the
    // same array contents doesn't churn local state mid-edit.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initial.join("")]);

  const dirty =
    names.length !== initial.length || names.some((n, i) => n !== initial[i]);

  const commitDraft = () => {
    const cleaned = draft.trim();
    if (!cleaned) {
      setDraft("");
      return;
    }
    if (cleaned.length > 200) {
      toast.error("Competitor name is too long (max 200 chars).");
      return;
    }
    if (names.length >= max) {
      toast.error(`Limit reached — at most ${max} names.`);
      return;
    }
    if (names.some((n) => n.toLowerCase() === cleaned.toLowerCase())) {
      setDraft("");
      return;
    }
    setNames([...names, cleaned]);
    setDraft("");
  };

  const removeAt = (idx: number) => {
    setNames(names.filter((_, i) => i !== idx));
  };

  const save = async () => {
    setSaving(true);
    try {
      const res = await fetch(endpoint, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ names }),
      });
      if (!res.ok) throw await parseApiError(res);
      const next = (await res.json()) as CompetitorListResponse;
      await mutate(next, { revalidate: false });
      toast.success("Watchlist updated.");
    } catch (err) {
      reportApiError(err as Awaited<ReturnType<typeof parseApiError>>);
    } finally {
      setSaving(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" || e.key === "," || e.key === "Tab") {
      if (draft.trim()) {
        e.preventDefault();
        commitDraft();
      }
      return;
    }
    if (e.key === "Backspace" && !draft && names.length > 0) {
      // Quick undo of the most recent chip without aiming for it.
      e.preventDefault();
      setNames(names.slice(0, -1));
    }
  };

  if (isLoading) {
    return <Skeleton className="h-24" />;
  }
  if (error || !data) {
    return (
      <p className="text-sm text-destructive-ink">
        Couldn&apos;t load the watchlist. Try again later.
      </p>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex items-start gap-2">
        {icon}
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-foreground">{label}</p>
          <p className="mt-0.5 text-xs text-muted-foreground">{help}</p>
        </div>
      </div>

      <div
        className={`flex flex-wrap items-center gap-1.5 rounded-xl border border-input bg-background p-2 text-sm ${
          canEdit ? "" : "opacity-70"
        }`}
      >
        {names.map((name, idx) => (
          <span
            key={`${name}-${idx}`}
            className="inline-flex items-center gap-1 rounded-full border border-border bg-muted px-2 py-0.5 text-xs"
          >
            {name}
            {canEdit && (
              <button
                type="button"
                onClick={() => removeAt(idx)}
                className="rounded-full p-0.5 text-muted-foreground hover:bg-background hover:text-foreground"
                aria-label={`Remove ${name}`}
              >
                <X className="h-3 w-3" />
              </button>
            )}
          </span>
        ))}
        {canEdit && (
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={handleKeyDown}
            onBlur={() => draft.trim() && commitDraft()}
            placeholder={
              names.length === 0
                ? "Type a name and press Enter…"
                : "Add another…"
            }
            disabled={saving || names.length >= max}
            className="flex-1 min-w-[10rem] bg-transparent text-sm outline-none placeholder:text-muted-foreground"
            maxLength={200}
          />
        )}
      </div>

      <div className="flex items-center justify-between text-[11px] text-muted-foreground">
        <span>
          {names.length}/{max}
        </span>
        {canEdit && (
          <Button
            onClick={save}
            disabled={!dirty || saving}
            size="sm"
            variant={dirty ? "primary" : "ghost"}
          >
            {saving && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
            Save
          </Button>
        )}
      </div>
    </div>
  );
}
