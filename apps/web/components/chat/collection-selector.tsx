"use client";

import Link from "next/link";
import { FolderOpen, Settings as SettingsIcon } from "lucide-react";
import { useCollections } from "@/hooks/use-collections";
import { cn } from "@/lib/utils";

interface Props {
  value: string | null;
  onChange: (id: string | null) => void;
}

/**
 * V5 #35 — Collection chip row, shown above the new-chat surface when the
 * org has at least one collection. Picking a chip becomes the conversation's
 * scope on first send (locked at creation, same semantics as scopedTags).
 *
 * If the org has no collections we render nothing; the existing tag selector
 * (`ChatScopeSelector`) takes over as a per-tag fallback so brand-new orgs
 * can still narrow searches without first standing up a collection.
 */
export function CollectionSelector({ value, onChange }: Props) {
  const { collections, loading } = useCollections();
  if (loading) return null;
  if (!collections.length) return null;

  return (
    <div className="mx-4 mt-3 flex flex-wrap items-center gap-1.5 rounded-lg border border-dashed border-border bg-muted/30 px-3 py-2 md:mx-6">
      <span className="mr-1 inline-flex items-center gap-1 text-xs text-muted-foreground">
        <FolderOpen className="h-3 w-3" />
        Collection:
      </span>

      <button
        type="button"
        onClick={() => onChange(null)}
        className={cn(
          "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs transition-colors",
          value === null
            ? "border-foreground bg-foreground text-background"
            : "border-border bg-background text-muted-foreground hover:border-foreground/40 hover:text-foreground",
        )}
      >
        All
      </button>

      {collections.map((col) => {
        const active = col.id === value;
        const style = active
          ? { backgroundColor: col.color, borderColor: col.color }
          : undefined;
        return (
          <button
            key={col.id}
            type="button"
            onClick={() => onChange(active ? null : col.id)}
            className={cn(
              "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs transition-colors",
              active
                ? "text-white"
                : "border-border bg-background text-muted-foreground hover:border-foreground/40 hover:text-foreground",
            )}
            style={style}
            title={`${col.document_count} document${col.document_count === 1 ? "" : "s"}`}
          >
            {col.icon && <span className="text-[11px] leading-none">{col.icon}</span>}
            {col.name}
            <span
              className={cn(
                "text-[10px] tabular-nums",
                active ? "opacity-90" : "opacity-60",
              )}
            >
              {col.document_count}
            </span>
          </button>
        );
      })}

      <Link
        href="/settings/collections"
        className="ml-auto inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground"
      >
        <SettingsIcon className="h-3 w-3" />
        Manage
      </Link>
    </div>
  );
}
