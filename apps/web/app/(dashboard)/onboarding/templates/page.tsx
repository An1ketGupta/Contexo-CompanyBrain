"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import useSWR from "swr";
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  ExternalLink,
  Eye,
  FileText,
  Loader2,
  PenLine,
  Sparkles,
  Trash2,
  XCircle,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { TemplateMapperModal } from "@/components/onboarding/template-mapper-modal";

interface DocumentRow {
  id: string;
  name: string;
  file_type: string | null;
  template_kind: string | null;
  created_at: string;
}

interface DocListResponse {
  documents: DocumentRow[];
  total: number;
}

interface TemplateStatusRow {
  id: string;
  name: string;
  template_kind: string;
}

interface TemplateStatusResponse {
  loi: TemplateStatusRow | null;
  appointment_letter: TemplateStatusRow | null;
  nda: TemplateStatusRow | null;
  induction: TemplateStatusRow | null;
}

interface DocusealTemplateItem {
  template_key: string;
  label: string;
  docuseal_template_id: string | null;
  role_names: string[];
  field_map: Record<string, string>;
  note: string | null;
  configured: boolean;
}

interface DocusealTemplatesStatus {
  esign_configured: boolean;
  templates: DocusealTemplateItem[];
}

const KIND_LABEL: Record<string, string> = {
  loi: "Letter of Intent",
  appointment_letter: "Appointment Letter",
  nda: "NDA",
  induction: "Induction",
};

