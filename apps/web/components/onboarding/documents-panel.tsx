"use client";

import useSWR from "swr";
import { Building2, Download, UserRound } from "lucide-react";

import type { RunStep } from "@/components/onboarding/step-panel";
import { documentKindLabel } from "@/lib/onboarding-documents";

interface Submission {
  id: string;
  run_step_id: string;
  item_key: string;
  label: string;
  original_filename: string | null;
  file_bytes: number | null;
  submitted_at: string | null;
  review_status: "pending" | "approved" | "rejected";
  review_note: string | null;
  signed_url: string | null;
}

/** The bits of a run document this panel reads. Structural so the run page can
 *  pass its own row type without a cast. */
export interface ArchiveDocument {
  id: string;
  kind: string;
  signed_url: string | null;
  /** The countersigned PDF, and only that — null until someone has signed. */
  signed_pdf_url: string | null;
  sign_status: string;
  signed_pdf_path: string | null;
  signed_uploaded_at: string | null;
  esign_status: string | null;
  esign_completed_at: string | null;
}

interface FileRow {
  id: string;
  label: string;
  note: string | null;
  href: string | null;
  filename: string | null;
}

const fetcher = async (url: string): Promise<Submission[]> => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed to load (${res.status})`);
  return res.json();
};

function relativeTime(iso: string | null): string | null {
  if (!iso) return null;
  const min = Math.floor((Date.now() - new Date(iso).getTime()) / 60_000);
  if (min < 1) return "just now";
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const d = Math.floor(hr / 24);
  if (d < 30) return `${d}d ago`;
  return new Date(iso).toLocaleDateString();
}

/**
 * Supabase serves a signed URL inline unless asked otherwise, and the `download`
 * attribute is ignored cross-origin — so the filename has to travel as a query
 * param for the click to land a file on disk instead of a browser tab.
 */
function withDownloadName(url: string | null, name: string): string | null {
  if (!url) return null;
  const sep = url.includes("?") ? "&" : "?";
  return `${url}${sep}download=${encodeURIComponent(name)}`;
}

/**
 * The company document's final form, or null if it does not have one yet.
 *
 * "Final" mirrors the agent's own `_signatures_complete`: an e-sign envelope
 * that completed, a scan HR uploaded, or the candidate signing in-app. A step
 * the org configured with no signers at all never reaches any of those, so its
 * document counts as final once the step is done and the copy has gone out —
 * otherwise a pipeline that signs nothing would show an empty archive forever.
 */
function executedCopy(
  doc: ArchiveDocument,
  step: RunStep | undefined,
): { href: string | null; note: string } | null {
  if (doc.esign_status === "completed") {
    return {
      href: doc.signed_pdf_url ?? doc.signed_url,
      note: `Signed ${relativeTime(doc.esign_completed_at) ?? ""}`.trim(),
    };
  }
  if (doc.signed_pdf_path) {
    return {
      href: doc.signed_pdf_url ?? doc.signed_url,
      note: `Signed copy uploaded ${relativeTime(doc.signed_uploaded_at) ?? ""}`.trim(),
    };
  }
  if (doc.sign_status === "signed_by_candidate") {
    return { href: doc.signed_pdf_url ?? doc.signed_url, note: "Signed" };
  }
  if (step?.status === "done" && step.signer_roles.length === 0) {
    return { href: doc.signed_url, note: "Issued — no signature required" };
  }
  return null;
}

/**
 * Every finished file this run produced, in one place to download from.
 *
 * Two shelves, deliberately not one list: what the candidate filed and what the
 * company issued back. Both are restricted to things that are settled — a
 * submission on a step HR has accepted, a letter that has actually been signed.
 * Anything still in play belongs to the panel that can act on it, and listing
 * it here as well gave HR two contradictory places to look.
 */
export function DocumentsPanel({
  runId,
  steps,
  documents,
}: {
  runId: string;
  steps: RunStep[];
  documents: ArchiveDocument[];
}) {
  const { data, isLoading } = useSWR<Submission[]>(
    `/api/onboarding/runs/${runId}/submissions`,
    fetcher,
    { revalidateOnFocus: false },
  );

  const settled = new Set(
    steps.filter((s) => s.status === "done").map((s) => s.id),
  );
  const fromCandidate: FileRow[] = (data ?? [])
    .filter((s) => settled.has(s.run_step_id))
    .map((s) => ({
      id: s.id,
      label: s.label,
      note: s.submitted_at ? `Submitted ${relativeTime(s.submitted_at)}` : null,
      href: withDownloadName(s.signed_url, s.original_filename || s.label),
      filename: s.original_filename,
    }));

  const stepByKey = new Map(steps.map((s) => [s.step_key, s]));
  // Pipeline order, so the archive reads the way the run ran. A document whose
  // step is gone from the catalog sorts to the end rather than to the front.
  const positionOf = (kind: string) =>
    stepByKey.get(kind)?.position ?? Number.MAX_SAFE_INTEGER;
  const fromCompany: FileRow[] = [...documents]
    .sort((a, b) => positionOf(a.kind) - positionOf(b.kind))
    .flatMap((doc) => {
      const step = stepByKey.get(doc.kind);
      const final = executedCopy(doc, step);
      if (!final) return [];
      const label = step?.label ?? documentKindLabel(doc.kind);
      const filename = `${label}.pdf`;
      return [
        {
          id: doc.id,
          label,
          note: final.note,
          href: withDownloadName(final.href, filename),
          filename,
        },
      ];
    });

  if (isLoading && !fromCompany.length) {
    return <div className="h-24 animate-pulse rounded-xl bg-muted" />;
  }
  if (!fromCandidate.length && !fromCompany.length) return null;

  return (
    <section className="rounded-2xl border border-border bg-card p-5">
      <header className="mb-4">
        <h2 className="text-sm font-semibold text-foreground">Documents</h2>
      </header>

      <div className="space-y-5">
        <FileGroup
          icon={UserRound}
          title="From the candidate"
          rows={fromCandidate}
          loading={isLoading}
          empty="Nothing accepted from the candidate yet."
        />
        <FileGroup
          icon={Building2}
          title="From the company"
          rows={fromCompany}
          empty="No signed company documents yet."
        />
      </div>
    </section>
  );
}

function FileGroup({
  icon: Icon,
  title,
  rows,
  empty,
  loading = false,
}: {
  icon: React.ComponentType<{ className?: string }>;
  title: string;
  rows: FileRow[];
  empty: string;
  loading?: boolean;
}) {
  return (
    <div>
      <h3 className="mb-2 flex items-center gap-1.5 font-mono text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
        <Icon className="h-3.5 w-3.5 text-brand" />
        {title}
      </h3>

      {loading && rows.length === 0 ? (
        <div className="h-12 animate-pulse rounded-xl bg-muted" />
      ) : rows.length === 0 ? (
        <p className="rounded-xl border border-dashed border-border px-3 py-2.5 text-xs text-muted-foreground">
          {empty}
        </p>
      ) : (
        <div className="space-y-2">
          {rows.map((row) => (
            <div
              key={row.id}
              className="flex flex-wrap items-center gap-3 rounded-xl border border-border p-3"
            >
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium text-foreground">{row.label}</p>
                {row.note ? (
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    {row.note}
                  </p>
                ) : null}
              </div>

              {row.href ? (
                <a
                  href={row.href}
                  download={row.filename || undefined}
                  className="flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1.5 text-xs font-medium text-foreground transition-colors hover:border-brand hover:text-brand"
                >
                  <Download className="h-3.5 w-3.5" />
                  Download
                </a>
              ) : (
                <span className="text-xs text-muted-foreground">
                  Download unavailable
                </span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
