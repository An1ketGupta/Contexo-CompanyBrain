"use client";

import { BookOpen } from "lucide-react";

export interface TocEntry {
  level: number;
  title: string;
  page: number | null;
}

interface TableOfContentsProps {
  entries: TocEntry[];
}

/** Read-only TOC rendered in the expanded document row.
 *
 *  For PDFs we surface page numbers — clicking a row could in future open the
 *  PDF at that page (we keep it as plain text for v1 because we don't yet
 *  expose a signed URL viewer). For DOCX, page is null and we just render
 *  the heading hierarchy. */
export function TableOfContents({ entries }: TableOfContentsProps) {
  if (!entries?.length) return null;
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
        <BookOpen className="h-3 w-3" />
        Contents
        <span className="font-normal normal-case tracking-normal text-muted-foreground/60">
          · {entries.length}
        </span>
      </div>
      <ul className="space-y-0.5">
        {entries.map((entry, i) => (
          <li
            key={`${entry.level}-${entry.title}-${i}`}
            className="flex items-baseline justify-between gap-3 text-xs"
            style={{ paddingLeft: `${(entry.level - 1) * 12}px` }}
          >
            <span
              className={
                entry.level === 1
                  ? "truncate font-medium text-foreground"
                  : "truncate text-muted-foreground"
              }
              title={entry.title}
            >
              {entry.title}
            </span>
            {entry.page != null && (
              <span className="shrink-0 font-mono text-[10px] text-muted-foreground/60">
                p.{entry.page}
              </span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
