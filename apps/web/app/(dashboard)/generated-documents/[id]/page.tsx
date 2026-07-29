"use client";

import Link from "next/link";
import { use, useState } from "react";
import {
  AlertTriangle,
  ArrowLeft,
  Check,
  CheckCircle2,
  Download,
  FileText,
  Loader2,
  X,
} from "lucide-react";

import { PageHeader, StatusPill, type PillTone } from "@/components/actual/kit";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useGeneratedDocument } from "@/hooks/use-generated-documents";
import type {
  GeneratedDocument,
  GeneratedDocumentStatus,
  ValidationReport,
} from "@/lib/types";

const STATUS_TONE: Record<GeneratedDocumentStatus, PillTone> = {
  pending: "gray",
  generating: "blue",
  generated: "blue",
  validation_failed: "amber",
  generation_failed: "red",
  approved: "green",
  rejected: "red",
  sending: "blue",
  sent: "green",
  send_failed: "red",
};

const STATUS_LABEL: Record<GeneratedDocumentStatus, string> = {
  pending: "Pending",
  generating: "Generating",
  generated: "Ready for review",
  validation_failed: "Missing information",
  generation_failed: "Generation failed",
  approved: "Approved",
  rejected: "Rejected",
  sending: "Sending",
  sent: "Sent",
  send_failed: "Send failed",
};

export default function GeneratedDocumentPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const { document, isLoading, approve, reject } = useGeneratedDocument(id);

  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");

  async function run(key: string, fn: () => Promise<unknown>) {
    setBusy(key);
    setError(null);
    try {
      await fn();
      setRejecting(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setBusy(null);
    }
  }

  if (isLoading) {
    return (
      <div className="mx-auto flex max-w-4xl items-center gap-2 p-6 text-sm text-muted-foreground md:p-8">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading document…
      </div>
    );
  }

  if (!document) {
    return (
      <div className="mx-auto max-w-4xl p-6 md:p-8">
        <div className="rounded-xl border border-dashed px-6 py-12 text-center">
          <p className="font-semibold">Document not found</p>
        </div>
      </div>
    );
  }

  const report = document.validation_report as ValidationReport;
  const decided = ["approved", "rejected"].includes(document.status);
  const reviewable = document.status === "generated";

  return (
    <div className="mx-auto max-w-4xl space-y-8 p-6 md:p-8">
      <Button asChild variant="ghost" size="sm" className="-ml-2">
        <Link href="/generated-documents">
          <ArrowLeft className="mr-2 h-4 w-4" />
          All generated documents
        </Link>
      </Button>

      <PageHeader
        eyebrow={document.template_name ?? "Document"}
        title={candidateName(document) ?? "Generated document"}
        description={
          document.generation_no > 1
            ? `Generation ${document.generation_no}. Earlier generations are kept and remain downloadable.`
            : "Check the details below, then approve or reject."
        }
        actions={
          <StatusPill tone={STATUS_TONE[document.status]}>
            {STATUS_LABEL[document.status]}
          </StatusPill>
        }
      />

      {error ? <Banner tone="error">{error}</Banner> : null}
      {document.error_message ? (
        <Banner tone="error">{document.error_message}</Banner>
      ) : null}

      {report?.errors?.length ? (
        <section className="space-y-2 rounded-xl border border-destructive/30 bg-destructive-soft p-4">
          <p className="text-sm font-bold text-destructive">
            This document was not produced — {report.errors.length} problem
            {report.errors.length === 1 ? "" : "s"} with the information
          </p>
          <ul className="space-y-1 text-sm text-destructive">
            {report.errors.map((issue, i) => (
              <li key={`${issue.code}-${i}`}>· {issue.message}</li>
            ))}
          </ul>
          <p className="pt-1 text-xs text-destructive/80">
            Fix these on the candidate&rsquo;s record, then generate again.
          </p>
        </section>
      ) : null}

      {report?.warnings?.length ? (
        <section className="space-y-2 rounded-xl bg-amber-tint p-4">
          <p className="text-sm font-bold text-amber">Worth checking</p>
          <ul className="space-y-1 text-sm text-amber">
            {report.warnings.map((issue, i) => (
              <li key={`${issue.code}-${i}`}>· {issue.message}</li>
            ))}
          </ul>
        </section>
      ) : null}

      {document.files.length > 0 ? (
        <section className="space-y-3">
          <h2 className="text-sm font-bold uppercase tracking-wide text-muted-foreground">
            Document
          </h2>
          <div className="flex flex-wrap gap-2">
            {document.files.map((file) => (
              <Button
                key={file.format}
                asChild
                variant={file.format === "pdf" ? "secondary" : "ghost"}
                size="sm"
                disabled={!file.url}
              >
                <a href={file.url ?? "#"} target="_blank" rel="noopener noreferrer">
                  {file.format === "pdf" ? (
                    <FileText className="mr-2 h-4 w-4" />
                  ) : (
                    <Download className="mr-2 h-4 w-4" />
                  )}
                  {file.format === "pdf" ? "Open PDF" : "Download Word"}
                </a>
              </Button>
            ))}
          </div>
          {!document.files.some((f) => f.format === "pdf") ? (
            <p className="text-xs text-amber">
              PDF conversion didn&rsquo;t complete. The Word document is
              complete and is what would be sent.
            </p>
          ) : null}
        </section>
      ) : null}

      <FilledValues document={document} />

      {reviewable ? (
        <section className="space-y-3 border-t pt-6">
          {rejecting ? (
            <div className="space-y-3">
              <Textarea
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="Why is this being rejected? (optional, but useful later)"
                rows={2}
              />
              <div className="flex gap-2">
                <Button
                  variant="destructive"
                  onClick={() => run("reject", () => reject(reason || undefined))}
                  disabled={busy !== null}
                >
                  {busy === "reject" ? (
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  ) : (
                    <X className="mr-2 h-4 w-4" />
                  )}
                  Confirm rejection
                </Button>
                <Button variant="ghost" onClick={() => setRejecting(false)}>
                  Cancel
                </Button>
              </div>
            </div>
          ) : (
            <div className="flex flex-wrap gap-2">
              <Button
                onClick={() => run("approve", approve)}
                disabled={busy !== null}
              >
                {busy === "approve" ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  <Check className="mr-2 h-4 w-4" />
                )}
                Approve
              </Button>
              <Button variant="ghost" onClick={() => setRejecting(true)}>
                Reject
              </Button>
            </div>
          )}
        </section>
      ) : decided ? (
        <div className="flex items-center gap-2 border-t pt-6 text-sm text-muted-foreground">
          {document.status === "approved" ? (
            <>
              <CheckCircle2 className="h-4 w-4 text-success" />
              Approved{document.approved_at ? ` on ${fmt(document.approved_at)}` : ""}.
            </>
          ) : (
            <>
              <X className="h-4 w-4 text-destructive" />
              Rejected{document.rejected_at ? ` on ${fmt(document.rejected_at)}` : ""}
              {document.rejection_reason ? ` — ${document.rejection_reason}` : ""}
            </>
          )}
        </div>
      ) : null}
    </div>
  );
}

