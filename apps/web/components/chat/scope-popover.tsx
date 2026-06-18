"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import Link from "next/link";
import {
  Check,
  ChevronDown,
  FolderOpen,
  Search,
  Settings as SettingsIcon,
  Tag,
  X,
} from "lucide-react";
import { Input } from "@/components/ui/input";
import { useCollections } from "@/hooks/use-collections";
import { useDocumentTags } from "@/hooks/use-documents";
import { cn } from "@/lib/utils";

export interface ScopeValue {
  collectionId: string | null;
  tags: string[];
}

interface Props {
  value: ScopeValue;
  onChange: (next: ScopeValue) => void;
}

/**
 * Unified scope picker for the new-chat surface.
 *
 * Replaces the standalone CollectionSelector + ChatScopeSelector chip rows
 * with a single collapsed pill that opens a popover. Collections are
 * single-select; tags are multi-select. Picking either side clears the
 * other — backend hard-codes collection > tags priority in task_chain.py,
 * so showing both as simultaneously active would lie about what runs.
 *
 * Self-hides when the org has neither collections nor tags. After the
 * conversation persists, the read-only ScopeBanner on /chat/[id] takes
 * over; scope locks at first send.
 *
 * No Radix Popover dep — mirrors the hand-rolled outside-click pattern
 * already used by template-popover.tsx.
 */
