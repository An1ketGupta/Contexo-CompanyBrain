"use client";

import Link from "next/link";
import { AlertTriangle, FileText, Loader2 } from "lucide-react";

import { PageHeader, StatusPill, type PillTone } from "@/components/actual/kit";
import { useGeneratedDocuments } from "@/hooks/use-generated-documents";
import type { GeneratedDocument, GeneratedDocumentStatus } from "@/lib/types";

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
  generated: "Needs review",
  validation_failed: "Missing information",
  generation_failed: "Failed",
  approved: "Approved",
  rejected: "Rejected",
  sending: "Sending",
  sent: "Sent",
  send_failed: "Send failed",
};

export default function GeneratedDocumentsPage() {
  const { documents, isLoading, error } = useGeneratedDocuments();

  const needsReview = documents.filter((d) => d.status === "generated");

  return (
    <div className="mx-auto max-w-5xl space-y-8 p-6 md:p-8">
      <PageHeader
        eyebrow="Documents"
        title="Generated documents"
        description="Every document produced from a template, with the data that went into it. Regenerating never replaces an earlier one."
      />

      {error ? (
        <div className="flex items-start gap-2 rounded-lg bg-destructive-soft px-4 py-3 text-sm text-destructive">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>Couldn&rsquo;t load documents.</span>
        </div>
      ) : null}

      {needsReview.length > 0 ? (
        <p className="text-sm text-muted-foreground">
          {needsReview.length} document{needsReview.length === 1 ? "" : "s"}{" "}
          waiting for review.
        </p>
      ) : null}

      {isLoading ? (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading…
        </div>
      ) : documents.length === 0 ? (
        <div className="rounded-xl border border-dashed px-6 py-12 text-center">
          <FileText className="mx-auto h-8 w-8 text-muted-foreground" />
          <h3 className="mt-4 font-semibold">Nothing generated yet</h3>
          <p className="mx-auto mt-2 max-w-md text-sm text-muted-foreground">
            Documents appear here once an onboarding run produces one, or when
            you generate one from a template.
          </p>
        </div>
      ) : (
        <ul className="space-y-2">
          {documents.map((doc) => (
            <li key={doc.id}>
              <Link
                href={`/generated-documents/${doc.id}`}
                className="flex items-center justify-between gap-4 rounded-xl border bg-card p-4 transition-colors hover:bg-muted/40"
              >
                <div className="min-w-0">
                  <p className="truncate font-semibold">
                    {candidateName(doc) ?? "Unknown candidate"}
                  </p>
                  <p className="truncate text-xs text-muted-foreground">
                    {doc.template_name ?? "Template"}
                    {doc.generation_no > 1 ? ` · generation ${doc.generation_no}` : ""}
                    {doc.created_at ? ` · ${fmt(doc.created_at)}` : ""}
                  </p>
                </div>
                <StatusPill tone={STATUS_TONE[doc.status]}>
                  {STATUS_LABEL[doc.status]}
                </StatusPill>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function candidateName(doc: GeneratedDocument): string | null {
  const name = doc.candidate_snapshot?.candidate?.full_name;
  return typeof name === "string" && name ? name : null;
}

function fmt(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString();
  } catch {
    return iso;
  }
}