/**
 * What went into the document, and where each value came from.
 *
 * The provenance column is the point: "why does this say Bengaluru?" should be
 * answerable without asking anyone.
 */
function FilledValues({ document }: { document: GeneratedDocument }) {
  const values = document.context_snapshot?.values ?? {};
  const sources = document.context_snapshot?.sources ?? {};
  const entries = Object.entries(values);

  if (entries.length === 0) return null;

  return (
    <section className="space-y-3">
      <h2 className="text-sm font-bold uppercase tracking-wide text-muted-foreground">
        Filled values
      </h2>
      <div className="overflow-x-auto rounded-xl border">
        <table className="w-full text-sm">
          <thead className="bg-muted/50 text-left text-xs uppercase tracking-wide text-muted-foreground">
            <tr>
              <th className="px-4 py-2 font-semibold">Field</th>
              <th className="px-4 py-2 font-semibold">Value</th>
              <th className="px-4 py-2 font-semibold">Taken from</th>
            </tr>
          </thead>
          <tbody>
            {entries.map(([name, value]) => (
              <tr key={name} className="border-t">
                <td className="px-4 py-2 font-medium">{name}</td>
                <td className="px-4 py-2">{renderValue(value)}</td>
                <td className="px-4 py-2 text-xs text-muted-foreground">
                  {sources[name] ?? "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function renderValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  return String(value);
}

function candidateName(document: GeneratedDocument): string | null {
  const candidate = document.candidate_snapshot?.candidate;
  const name = candidate?.full_name;
  return typeof name === "string" && name ? name : null;
}

function fmt(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function Banner({
  tone,
  children,
}: {
  tone: "error" | "warn";
  children: React.ReactNode;
}) {
  const styles =
    tone === "error"
      ? "bg-destructive-soft text-destructive"
      : "bg-amber-tint text-amber";
  return (
    <div className={`flex items-start gap-2 rounded-lg px-4 py-3 text-sm ${styles}`}>
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
      <span>{children}</span>
    </div>
  );
}
