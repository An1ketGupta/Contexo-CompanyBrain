"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import useSWR from "swr";
import {
  AlertTriangle,
  ArrowLeft,
  ExternalLink,
  Eye,
  FileText,
  Loader2,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { PageHeader, StatusPill } from "@/components/actual/kit";
import { TemplateMapperModal } from "@/components/onboarding/template-mapper-modal";
import {
  TemplateSlot,
  type TemplateStatusRow,
} from "@/components/onboarding/template-slot";
import { cn } from "@/lib/utils";

interface TemplateStatusResponse {
  loi: TemplateStatusRow | null;
  appointment_letter: TemplateStatusRow | null;
  nda: TemplateStatusRow | null;
  induction: TemplateStatusRow | null;
}

interface IntegrationsStatusResponse {
  drive?: {
    available?: boolean;
    connected?: boolean;
  };
}

type TemplateKind = keyof TemplateStatusResponse;

// Ordered to mirror the onboarding sequence: LOI → Appointment + NDA →
// Induction. The one-line purpose is the microcopy on each config card.
const TEMPLATES: { kind: TemplateKind; label: string; purpose: string }[] = [
  {
    kind: "loi",
    label: "Letter of Intent",
    purpose: "The first offer document sent to a candidate.",
  },
  {
    kind: "appointment_letter",
    label: "Appointment Letter",
    purpose: "Formal appointment terms issued after background verification.",
  },
  {
    kind: "nda",
    label: "NDA",
    purpose: "Confidentiality agreement bundled with the appointment.",
  },
  {
    kind: "induction",
    label: "Induction",
    purpose: "Day-one induction pack sent once policies are acknowledged.",
  },
];

const fetcher = async <T,>(url: string): Promise<T> => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed (${res.status})`);
  return res.json();
};

export default function OnboardingTemplatesPage() {
  const { data: status, mutate: refreshStatus } =
    useSWR<TemplateStatusResponse>("/api/onboarding/templates/status", fetcher);
  const { data: integrations } = useSWR<IntegrationsStatusResponse>(
    "/api/integrations/status",
    fetcher,
  );
  const driveConnected = Boolean(integrations?.drive?.connected);

  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mapper, setMapper] = useState<{ docId: string; kind: string } | null>(
    null,
  );

  const configuredCount = useMemo(
    () =>
      status
        ? TEMPLATES.filter(
            (t) => status[t.kind]?.template_status === "active",
          ).length
        : 0,
    [status],
  );
  const allSet = status !== undefined && configuredCount === TEMPLATES.length;
  const remaining = TEMPLATES.length - configuredCount;

  async function tagDoc(docId: string, kind: string) {
    setBusy(kind);
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
      setMapper({ docId, kind });
    } finally {
      setBusy(null);
    }
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
    <div className="mx-auto max-w-3xl space-y-8 p-6 md:p-8">
      <div className="space-y-5">
        <Link
          href="/onboarding"
          className="inline-flex items-center gap-1.5 text-xs font-semibold text-muted-foreground transition-colors hover:text-foreground"
        >
          <ArrowLeft className="h-3.5 w-3.5" /> Back to onboarding
        </Link>

        <PageHeader
          eyebrow="Onboarding"
          title="Onboarding templates"
          description="Assign a DOCX to each onboarding document. The Onboarding agent fills the {{ placeholders }} and generates the final file for every candidate."
        />
      </div>

      {error ? (
        <div className="flex items-start gap-3 rounded-2xl border border-destructive/30 bg-destructive-soft p-4">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
          <p className="text-sm font-medium text-destructive">{error}</p>
        </div>
      ) : null}

      {/* ── Setup progress ─────────────────────────────────────────── */}
      <div className="rounded-2xl border border-border bg-card p-5">
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="font-mono text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
              Setup progress
            </p>
            <p className="mt-1.5 text-sm font-bold text-foreground">
              {configuredCount} of {TEMPLATES.length} templates configured
            </p>
          </div>
          {status === undefined ? null : allSet ? (
            <StatusPill tone="green">All set</StatusPill>
          ) : (
            <StatusPill tone="amber">{remaining} remaining</StatusPill>
          )}
        </div>
        <div className="mt-4 h-2 overflow-hidden rounded-full bg-muted">
          <div
            className="h-full rounded-full bg-brand transition-all duration-500"
            style={{
              width: `${(configuredCount / TEMPLATES.length) * 100}%`,
            }}
          />
        </div>
      </div>

      {/* ── Template slots ─────────────────────────────────────────── */}
      <div className="space-y-3">
        <p className="font-mono text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
          Documents
        </p>

        {TEMPLATES.map(({ kind, label, purpose }, i) => {
          const current = status?.[kind] ?? null;
          return (
            <section
              key={kind}
              className="rounded-2xl border border-border bg-card p-5"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="flex items-start gap-3">
                  <div
                    className={cn(
                      "flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-xs font-extrabold",
                      current
                        ? "bg-brand text-brand-foreground"
                        : "bg-muted text-muted-foreground",
                    )}
                  >
                    {i + 1}
                  </div>
                  <div>
                    <p className="text-sm font-bold text-foreground">{label}</p>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      {purpose}
                    </p>
                  </div>
                </div>
                {current?.template_status === "active" ? (
                  <StatusPill tone="green">Active</StatusPill>
                ) : current ? (
                  <StatusPill tone="amber">Draft — not saved</StatusPill>
                ) : (
                  <StatusPill tone="gray">Not configured</StatusPill>
                )}
              </div>

              {current ? (
                <div className="mt-4 flex items-center gap-2 rounded-xl border border-border bg-muted/40 px-3 py-2.5">
                  <FileText className="h-4 w-4 shrink-0 text-brand" />
                  <span className="truncate text-xs font-semibold text-foreground">
                    {current.name}
                  </span>
                </div>
              ) : null}

              <div className="mt-4">
                <TemplateSlot
                  bare
                  kind={kind}
                  label={label}
                  current={current}
                  onAssign={(docId) => tagDoc(docId, kind)}
                  onDriveImported={() => {
                    void refreshStatus();
                  }}
                  isBusy={busy === kind}
                  driveConnected={driveConnected}
                />
              </div>

              {current ? (
                <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-border pt-4">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => previewDoc(current.id)}
                    disabled={busy === `preview-${current.id}`}
                  >
                    {busy === `preview-${current.id}` ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Eye className="h-3.5 w-3.5" />
                    )}
                    Preview with sample data
                  </Button>
                </div>
              ) : null}
            </section>
          );
        })}
      </div>

      <TemplateMapperModal
        documentId={mapper?.docId ?? null}
        templateKind={mapper?.kind ?? "loi"}
        open={mapper !== null}
        onOpenChange={(o) => {
          if (!o) {
            setMapper(null);
            // "Save template" inside the mapper promotes draft→active; refresh
            // so the Active/Draft pill updates without a reload.
            void refreshStatus();
          }
        }}
        onApplied={() => {
          void refreshStatus();
        }}
      />
    </div>
  );
}
