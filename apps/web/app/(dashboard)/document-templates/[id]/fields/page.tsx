"use client";

import Link from "next/link";
import { use, useMemo, useState } from "react";
import {
  AlertTriangle,
  ArrowLeft,
  Download,
  Eye,
  Loader2,
  MapPin,
  Plus,
  Sparkles,
} from "lucide-react";

import { PageHeader, StatusPill } from "@/components/actual/kit";
import { AddFieldDialog } from "@/components/document-templates/add-field-dialog";
import { FieldRow } from "@/components/document-templates/field-row";
import { PlaceFieldDialog } from "@/components/document-templates/place-field-dialog";
import { UnmappedPositions } from "@/components/document-templates/unmapped-positions";
import { Button } from "@/components/ui/button";
import {
  useDocumentTemplate,
  useTemplateSchema,
  useTemplateVersions,
} from "@/hooks/use-document-templates";
import type { DocPreviewResult } from "@/lib/types";

export default function TemplateFieldsPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);

  const { template } = useDocumentTemplate(id);
  const { versions } = useTemplateVersions(id);

  const versionId = template?.current_version_id ?? versions[0]?.id ?? null;
  const {
    schema,
    isLoading,
    analyze,
    updateVariable,
    createVariable,
    deleteVariable,
    updateSlot,
    createSlot,
    preview,
    downloadOriginal,
  } = useTemplateSchema(versionId);

  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [previewResult, setPreviewResult] = useState<DocPreviewResult | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [placeOpen, setPlaceOpen] = useState(false);

  const variables = schema?.variables ?? [];
  const slots = schema?.slots ?? [];
  const threshold = schema?.confirm_threshold ?? 0.85;

  const slotsByVariable = useMemo(() => {
    const map = new Map<string, typeof slots>();
    for (const slot of slots) {
      if (!slot.variable_id) continue;
      const bucket = map.get(slot.variable_id);
      if (bucket) bucket.push(slot);
      else map.set(slot.variable_id, [slot]);
    }
    return map;
  }, [slots]);

  const pending = variables.filter((v) => v.status === "proposed");
  const confirmed = variables.filter((v) => v.status === "confirmed");
  const rejected = variables.filter((v) => v.status === "rejected");
  const unmapped = slots.filter((s) => !s.variable_id && s.status !== "rejected");

  // Fill-points that currently hold a real person's details. Rejecting one
  // leaves that person's data in a document sent to somebody else, so it is
  // worth saying out loud rather than burying in a row.
  const occupiedCount = slots.filter(
    (s) => s.original_text.trim().length > 0 && s.status !== "rejected",
  ).length;

  async function run(key: string, fn: () => Promise<unknown>) {
    setBusy(key);
    setError(null);
    setNotice(null);
    try {
      await fn();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setBusy(null);
    }
  }

  async function runAnalyze() {
    await run("analyze", async () => {
      const result = await analyze();
      if (result.status === "failed") {
        setError(
          result.error ??
            "Analysis didn't complete. You can still add fields by hand.",
        );
        return;
      }
      setNotice(
        `Found ${result.candidates_found} field position${
          result.candidates_found === 1 ? "" : "s"
        }. ${result.variables_created} new field${
          result.variables_created === 1 ? "" : "s"
        } proposed.`,
      );
    });
  }

  async function runPreview() {
    await run("preview", async () => {
      const result = await preview();
      setPreviewResult(result);
    });
  }

  async function runDownload() {
    await run("download", async () => {
      const url = await downloadOriginal();
      window.open(url, "_blank", "noopener,noreferrer");
    });
  }

  return (
    <div className="mx-auto max-w-5xl space-y-8 p-6 md:p-8">
      <Button asChild variant="ghost" size="sm" className="-ml-2">
        <Link href="/document-templates">
          <ArrowLeft className="mr-2 h-4 w-4" />
          All templates
        </Link>
      </Button>

      <PageHeader
        eyebrow={template?.document_type_label ?? "Template"}
        title={template?.name ?? "Review fields"}
        description="Confirm which details change for each person. Everything else in your document stays exactly as you wrote it."
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={runDownload}
              disabled={busy !== null}
            >
              <Download className="mr-2 h-4 w-4" />
              Original
            </Button>
            <Button
              variant="secondary"
              size="sm"
              onClick={runPreview}
              disabled={busy !== null || confirmed.length === 0}
            >
              {busy === "preview" ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <Eye className="mr-2 h-4 w-4" />
              )}
              Preview
            </Button>
            <Button onClick={runAnalyze} disabled={busy !== null}>
              {busy === "analyze" ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <Sparkles className="mr-2 h-4 w-4" />
              )}
              {variables.length ? "Re-analyze" : "Find fields"}
            </Button>
          </div>
        }
      />

      {error ? <Banner tone="error">{error}</Banner> : null}
      {notice ? <Banner tone="info">{notice}</Banner> : null}

      {previewResult ? (
        <PreviewPanel
          result={previewResult}
          onDismiss={() => setPreviewResult(null)}
        />
      ) : null}

      {occupiedCount > 0 ? (
        <Banner tone="warn">
          {occupiedCount} field{occupiedCount === 1 ? "" : "s"}{" "}
          in this document still contain a real person&rsquo;s details from the
          sample. Confirming
          a field means it gets replaced for each new person; rejecting it means
          the sample value stays in every document you send.
        </Banner>
      ) : null}

      {isLoading ? (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading fields…
        </div>
      ) : variables.length === 0 && slots.length === 0 ? (
        <EmptyFields analyzing={busy === "analyze"} onAnalyze={runAnalyze} />
      ) : (
        <div className="space-y-8">
          <FieldSection
            title="Needs your review"
            count={pending.length}
            tone="amber"
            empty="Nothing waiting — every detected field has been decided."
          >
            {pending.map((variable) => (
              <FieldRow
                key={variable.id}
                variable={variable}
                slots={slotsByVariable.get(variable.id) ?? []}
                threshold={threshold}
                busy={busy === variable.id}
                onUpdate={(patch) =>
                  run(variable.id, () => updateVariable(variable.id, patch))
                }
                onDelete={() =>
                  run(variable.id, () => deleteVariable(variable.id))
                }
              />
            ))}
          </FieldSection>

          <FieldSection
            title="Confirmed"
            count={confirmed.length}
            tone="green"
            empty="No fields confirmed yet. Confirmed fields are the ones that get filled in."
          >
            {confirmed.map((variable) => (
              <FieldRow
                key={variable.id}
                variable={variable}
                slots={slotsByVariable.get(variable.id) ?? []}
                threshold={threshold}
                busy={busy === variable.id}
                onUpdate={(patch) =>
                  run(variable.id, () => updateVariable(variable.id, patch))
                }
                onDelete={() =>
                  run(variable.id, () => deleteVariable(variable.id))
                }
              />
            ))}
          </FieldSection>

          {unmapped.length > 0 ? (
            <UnmappedPositions
              slots={unmapped}
              variables={confirmed}
              busy={busy}
              onAssign={(slotId, variableId) =>
                run(slotId, () =>
                  updateSlot(slotId, {
                    variable_id: variableId,
                    status: "confirmed",
                  }),
                )
              }
              onReject={(slotId) =>
                run(slotId, () => updateSlot(slotId, { status: "rejected" }))
              }
            />
          ) : null}

          {rejected.length > 0 ? (
            <p className="text-xs text-muted-foreground">
              {rejected.length} field{rejected.length === 1 ? "" : "s"} rejected.
              These won&rsquo;t be proposed again when you re-analyze.
            </p>
          ) : null}
        </div>
      )}

      <div className="border-t pt-6">
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" onClick={() => setAddOpen(true)}>
            <Plus className="mr-2 h-4 w-4" />
            Add a field manually
          </Button>
          <Button
            variant="secondary"
            onClick={() => setPlaceOpen(true)}
            disabled={confirmed.length === 0}
          >
            <MapPin className="mr-2 h-4 w-4" />
            Place a field in the document
          </Button>
        </div>
        <p className="mt-2 text-xs text-muted-foreground">
          Use these when a detail that changes per person wasn&rsquo;t picked up
          automatically. Add the field first, then point it at the place it
          belongs.
        </p>
      </div>

      <AddFieldDialog
        open={addOpen}
        onOpenChange={setAddOpen}
        existingNames={variables.map((v) => v.internal_name)}
        onCreate={createVariable}
      />

      <PlaceFieldDialog
        open={placeOpen}
        onOpenChange={setPlaceOpen}
        versionId={versionId}
        variables={confirmed}
        onPlace={createSlot}
      />
    </div>
  );
}

