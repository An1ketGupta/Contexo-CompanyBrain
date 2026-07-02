"use client";

import { useMemo, useState } from "react";
import { ChevronRight, Search } from "lucide-react";
import { cn } from "@/lib/utils";

export interface TocEntry {
  level: number;
  title: string;
  page: number | null;
}

interface TableOfContentsProps {
  entries: TocEntry[];
}

interface Chapter {
  head: TocEntry;
  children: TocEntry[];
  index: number;
}

/**
 * Document table of contents with search + collapsible chapters. Level-1
 * entries are chapter heads; everything until the next level-1 belongs to
 * the previous chapter.
 */
export function TableOfContents({ entries }: TableOfContentsProps) {
  const [query, setQuery] = useState("");
  const [collapsed, setCollapsed] = useState<Set<number>>(new Set());

  const chapters = useMemo(() => groupIntoChapters(entries), [entries]);
  const filtered = useMemo(() => filterChapters(chapters, query), [chapters, query]);
  const hasResults = filtered.length > 0;

  if (!entries?.length) return null;

  const toggle = (idx: number) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(idx)) next.delete(idx);
      else next.add(idx);
      return next;
    });
  };

  return (
    <div className="space-y-3">
      <div className="sticky top-0 z-10 -mx-2 -mt-2 bg-background px-2 pb-2 pt-2">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground/60" />
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search sections…"
            className="h-8 w-full rounded-[10px] border border-input bg-background pl-8 pr-3 text-xs text-foreground shadow-sm transition-colors placeholder:text-muted-foreground/60 focus-visible:border-ring focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/25"
          />
        </div>
      </div>

      {!hasResults ? (
        <p className="px-2 py-4 text-center text-xs text-muted-foreground">
          No sections match &ldquo;{query}&rdquo;.
        </p>
      ) : (
        <ul className="space-y-0.5">
          {filtered.map((chapter) => {
            const isCollapsed = !query && collapsed.has(chapter.index);
            const childCount = chapter.children.length;
            return (
              <li key={chapter.index}>
                <button
                  type="button"
                  onClick={() => toggle(chapter.index)}
                  disabled={childCount === 0}
                  className={cn(
                    "group flex w-full items-baseline gap-2 rounded px-1.5 py-1 text-left text-sm transition-colors",
                    childCount > 0
                      ? "hover:bg-muted"
                      : "cursor-default",
                  )}
                >
                  <ChevronRight
                    aria-hidden
                    className={cn(
                      "h-3 w-3 shrink-0 self-center text-muted-foreground/60 transition-transform",
                      childCount === 0 && "opacity-0",
                      !isCollapsed && childCount > 0 && "rotate-90",
                    )}
                  />
                  <span className="min-w-0 flex-1 truncate font-medium text-foreground">
                    {chapter.head.title}
                  </span>
                  {chapter.head.page != null && (
                    <span className="shrink-0 font-mono text-[10px] tabular-nums text-muted-foreground/60">
                      p.{chapter.head.page}
                    </span>
                  )}
                </button>
                {!isCollapsed && childCount > 0 && (
                  <ul className="ml-5 mt-0.5 space-y-0.5 border-l border-border/60 pl-3">
                    {chapter.children.map((entry, i) => (
                      <li
                        key={`${chapter.index}-${i}`}
                        className="flex items-baseline justify-between gap-3 py-0.5 text-xs"
                        style={{ paddingLeft: `${Math.max(entry.level - 2, 0) * 12}px` }}
                      >
                        <span
                          className="truncate text-muted-foreground"
                          title={entry.title}
                        >
                          {entry.title}
                        </span>
                        {entry.page != null && (
                          <span className="shrink-0 font-mono text-[10px] tabular-nums text-muted-foreground/60">
                            p.{entry.page}
                          </span>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function groupIntoChapters(entries: TocEntry[]): Chapter[] {
  const chapters: Chapter[] = [];
  for (let i = 0; i < entries.length; i++) {
    const entry = entries[i];
    if (entry.level === 1 || chapters.length === 0) {
      chapters.push({ head: entry, children: [], index: chapters.length });
    } else {
      chapters[chapters.length - 1].children.push(entry);
    }
  }
  return chapters;
}

function filterChapters(chapters: Chapter[], query: string): Chapter[] {
  const q = query.trim().toLowerCase();
  if (!q) return chapters;
  return chapters
    .map((chapter) => {
      const headMatch = chapter.head.title.toLowerCase().includes(q);
      const matchedChildren = chapter.children.filter((c) =>
        c.title.toLowerCase().includes(q),
      );
      if (headMatch) {
        return { ...chapter, children: chapter.children };
      }
      if (matchedChildren.length > 0) {
        return { ...chapter, children: matchedChildren };
      }
      return null;
    })
    .filter((c): c is Chapter => c !== null);
}
