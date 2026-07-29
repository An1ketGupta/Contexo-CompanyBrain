"use client";

import Link from "next/link";
import useSWR from "swr";
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  FileText,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * Whether onboarding can actually produce its documents.
 *
 * Replaces the old in-page template configurator. Templates are now managed in
 * their own library (`/document-templates`), so this panel only answers the
 * question the onboarding dashboard actually needs answered: is anything going
 * to block a run?
 *
 * It distinguishes the two ways a document type can be unready. The old panel
 * only checked whether a template had been *tagged*, so "template uploaded but
 * nobody confirmed its fields" looked identical to ready — and then failed at
 * generation time, which is the worst place to find out.
 */

interface Readiness {
  type_key: string;
  label: string;
  ready: boolean;
  reason: string | null;
  template_id: string | null;
  template_name: string | null;
  version_id: string | null;
  confirmed_field_count: number;
}

const REASON_COPY: Record<string, string> = {
  no_template: "No template set as the default for this type.",
  no_confirmed_fields:
    "A template is set, but none of its fields have been confirmed yet.",
};

const fetcher = async (url: string): Promise<Readiness[]> => {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) return [];
  return res.json();
};

export function TemplateReadinessPanel({
  open,
  onToggle,
}: {
  open: boolean;
  onToggle: () => void;
}) {
  const { data, isLoading } = useSWR<Readiness[]>(
    "/api/document-templates/readiness",
    fetcher,
    { revalidateOnFocus: true },
  );

  const rows = data ?? [];
  const missing = rows.filter((r) => !r.ready);
  const allReady = rows.length > 0 && missing.length === 0;

  return (
    <div className="mb-6 overflow-hidden rounded-2xl border border-border bg-card">
      <button
        onClick={onToggle}
        className="flex w-full items-center justify-between px-5 py-4 text-left"
      >
        <div className="flex items-center gap-2">
          <FileText className="h-4 w-4 text-muted-foreground" />
          <span className="font-mono text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
            Document templates
          </span>
          {isLoading ? null : missing.length > 0 ? (
            <span className="rounded-full bg-amber-tint px-2 py-0.5 text-[10px] font-bold text-amber">
              {missing.length} not ready
            </span>
          ) : allReady ? (
            <span className="rounded-full bg-success-tint px-2 py-0.5 text-[10px] font-bold text-success">
              All set
            </span>
          ) : null}
        </div>
        {open ? (
          <ChevronUp className="h-4 w-4 text-muted-foreground" />
        ) : (
          <ChevronDown className="h-4 w-4 text-muted-foreground" />
        )}
      </button>

      {open ? (
        <div className="space-y-3 border-t border-border px-5 pb-5 pt-4">
          <div className="grid gap-2 sm:grid-cols-2">
            {rows.map((row) => (
              <div
                key={row.type_key}
                className={cn(
                  "rounded-xl border p-3",
                  row.ready ? "border-border" : "border-amber/40 bg-amber-tint/30",
                )}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-semibold">{row.label}</p>
                    {row.ready ? (
                      <p className="mt-0.5 text-xs text-muted-foreground">
                        {row.template_name} · {row.confirmed_field_count} field
                        {row.confirmed_field_count === 1 ? "" : "s"}
                      </p>
                    ) : (
                      <p className="mt-0.5 text-xs text-amber">
                        {REASON_COPY[row.reason ?? ""] ?? "Not ready."}
                      </p>
                    )}
                  </div>
                  {row.ready ? (
                    <CheckCircle2 className="h-4 w-4 shrink-0 text-success" />
                  ) : (
                    <AlertTriangle className="h-4 w-4 shrink-0 text-amber" />
                  )}
                </div>

                {!row.ready && row.template_id && row.version_id ? (
                  <Button asChild size="sm" variant="ghost" className="mt-2 -ml-2">
                    <Link
                      href={`/document-templates/${row.template_id}/fields?version=${row.version_id}`}
                    >
                      Confirm fields
                      <ArrowRight className="ml-1.5 h-3 w-3" />
                    </Link>
                  </Button>
                ) : null}
              </div>
            ))}
          </div>

          <Button asChild variant="secondary" size="sm">
            <Link href="/document-templates">
              Manage templates
              <ArrowRight className="ml-2 h-3 w-3" />
            </Link>
          </Button>
        </div>
      ) : null}
    </div>
  );
}