export function ScopePopover({ value, onChange }: Props) {
  const { tags } = useDocumentTags();
  const { collections, loading: loadingCollections } = useCollections();
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const panelRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const node = panelRef.current;
      const trig = triggerRef.current;
      if (!node || node.contains(e.target as Node)) return;
      if (trig && trig.contains(e.target as Node)) return;
      setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  // Reset filter when the popover closes — opening it again should start
  // clean, not preserve a stale query.
  useEffect(() => {
    if (!open) setSearch("");
  }, [open]);

  const activeCollection = useMemo(
    () => collections.find((c) => c.id === value.collectionId) ?? null,
    [collections, value.collectionId],
  );

  const summary = useMemo(() => {
    if (activeCollection) return activeCollection.name;
    if (value.tags.length === 0) return "All documents";
    if (value.tags.length === 1) return value.tags[0];
    return `${value.tags[0]} + ${value.tags.length - 1} more`;
  }, [activeCollection, value.tags]);

  const hasScope = !!activeCollection || value.tags.length > 0;

  const filteredCollections = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return collections;
    return collections.filter((c) => c.name.toLowerCase().includes(q));
  }, [collections, search]);

  const filteredTags = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return tags;
    return tags.filter((t) => t.tag.toLowerCase().includes(q));
  }, [tags, search]);

  const pickCollection = useCallback(
    (id: string | null) => {
      onChange({ collectionId: id, tags: id ? [] : value.tags });
    },
    [onChange, value.tags],
  );

  const toggleTag = useCallback(
    (tag: string) => {
      const next = value.tags.includes(tag)
        ? value.tags.filter((t) => t !== tag)
        : [...value.tags, tag];
      onChange({
        collectionId: next.length > 0 ? null : value.collectionId,
        tags: next,
      });
    },
    [onChange, value.collectionId, value.tags],
  );

  const clear = useCallback(() => {
    onChange({ collectionId: null, tags: [] });
  }, [onChange]);

  if (!loadingCollections && collections.length === 0 && tags.length === 0) {
    return null;
  }

  return (
    <div className="relative mx-4 mt-3 md:mx-6">
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-haspopup="dialog"
        className={cn(
          "inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs transition-colors",
          hasScope
            ? "border-primary/40 bg-primary/5 text-foreground"
            : "border-dashed border-border bg-muted/30 text-muted-foreground hover:border-primary/40 hover:text-foreground",
        )}
      >
        <Search className="h-3 w-3" />
        <span className="font-medium">Search in:</span>
        <span
          className={cn(
            "max-w-[14rem] truncate",
            hasScope ? "text-foreground" : "text-muted-foreground",
          )}
        >
          {summary}
        </span>
        {hasScope && (
          <span
            role="button"
            tabIndex={0}
            aria-label="Clear scope"
            onClick={(e) => {
              e.stopPropagation();
              clear();
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                e.stopPropagation();
                clear();
              }
            }}
            className="ml-1 inline-flex h-3.5 w-3.5 cursor-pointer items-center justify-center rounded-full text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            <X className="h-3 w-3" />
          </span>
        )}
        <ChevronDown
          className={cn(
            "h-3 w-3 transition-transform",
            open && "rotate-180",
          )}
        />
      </button>

      {open && (
        <div
          ref={panelRef}
          role="dialog"
          aria-label="Choose what to search"
          className="absolute left-0 top-full z-30 mt-2 w-[min(20rem,calc(100vw-2rem))] overflow-hidden rounded-xl border border-border bg-background shadow-lg"
        >
          <div className="border-b border-border p-2.5">
            <div className="relative">
              <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Filter tags & collections…"
                className="h-8 pl-7 text-sm"
                autoFocus
              />
            </div>
          </div>

          <div className="max-h-80 overflow-y-auto">
            <button
              type="button"
              onClick={() => pickCollection(null)}
              className="flex w-full items-center gap-2 px-3 py-2 text-sm transition-colors hover:bg-muted/60"
            >
              <span
                className={cn(
                  "flex h-4 w-4 shrink-0 items-center justify-center rounded-full border",
                  !hasScope ? "border-primary bg-primary" : "border-border",
                )}
              >
                {!hasScope && (
                  <Check className="h-2.5 w-2.5 text-primary-foreground" />
                )}
              </span>
              <span className="font-medium">All documents</span>
            </button>

            {filteredCollections.length > 0 && (
              <div className="border-t border-border/60">
                <p className="px-3 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                  Collections
                </p>
                {filteredCollections.map((col) => {
                  const active = col.id === value.collectionId;
                  return (
                    <button
                      key={col.id}
                      type="button"
                      onClick={() => pickCollection(active ? null : col.id)}
                      className="flex w-full items-center gap-2 px-3 py-1.5 text-sm transition-colors hover:bg-muted/60"
                    >
                      <span
                        className={cn(
                          "flex h-4 w-4 shrink-0 items-center justify-center rounded-full border",
                          active
                            ? "border-primary bg-primary"
                            : "border-border",
                        )}
                      >
                        {active && (
                          <Check className="h-2.5 w-2.5 text-primary-foreground" />
                        )}
                      </span>
                      {col.icon ? (
                        <span className="text-xs leading-none">{col.icon}</span>
                      ) : (
                        <FolderOpen className="h-3.5 w-3.5 text-muted-foreground" />
                      )}
                      <span className="min-w-0 flex-1 truncate text-left">
                        {col.name}
                      </span>
                      <span className="shrink-0 text-[10px] tabular-nums text-muted-foreground">
                        {col.document_count}
                      </span>
                    </button>
                  );
                })}
              </div>
            )}

            {filteredTags.length > 0 && (
              <div className="border-t border-border/60">
                <p className="px-3 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                  Tags
                </p>
                {filteredTags.map((t) => {
                  const active = value.tags.includes(t.tag);
                  return (
                    <button
                      key={t.tag}
                      type="button"
                      onClick={() => toggleTag(t.tag)}
                      className="flex w-full items-center gap-2 px-3 py-1.5 text-sm transition-colors hover:bg-muted/60"
                    >
                      <span
                        className={cn(
                          "flex h-4 w-4 shrink-0 items-center justify-center rounded border",
                          active
                            ? "border-primary bg-primary"
                            : "border-border",
                        )}
                      >
                        {active && (
                          <Check className="h-2.5 w-2.5 text-primary-foreground" />
                        )}
                      </span>
                      <Tag className="h-3 w-3 shrink-0 text-muted-foreground" />
                      <span className="min-w-0 flex-1 truncate text-left">
                        {t.tag}
                      </span>
                      <span className="shrink-0 text-[10px] tabular-nums text-muted-foreground">
                        {t.count}
                      </span>
                    </button>
                  );
                })}
              </div>
            )}

            {filteredCollections.length === 0 &&
              filteredTags.length === 0 && (
                <p className="p-6 text-center text-xs text-muted-foreground">
                  {search ? "No matches." : "No collections or tags yet."}
                </p>
              )}
          </div>

          <div className="flex items-center justify-between border-t border-border bg-muted/30 px-3 py-2">
            <button
              type="button"
              onClick={clear}
              disabled={!hasScope}
              className="text-[11px] text-muted-foreground transition-colors hover:text-foreground disabled:opacity-40"
            >
              Clear
            </button>
            <Link
              href="/settings/collections"
              className="inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground"
              onClick={() => setOpen(false)}
            >
              <SettingsIcon className="h-3 w-3" />
              Manage
            </Link>
          </div>
        </div>
      )}
    </div>
  );
}
