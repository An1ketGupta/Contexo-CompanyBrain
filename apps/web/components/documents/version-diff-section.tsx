"use client";

import useSWR from "swr";
import { Loader2, GitCompare } from "lucide-react";

interface DocumentDiff {
  id: string;
  from_version: number | null;
  to_version: number;
  diff_summary: string;
  created_at: string;
}

interface DiffsResponse {
  diffs: DocumentDiff[];
}

const fetcher = async (url: string): Promise<DiffsResponse> => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed to load diffs (${res.status})`);
  return res.json();
};

export function VersionDiffSection({ documentId }: { documentId: string }) {
  const { data, isLoading } = useSWR<DiffsResponse>(
    `/api/documents/${documentId}/diffs`,
    fetcher,
    { revalidateOnFocus: false },
  );

  if (isLoading) {
    return (
      <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <Loader2 className="h-3 w-3 animate-spin" />
        Checking for version changes…
      </div>
    );
  }

  const diffs = data?.diffs ?? [];
  if (diffs.length === 0) {
    return null;
  }

  const latest = diffs[0];

  return (
    <section className="space-y-2 rounded-xl border border-border bg-background p-3">
      <header className="flex items-center gap-2 text-xs font-semibold">
        <GitCompare className="h-3.5 w-3.5 text-muted-foreground" />
        <span>
          What changed in v{latest.to_version}
          {latest.from_version != null ? ` since v${latest.from_version}` : ""}
        </span>
      </header>
      <ul className="space-y-1 text-xs leading-relaxed text-foreground">
        {parseBullets(latest.diff_summary).map((bullet, i) => (
          <li key={i} className="flex gap-1.5">
            <span className="text-muted-foreground">•</span>
            <span>{bullet}</span>
          </li>
        ))}
      </ul>
      {diffs.length > 1 ? (
        <details className="text-xs">
          <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
            Older changes ({diffs.length - 1})
          </summary>
          <div className="mt-2 space-y-2 border-t border-border pt-2">
            {diffs.slice(1).map((d) => (
              <div key={d.id} className="space-y-1">
                <p className="font-medium text-muted-foreground">
                  v{d.to_version}
                  {d.from_version != null ? ` vs v${d.from_version}` : ""}
                </p>
                <ul className="space-y-1">
                  {parseBullets(d.diff_summary).map((b, i) => (
                    <li key={i} className="flex gap-1.5">
                      <span className="text-muted-foreground">•</span>
                      <span>{b}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </details>
      ) : null}
    </section>
  );
}

function parseBullets(raw: string): string[] {
  // The LLM is prompted to emit "• ...", "- ...", or "1. ..." style bullets.
  // Normalise into a flat list of trimmed strings; fall back to a single
  // paragraph if no bullet markers are present.
  const lines = raw
    .split(/\r?\n/)
    .map((l) => l.replace(/^[\s•\-*\d.)]+/, "").trim())
    .filter(Boolean);
  return lines.length > 0 ? lines : [raw.trim()];
}
