"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import {
  AlertTriangle,
  Archive,
  CheckCircle2,
  FileText,
  Loader2,
  Plus,
  Star,
} from "lucide-react";

import { PageHeader, StatusPill, type PillTone } from "@/components/actual/kit";
import { UploadTemplateDialog } from "@/components/document-templates/upload-template-dialog";
import { Button } from "@/components/ui/button";
import {
  useDocumentTemplates,
  useDocumentTypes,
} from "@/hooks/use-document-templates";
import type { DocTemplate, DocTemplateStatus } from "@/lib/types";
import { cn } from "@/lib/utils";

const STATUS_TONE: Record<DocTemplateStatus, PillTone> = {
  active: "green",
  draft: "amber",
  archived: "gray",
};

export default function DocumentTemplatesPage() {
  const [showArchived, setShowArchived] = useState(false);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const { types } = useDocumentTypes();
  const { templates, isLoading, makeDefault, archiveTemplate } =
    useDocumentTemplates(showArchived);

  // Group by document type so the library reads as "what kinds of document do
  // we produce", which is how HR thinks about it, rather than a flat list.
  const grouped = useMemo(() => {
    const buckets = new Map<string, DocTemplate[]>();
    for (const template of templates) {
      const key = template.document_type_label ?? "Other";
      const bucket = buckets.get(key);
      if (bucket) bucket.push(template);
      else buckets.set(key, [template]);
    }
    return [...buckets.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [templates]);

  async function run(id: string, fn: () => Promise<void>) {
    setBusyId(id);
    setError(null);
    try {
      await fn();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="mx-auto max-w-6xl space-y-8 p-6 md:p-8">
      <PageHeader
        eyebrow="Documents"
        title="Document templates"
        actions={
          <Button onClick={() => setUploadOpen(true)}>
            <Plus className="mr-2 h-4 w-4" />
            Upload template
          </Button>
        }
      />

      {error ? (
        <div className="flex items-start gap-2 rounded-lg bg-destructive-soft px-4 py-3 text-sm text-destructive">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{error}</span>
        </div>
      ) : null}

      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          {isLoading
            ? "Loading…"
            : `${templates.length} template${templates.length === 1 ? "" : "s"}`}
        </p>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setShowArchived((v) => !v)}
        >
          <Archive className="mr-2 h-4 w-4" />
          {showArchived ? "Hide archived" : "Show archived"}
        </Button>
      </div>

      {isLoading ? (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading templates…
        </div>
      ) : templates.length === 0 ? (
        <EmptyLibrary onUpload={() => setUploadOpen(true)} />
      ) : (
        <div className="space-y-8">
          {grouped.map(([typeLabel, rows]) => (
            <section key={typeLabel} className="space-y-3">
              <h2 className="text-sm font-bold uppercase tracking-wide text-muted-foreground">
                {typeLabel}
              </h2>
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                {rows.map((template) => (
                  <TemplateCard
                    key={template.id}
                    template={template}
                    busy={busyId === template.id}
                    onMakeDefault={() =>
                      run(template.id, () => makeDefault(template.id))
                    }
                    onArchive={() =>
                      run(template.id, () => archiveTemplate(template.id))
                    }
                  />
                ))}
              </div>
            </section>
          ))}
        </div>
      )}

      <UploadTemplateDialog
        open={uploadOpen}
        onOpenChange={setUploadOpen}
        types={types}
      />
    </div>
  );
}

function TemplateCard({
  template,
  busy,
  onMakeDefault,
  onArchive,
}: {
  template: DocTemplate;
  busy: boolean;
  onMakeDefault: () => void;
  onArchive: () => void;
}) {
  const archived = template.status === "archived";

  return (
    <div
      className={cn(
        "flex flex-col justify-between gap-4 rounded-xl border bg-card p-4",
        archived && "opacity-60",
      )}
    >
      <div className="space-y-2">
        <div className="flex items-start justify-between gap-2">
          <div className="flex items-start gap-2">
            <FileText className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
            <div>
              <p className="font-semibold leading-tight">{template.name}</p>
              {template.description ? (
                <p className="mt-1 text-xs text-muted-foreground">
                  {template.description}
                </p>
              ) : null}
            </div>
          </div>
          <StatusPill tone={STATUS_TONE[template.status]}>
            {template.status}
          </StatusPill>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {template.current_version_id ? (
          <Button asChild size="sm" variant="secondary">
            <Link
              href={`/document-templates/${template.id}/fields?version=${template.current_version_id}`}
            >
              Review fields
            </Link>
          </Button>
        ) : (
          <span className="text-xs text-muted-foreground">No file uploaded</span>
        )}

        {!archived && !template.is_default ? (
          <Button
            size="sm"
            variant="ghost"
            onClick={onMakeDefault}
            disabled={busy}
          >
            {busy ? (
              <Loader2 className="mr-2 h-3 w-3 animate-spin" />
            ) : (
              <CheckCircle2 className="mr-2 h-3 w-3" />
            )}
            Make default
          </Button>
        ) : null}

        {!archived ? (
          <Button size="sm" variant="ghost" onClick={onArchive} disabled={busy}>
            Archive
          </Button>
        ) : null}
      </div>
    </div>
  );
}

function EmptyLibrary({ onUpload }: { onUpload: () => void }) {
  return (
    <div className="rounded-xl border border-dashed px-6 py-12 text-center">
      <FileText className="mx-auto h-8 w-8 text-muted-foreground" />
      <h3 className="mt-4 font-semibold">No templates yet</h3>
      <Button className="mt-6" onClick={onUpload}>
        <Plus className="mr-2 h-4 w-4" />
        Upload your first template
      </Button>
    </div>
  );
}