function FieldSection({
  title,
  count,
  tone,
  empty,
  children,
}: {
  title: string;
  count: number;
  tone: "amber" | "green";
  empty: string;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-3">
      <div className="flex items-center gap-2">
        <h2 className="text-sm font-bold uppercase tracking-wide text-muted-foreground">
          {title}
        </h2>
        <StatusPill tone={tone}>{count}</StatusPill>
      </div>
      {count === 0 ? (
        <p className="text-sm text-muted-foreground">{empty}</p>
      ) : (
        <div className="space-y-2">{children}</div>
      )}
    </section>
  );
}

function Banner({
  tone,
  children,
}: {
  tone: "error" | "warn" | "info";
  children: React.ReactNode;
}) {
  const styles = {
    error: "bg-destructive-soft text-destructive",
    warn: "bg-amber-tint text-amber",
    info: "bg-brand-tint text-brand",
  }[tone];

  return (
    <div className={`flex items-start gap-2 rounded-lg px-4 py-3 text-sm ${styles}`}>
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
      <span>{children}</span>
    </div>
  );
}

function PreviewPanel({
  result,
  onDismiss,
}: {
  result: DocPreviewResult;
  onDismiss: () => void;
}) {
  return (
    <div className="space-y-3 rounded-xl border bg-card p-4">
      <div className="flex items-start justify-between gap-2">
        <div>
          <p className="font-semibold">Preview ready</p>
          <p className="text-xs text-muted-foreground">
            Filled with sample data so you can check the layout. Nothing here is
            a real person.
          </p>
        </div>
        <Button variant="ghost" size="sm" onClick={onDismiss}>
          Dismiss
        </Button>
      </div>

      <div className="flex flex-wrap gap-2">
        {result.pdf_url ? (
          <Button asChild size="sm" variant="secondary">
            <a href={result.pdf_url} target="_blank" rel="noopener noreferrer">
              Open PDF
            </a>
          </Button>
        ) : null}
        {result.docx_url ? (
          <Button asChild size="sm" variant="ghost">
            <a href={result.docx_url} target="_blank" rel="noopener noreferrer">
              Download Word
            </a>
          </Button>
        ) : null}
      </div>

      {result.warnings.length > 0 ? (
        <ul className="space-y-1 text-xs text-amber">
          {result.warnings.map((warning) => (
            <li key={warning}>· {warning}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function EmptyFields({
  analyzing,
  onAnalyze,
}: {
  analyzing: boolean;
  onAnalyze: () => void;
}) {
  return (
    <div className="rounded-xl border border-dashed px-6 py-12 text-center">
      <Sparkles className="mx-auto h-8 w-8 text-muted-foreground" />
      <h3 className="mt-4 font-semibold">No fields yet</h3>
      <p className="mx-auto mt-2 max-w-md text-sm text-muted-foreground">
        Run the analysis to find the details that change per person. Your
        document is only read, never edited.
      </p>
      <Button className="mt-6" onClick={onAnalyze} disabled={analyzing}>
        {analyzing ? (
          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
        ) : (
          <Sparkles className="mr-2 h-4 w-4" />
        )}
        Find fields
      </Button>
    </div>
  );
}
