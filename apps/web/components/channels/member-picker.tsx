"use client";

import { useEffect, useMemo, useState } from "react";
import { Check, Loader2, Search, Users } from "lucide-react";
import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";

export interface OrgMember {
  id: string;
  display_name: string | null;
  email: string | null;
  role: string;
}

function labelFor(m: OrgMember): string {
  return m.display_name || m.email?.split("@")[0] || "Someone";
}

function initials(m: OrgMember): string {
  return labelFor(m).slice(0, 2).toUpperCase();
}

/**
 * Searchable multi-select over the caller's org members. Self-contained: fetches
 * `/api/organizations/members` once and filters client-side. `excludeIds` drops
 * rows that can't be picked (the creator, already-in-channel members).
 */
export function MemberPicker({
  selected,
  onChange,
  excludeIds = [],
}: {
  selected: string[];
  onChange: (ids: string[]) => void;
  excludeIds?: string[];
}) {
  const [members, setMembers] = useState<OrgMember[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch("/api/organizations/members");
        if (!res.ok) throw new Error(`Couldn't load teammates (${res.status})`);
        const data = await res.json();
        if (!cancelled) setMembers((data.members as OrgMember[]) ?? []);
      } catch (err: unknown) {
        if (!cancelled)
          setError(err instanceof Error ? err.message : "Couldn't load teammates.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const excluded = useMemo(() => new Set(excludeIds), [excludeIds]);
  const selectedSet = useMemo(() => new Set(selected), [selected]);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return members
      .filter((m) => !excluded.has(m.id))
      .filter((m) =>
        !q
          ? true
          : labelFor(m).toLowerCase().includes(q) ||
            (m.email ?? "").toLowerCase().includes(q),
      );
  }, [members, excluded, query]);

  function toggle(id: string) {
    if (selectedSet.has(id)) onChange(selected.filter((s) => s !== id));
    else onChange([...selected, id]);
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center rounded-xl border border-border py-8 text-muted-foreground">
        <Loader2 className="size-4 animate-spin" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-xl border border-dashed border-border px-4 py-6 text-center text-sm text-muted-foreground">
        {error}
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-border">
      <div className="flex items-center gap-2 border-b border-border px-3">
        <Search className="size-4 shrink-0 text-muted-foreground" />
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search teammates…"
          className="h-9 border-0 bg-transparent px-0 shadow-none focus-visible:ring-0"
        />
      </div>
      <div className="max-h-56 overflow-y-auto p-1">
        {visible.length === 0 ? (
          <div className="flex flex-col items-center gap-2 px-4 py-8 text-center text-sm text-muted-foreground">
            <Users className="size-5" />
            {members.filter((m) => !excluded.has(m.id)).length === 0
              ? "No other teammates to add yet."
              : "No teammates match that search."}
          </div>
        ) : (
          visible.map((m) => {
            const isSelected = selectedSet.has(m.id);
            return (
              <button
                key={m.id}
                type="button"
                onClick={() => toggle(m.id)}
                className={cn(
                  "flex w-full items-center gap-3 rounded-lg px-2.5 py-2 text-left transition-colors",
                  isSelected ? "bg-brand-tint" : "hover:bg-muted",
                )}
              >
                <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-muted font-mono text-[11px] font-bold text-muted-foreground">
                  {initials(m)}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-semibold text-foreground">
                    {labelFor(m)}
                  </span>
                  {m.email ? (
                    <span className="block truncate text-xs text-muted-foreground">
                      {m.email}
                    </span>
                  ) : null}
                </span>
                <span
                  className={cn(
                    "flex size-5 shrink-0 items-center justify-center rounded-full border transition-colors",
                    isSelected
                      ? "border-brand bg-brand text-white"
                      : "border-input",
                  )}
                >
                  {isSelected ? <Check className="size-3.5" /> : null}
                </span>
              </button>
            );
          })
        )}
      </div>
      {selected.length > 0 ? (
        <div className="border-t border-border px-3 py-2 font-mono text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
          {selected.length} selected
        </div>
      ) : null}
    </div>
  );
}