const fetcher = async <T,>(url: string): Promise<T> => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed (${res.status})`);
  return res.json();
};

export default function OnboardingTemplatesPage() {
  const { data: status, mutate: refreshStatus } =
    useSWR<TemplateStatusResponse>("/api/onboarding/templates/status", fetcher);
  const { data: docs, isLoading } = useSWR<DocListResponse>(
    "/api/documents/list",
    fetcher,
  );
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mapper, setMapper] = useState<{
    docId: string;
    docName: string;
    kind: string;
  } | null>(null);

  const docxDocs = useMemo(
    () =>
      (docs?.documents || []).filter((d) =>
        (d.file_type || "").includes("officedocument.wordprocessing"),
      ),
    [docs],
  );

  async function tagDoc(docId: string, kind: string) {
    setBusy(`${docId}-${kind}`);
    setError(null);
    try {
      const res = await fetch("/api/onboarding/templates", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ document_id: docId, template_kind: kind }),
      });
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as {
          detail?: string;
          message?: string;
        };
        setError(
          body.detail || body.message || "Couldn't tag the template.",
        );
        return;
      }
      await refreshStatus();
      // Open the AI mapper modal automatically — it will short-circuit and
      // close itself if the DOCX already has {{ placeholders }}.
      const doc = docxDocs.find((d) => d.id === docId);
      if (doc) {
        setMapper({ docId, docName: doc.name, kind });
      }
    } finally {
      setBusy(null);
    }
  }

  function openMapperFor(docId: string, kind: string) {
    const doc = docxDocs.find((d) => d.id === docId);
    if (!doc) return;
    setMapper({ docId, docName: doc.name, kind });
  }

  async function previewDoc(docId: string) {
    setBusy(`preview-${docId}`);
    setError(null);
    try {
      const res = await fetch(
        `/api/onboarding/templates/${docId}/preview`,
        { method: "POST" },
      );
      const body = (await res.json().catch(() => ({}))) as {
        preview_url?: string;
        detail?: { error?: string; variable?: string; message?: string } | string;
        message?: string;
      };
      if (!res.ok) {
        // The 422 case: a specific missing variable. Surface the name.
        if (typeof body.detail === "object" && body.detail?.variable) {
          setError(
            `Template references unknown variable: {{ ${body.detail.variable} }}. ` +
              "Remove the placeholder from your DOCX or supply the value.",
          );
        } else {
          const msg =
            typeof body.detail === "string"
              ? body.detail
              : body.detail?.message || body.message;
          setError(msg || "Couldn't preview the template.");
        }
        return;
      }
      if (body.preview_url) {
        window.open(body.preview_url, "_blank", "noopener,noreferrer");
      }
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="mx-auto max-w-4xl px-4 py-8 sm:px-6 lg:px-8">
      <Link
        href="/onboarding"
        className="mb-4 inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="h-3.5 w-3.5" /> Back to onboarding
      </Link>

      <header className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">
          Onboarding templates
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Tag the DOCX templates the Onboarding agent will fill per candidate.
          Upload templates from the{" "}
          <Link href="/documents" className="underline">
            documents page
          </Link>{" "}
          first, then assign each one to the right slot.
        </p>
      </header>

      {error ? (
        <div className="mb-4 rounded-md border border-red-300/60 bg-red-50 p-3 text-sm text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-200">
          {error}
        </div>
      ) : null}

      {(["loi", "appointment_letter", "nda", "induction"] as const).map((kind) => {
        const current = status?.[kind] ?? null;
        return (
          <section
            key={kind}
            className="mb-4 rounded-lg border border-border bg-background p-4"
          >
            <div className="mb-3 flex items-center justify-between gap-2">
              <div>
                <p className="text-sm font-medium text-foreground">
                  {KIND_LABEL[kind]}
                </p>
                <p className="text-xs text-muted-foreground">
                  {current ? (
                    <span className="inline-flex items-center gap-1 text-emerald-700 dark:text-emerald-300">
                      <CheckCircle2 className="h-3 w-3" />
                      Active: {current.name}
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1 text-red-600 dark:text-red-400">
                      <XCircle className="h-3 w-3" />
                      Not configured
                    </span>
                  )}
                </p>
              </div>
            </div>

            <TemplatePicker
              kind={kind}
              docs={docxDocs}
              currentId={current?.id || null}
              isLoading={isLoading}
              busy={busy}
              onSelect={(docId) => tagDoc(docId, kind)}
            />

            {current ? (
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => previewDoc(current.id)}
                  disabled={busy === `preview-${current.id}`}
                >
                  {busy === `preview-${current.id}` ? (
                    <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Eye className="mr-1.5 h-3.5 w-3.5" />
                  )}
                  Preview with sample data
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => openMapperFor(current.id, kind)}
                >
                  <Sparkles className="mr-1.5 h-3.5 w-3.5 text-violet-500" />
                  AI convert blanks
                </Button>
                <a
                  href="https://github.com/nirnayaiq/docs/blob/main/onboarding-template-vars.md"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
                >
                  Variable reference <ExternalLink className="h-3 w-3" />
                </a>
              </div>
            ) : null}
          </section>
        );
      })}

      <EsignTemplatesSection />

      <TemplateMapperModal
        documentId={mapper?.docId ?? null}
        documentName={mapper?.docName}
        templateKind={mapper?.kind ?? "loi"}
        open={mapper !== null}
        onOpenChange={(o) => {
          if (!o) setMapper(null);
        }}
        onApplied={() => {
          void refreshStatus();
        }}
      />
    </div>
  );
}

function TemplatePicker({
  kind,
  docs,
  currentId,
  isLoading,
  busy,
  onSelect,
}: {
  kind: string;
  docs: DocumentRow[];
  currentId: string | null;
  isLoading: boolean;
  busy: string | null;
  onSelect: (docId: string) => void;
}) {
  const [picked, setPicked] = useState<string>(currentId ?? "");

  if (isLoading) return <Skeleton className="h-10 w-full" />;
  if (docs.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        No DOCX files in your knowledge base yet. Upload your template from the{" "}
        <Link href="/documents" className="underline">
          documents page
        </Link>{" "}
        first.
      </p>
    );
  }
  return (
    <div className="flex items-center gap-2">
      <Select value={picked} onValueChange={(v) => setPicked(v)}>
        <SelectTrigger className="max-w-md">
          <SelectValue placeholder="Pick a DOCX from your knowledge base…" />
        </SelectTrigger>
        <SelectContent>
          {docs.map((d) => (
            <SelectItem key={d.id} value={d.id}>
              <span className="flex items-center gap-2">
                <FileText className="h-3.5 w-3.5" />
                {d.name}
              </span>
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <Button
        size="sm"
        disabled={!picked || picked === currentId || busy?.startsWith(picked)}
        onClick={() => onSelect(picked)}
      >
        {busy?.startsWith(picked) ? (
          <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
        ) : null}
        {currentId ? "Replace template" : "Assign template"}
      </Button>
    </div>
  );
}

// ── DocuSeal e-sign template bindings ──────────────────────────────────────
// Free self-hosted DocuSeal can't create a submission from an uploaded PDF, so
// signing runs off a template built once in the DocuSeal admin. HR pastes the
// numeric template id here per signing envelope. Without a binding, the run
// falls back to the print/scan (LOI) or plain-email (offer bundle) flow.

function EsignTemplatesSection() {
  const { data, mutate } = useSWR<DocusealTemplatesStatus>(
    "/api/onboarding/docuseal-templates",
    fetcher,
  );

  return (
    <section className="mt-10 border-t border-border pt-8">
      <header className="mb-4">
        <h2 className="text-lg font-semibold tracking-tight text-foreground">
          E-signature templates (DocuSeal)
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">
          The free DocuSeal edition can&apos;t accept an uploaded PDF over the
          API, so signing uses a template you build once in the DocuSeal admin.
          Upload your document there, drop signature + field boxes on the blanks
          (name each text field to match a{" "}
          <a
            href="https://github.com/nirnayaiq/docs/blob/main/onboarding-template-vars.md"
            target="_blank"
            rel="noopener noreferrer"
            className="underline"
          >
            template variable
          </a>
          , e.g. <code className="rounded bg-muted px-1">candidate_name</code>),
          then paste the template&apos;s numeric id below.
        </p>
      </header>

      {data && !data.esign_configured ? (
        <div className="mb-4 flex items-start gap-2 rounded-md border border-amber-300/60 bg-amber-50 p-3 text-sm text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            DocuSeal credentials aren&apos;t set on the backend
            (DOCUSEAL_BASE_URL / DOCUSEAL_API_KEY / DOCUSEAL_WEBHOOK_SECRET).
            Until they are, onboarding uses the print/scan &amp; email fallback
            even with a template configured here.
          </span>
        </div>
      ) : null}

      {!data ? (
        <Skeleton className="h-24 w-full" />
      ) : (
        data.templates.map((t) => (
          <EsignTemplateCard
            key={t.template_key}
            item={t}
            onChanged={() => void mutate()}
          />
        ))
      )}
    </section>
  );
}

function EsignTemplateCard({
  item,
  onChanged,
}: {
  item: DocusealTemplateItem;
  onChanged: () => void;
}) {
  const twoRole = item.template_key === "loi";
  const [templateId, setTemplateId] = useState(item.docuseal_template_id ?? "");
  const [roleNames, setRoleNames] = useState(
    (item.role_names.length
      ? item.role_names
      : twoRole
        ? ["First Party", "Second Party"]
        : ["First Party"]
    ).join(", "),
  );
  const [busy, setBusy] = useState<"save" | "delete" | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setBusy("save");
    setError(null);
    try {
      const roles = roleNames
        .split(",")
        .map((r) => r.trim())
        .filter(Boolean);
      const res = await fetch(
        `/api/onboarding/docuseal-templates/${item.template_key}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            docuseal_template_id: templateId.trim(),
            role_names: roles,
          }),
        },
      );
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as {
          detail?: string;
          message?: string;
        };
        setError(body.detail || body.message || "Couldn't save the template.");
        return;
      }
      onChanged();
    } finally {
      setBusy(null);
    }
  }

  async function remove() {
    setBusy("delete");
    setError(null);
    try {
      const res = await fetch(
        `/api/onboarding/docuseal-templates/${item.template_key}`,
        { method: "DELETE" },
      );
      if (!res.ok && res.status !== 204) {
        setError("Couldn't remove the binding.");
        return;
      }
      setTemplateId("");
      onChanged();
    } finally {
      setBusy(null);
    }
  }

  const dirty =
    templateId.trim() !== (item.docuseal_template_id ?? "") ||
    roleNames.trim() !== item.role_names.join(", ");

  return (
    <div className="mb-4 rounded-lg border border-border bg-background p-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <div>
          <p className="text-sm font-medium text-foreground">{item.label}</p>
          <p className="text-xs text-muted-foreground">
            {item.configured ? (
              <span className="inline-flex items-center gap-1 text-emerald-700 dark:text-emerald-300">
                <CheckCircle2 className="h-3 w-3" />
                Template #{item.docuseal_template_id}
              </span>
            ) : (
              <span className="inline-flex items-center gap-1 text-red-600 dark:text-red-400">
                <XCircle className="h-3 w-3" />
                Not configured — using fallback flow
              </span>
            )}
          </p>
        </div>
        <PenLine className="h-4 w-4 text-muted-foreground" />
      </div>

      {error ? (
        <div className="mb-3 rounded-md border border-red-300/60 bg-red-50 p-2 text-xs text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-200">
          {error}
        </div>
      ) : null}

      <div className="grid gap-3 sm:grid-cols-2">
        <label className="block">
          <span className="mb-1 block text-xs font-medium text-muted-foreground">
            DocuSeal template id
          </span>
          <Input
            value={templateId}
            inputMode="numeric"
            placeholder="e.g. 1000001"
            onChange={(e) => setTemplateId(e.target.value)}
          />
        </label>
        <label className="block">
          <span className="mb-1 block text-xs font-medium text-muted-foreground">
            {twoRole
              ? "Role names (HR first, candidate second)"
              : "Role name (candidate)"}
          </span>
          <Input
            value={roleNames}
            placeholder={twoRole ? "First Party, Second Party" : "First Party"}
            onChange={(e) => setRoleNames(e.target.value)}
          />
        </label>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button size="sm" onClick={save} disabled={busy !== null || !dirty}>
          {busy === "save" ? (
            <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
          ) : null}
          Save
        </Button>
        {item.configured ? (
          <Button
            variant="outline"
            size="sm"
            onClick={remove}
            disabled={busy !== null}
          >
            {busy === "delete" ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            ) : (
              <Trash2 className="mr-1.5 h-3.5 w-3.5" />
            )}
            Remove
          </Button>
        ) : null}
      </div>
    </div>
  );
}
