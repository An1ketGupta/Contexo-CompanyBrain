"use client";

import Link from "next/link";
import { Hash, Sparkles } from "lucide-react";

interface DocumentSummaryProps {
  summary: string;
  keyTopics?: string[];
  generatedAt?: string;
}

/** Auto-generated 2-3 sentence overview + 5 topic chips. Topics link into
 *  /chat with the topic as the seeded query so users can drill straight from
 *  the metadata to a scoped conversation. */
export function DocumentSummary({ summary, keyTopics }: DocumentSummaryProps) {
  return (
    <div className="space-y-3">
      <div className="flex items-start gap-2">
        <Sparkles className="mt-0.5 h-3.5 w-3.5 shrink-0 text-indigo-500" />
        <p className="text-xs leading-relaxed text-muted-foreground">{summary}</p>
      </div>
      {keyTopics && keyTopics.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {keyTopics.map((topic) => (
            <Link
              key={topic}
              href={`/chat?q=${encodeURIComponent(topic)}`}
              className="group inline-flex items-center gap-0.5 rounded-full border border-border bg-background px-2 py-0.5 text-[11px] text-muted-foreground transition-colors hover:border-indigo-300 hover:bg-indigo-50 hover:text-indigo-700 dark:hover:bg-indigo-950/40 dark:hover:text-indigo-300"
            >
              <Hash className="h-2.5 w-2.5 opacity-60 group-hover:opacity-100" />
              {topic}
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
