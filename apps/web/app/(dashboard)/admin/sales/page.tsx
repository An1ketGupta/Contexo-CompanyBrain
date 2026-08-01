"use client";

import { useMemo, useRef, useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import { toast } from "sonner";
import { Loader2, Settings2, Target, Upload } from "lucide-react";

import { Skeleton } from "@/components/ui/skeleton";
import { formatDistanceToNow } from "@/lib/date";
import { cn } from "@/lib/utils";
import type { Lead, LeadStatus } from "@/lib/types";

type LeadRow = Pick<
  Lead,
  | "id"
  | "company_name"
  | "domain"
  | "contact_name"
  | "contact_email"
  | "contact_title"
  | "status"
  | "follow_up_count"
  | "next_follow_up_at"
  | "last_contacted_at"
  | "replied_at"
  | "created_at"
  | "updated_at"
>;

interface LeadsResponse {
  leads: LeadRow[];
  total: number;
}

interface ImportRow {
  company_name: string;
  contact_email: string;
  contact_name?: string;
  contact_title?: string;
  domain?: string;
  context_note?: string;
}

type StatusFilter = LeadStatus | "all";

const STATUS_FILTERS: { value: StatusFilter; label: string }[] = [
  { value: "pending_review", label: "Needs review" },
  { value: "new", label: "New" },
  { value: "awaiting_reply", label: "Awaiting reply" },
  { value: "replied", label: "Replied" },
  { value: "escalated", label: "Escalated" },
  { value: "all", label: "All" },
];

const STATUS_STYLES: Record<string, string> = {
  pending_review: "border-amber/30 bg-amber-tint text-amber-ink",
  escalated: "border-red-500/30 bg-red-500/10 text-red-600 dark:text-red-400",
  replied:
    "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
  meeting_booked:
    "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
  won: "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
};

const fetcher = async (url: string): Promise<LeadsResponse> => {
  const res = await fetch(url, { cache: "no-store" });
  if (res.status === 403) throw new Error("Admin access required.");
  if (!res.ok) throw new Error(`Failed to load (${res.status})`);
  return res.json();
};

/**
 * Minimal CSV reader: handles quoted fields and embedded commas/newlines,
 * which is all a spreadsheet export actually produces. Deliberately not a
 * full RFC-4180 parser — anything stranger than this is better fixed in the
 * spreadsheet than guessed at here.
 */
function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;

  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i++;
        } else quoted = false;
      } else field += c;
      continue;
    }
    if (c === '"') quoted = true;
    else if (c === ",") {
      row.push(field);
      field = "";
    } else if (c === "\n") {
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
    } else if (c !== "\r") field += c;
  }
  if (field || row.length) {
    row.push(field);
    rows.push(row);
  }
  return rows.filter((r) => r.some((cell) => cell.trim()));
}

const HEADER_ALIASES: Record<keyof ImportRow, string[]> = {
  company_name: ["company_name", "company", "organization", "account"],
  contact_email: ["contact_email", "email", "e-mail", "work email"],
  contact_name: ["contact_name", "name", "full name", "contact"],
  contact_title: ["contact_title", "title", "job title", "role", "position"],
  domain: ["domain", "website", "url", "site"],
  context_note: ["context_note", "notes", "note", "context", "comments"],
};

function rowsToLeads(rows: string[][]): { leads: ImportRow[]; skipped: number } {
  if (rows.length < 2) return { leads: [], skipped: 0 };
  const header = rows[0].map((h) => h.trim().toLowerCase());
  const indexOf = (field: keyof ImportRow) =>
    header.findIndex((h) => HEADER_ALIASES[field].includes(h));

  const cols = {
    company_name: indexOf("company_name"),
    contact_email: indexOf("contact_email"),
    contact_name: indexOf("contact_name"),
    contact_title: indexOf("contact_title"),
    domain: indexOf("domain"),
    context_note: indexOf("context_note"),
  };

  const leads: ImportRow[] = [];
  let skipped = 0;
  for (const r of rows.slice(1)) {
    const pick = (i: number) => (i >= 0 ? (r[i] ?? "").trim() : "");
    const email = pick(cols.contact_email);
    const company = pick(cols.company_name);
    // Both are required by the API; drop incomplete rows here so the whole
    // upload doesn't 422 over one blank line in the spreadsheet.
    if (!email || !company) {
      skipped++;
      continue;
    }
    leads.push({
      company_name: company,
      contact_email: email,
      contact_name: pick(cols.contact_name) || undefined,
      contact_title: pick(cols.contact_title) || undefined,
      domain: pick(cols.domain) || undefined,
      context_note: pick(cols.context_note) || undefined,
    });
  }
  return { leads, skipped };
}

function ImportPanel({ onDone }: { onDone: () => void }) {
  const [pending, setPending] = useState<ImportRow[] | null>(null);
  const [skipped, setSkipped] = useState(0);
  const [startOutreach, setStartOutreach] = useState(false);
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const handleFile = async (file: File) => {
    const text = await file.text();
    const { leads, skipped: s } = rowsToLeads(parseCsv(text));
    if (leads.length === 0) {
      toast.error(
        "No usable rows. The file needs a header row with at least 'company' and 'email' columns.",
      );
      return;
    }
    setPending(leads);
    setSkipped(s);
  };

  const submit = async () => {
    if (!pending) return;
    setBusy(true);
    try {
      const res = await fetch("/api/admin/sales/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ leads: pending, start_outreach: startOutreach }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail ?? `Import failed (${res.status})`);
      toast.success(
        `Imported ${body.imported} lead${body.imported === 1 ? "" : "s"}` +
          (body.outreach_started ? " — drafting has started." : "."),
      );
      setPending(null);
      setSkipped(0);
      setStartOutreach(false);
      if (fileRef.current) fileRef.current.value = "";
      onDone();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Import failed.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="rounded-2xl border border-border bg-card p-4">
      <div className="font-mono text-[11px] font-bold uppercase tracking-[0.06em] text-muted-foreground">
        Import leads
      </div>
      <p className="mt-1 text-xs text-muted-foreground">
        Upload a CSV with a header row. Recognised columns: company, email,
        name, title, website, notes. Only company and email are required —
        anything in notes is passed to the agent as context when it writes.
      </p>

      <input
        ref={fileRef}
        type="file"
        accept=".csv,text/csv"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) void handleFile(f);
        }}
        className="mt-3 block w-full text-xs file:mr-3 file:rounded-full file:border file:border-border file:bg-muted file:px-3 file:py-1.5 file:text-xs file:font-bold"
      />

      {pending && (
        <div className="mt-3 space-y-3 rounded-xl border border-border bg-muted p-3">
          <div className="text-xs">
            <span className="font-bold">{pending.length}</span> lead
            {pending.length === 1 ? "" : "s"} ready to import
            {skipped > 0 && (
              <span className="text-muted-foreground">
                {" "}
                · {skipped} row{skipped === 1 ? "" : "s"} skipped (missing
                company or email)
              </span>
            )}
          </div>

          <label className="flex items-start gap-2 text-xs">
            <input
              type="checkbox"
              checked={startOutreach}
              onChange={(e) => setStartOutreach(e.target.checked)}
              className="mt-0.5 h-3.5 w-3.5 shrink-0 accent-[var(--brand)]"
            />
            <span>
              Start drafting immediately.
              <span className="block text-muted-foreground">
                Drafts still wait for your approval before anything is sent.
                Leave off if you want to check the list first.
              </span>
            </span>
          </label>

          <div className="flex items-center gap-2">
            <button
              type="button"
              disabled={busy}
              onClick={submit}
              className="inline-flex items-center gap-1.5 rounded-full bg-brand px-3.5 py-1.5 text-xs font-bold text-white transition-opacity hover:opacity-90 disabled:opacity-50"
            >
              {busy ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Upload className="h-3.5 w-3.5" />
              )}
              Import
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                setPending(null);
                setSkipped(0);
                if (fileRef.current) fileRef.current.value = "";
              }}
              className="rounded-full border border-border px-3.5 py-1.5 text-xs font-bold text-muted-foreground transition-colors hover:text-foreground disabled:opacity-50"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </section>
  );
}

export default function SalesLeadsPage() {
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("pending_review");

  const queryString = useMemo(() => {
    const params = new URLSearchParams();
    params.set("limit", "100");
    if (statusFilter !== "all") params.set("status", statusFilter);
    return params.toString();
  }, [statusFilter]);

  const { data, error, isLoading, mutate } = useSWR<LeadsResponse>(
    `/api/admin/sales?${queryString}`,
    fetcher,
    { revalidateOnFocus: false, refreshInterval: 30_000 },
  );

  const leads = data?.leads ?? [];

  return (
    <div className="mx-auto w-full max-w-5xl space-y-5 p-6">
      <header className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <Target className="h-5 w-5 text-muted-foreground" />
            <h1 className="text-2xl font-extrabold tracking-tight">Sales</h1>
          </div>
          <p className="mt-0.5 text-sm text-muted-foreground">
            Outreach drafted against your knowledge base. Nothing is sent until
            you approve it.
          </p>
        </div>
        <Link
          href="/admin/sales/settings"
          className="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-border px-3 py-1.5 text-xs font-bold text-muted-foreground transition-colors hover:text-foreground"
        >
          <Settings2 className="h-3.5 w-3.5" />
          Settings
        </Link>
      </header>

      <ImportPanel onDone={() => void mutate()} />

      <div className="flex flex-wrap gap-1 rounded-xl bg-muted p-1">
        {STATUS_FILTERS.map((f) => (
          <button
            key={f.value}
            type="button"
            onClick={() => setStatusFilter(f.value)}
            className={cn(
              "rounded-lg px-3 py-1.5 text-xs font-bold transition-colors",
              statusFilter === f.value
                ? "bg-card text-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {f.label}
          </button>
        ))}
      </div>

      {error && (
        <div className="rounded-2xl border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-600 dark:text-red-400">
          {error.message}
        </div>
      )}

      {isLoading && (
        <div className="space-y-2">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-16 w-full rounded-2xl" />
          ))}
        </div>
      )}

      {!isLoading && !error && leads.length === 0 && (
        <div className="rounded-2xl border border-border bg-card p-8 text-center">
          <p className="text-sm font-bold">No leads here</p>
          <p className="mt-1 text-sm text-muted-foreground">
            Import a CSV above to get started, or switch the filter to see leads
            at another stage.
          </p>
        </div>
      )}

      <div className="space-y-2">
        {leads.map((l) => (
          <Link
            key={l.id}
            href={`/admin/sales/${l.id}`}
            className="block rounded-2xl border border-border bg-card p-4 transition-colors hover:border-brand/40"
          >
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="truncate text-sm font-bold">{l.company_name}</div>
                <div className="mt-0.5 truncate text-xs text-muted-foreground">
                  {l.contact_name ? `${l.contact_name} · ` : ""}
                  {l.contact_email}
                  {l.contact_title ? ` · ${l.contact_title}` : ""}
                </div>
              </div>
              <div className="shrink-0 text-xs text-muted-foreground">
                {formatDistanceToNow(l.updated_at)}
              </div>
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-1.5">
              <span
                className={cn(
                  "rounded-full border px-2 py-0.5 text-[11px] font-bold",
                  STATUS_STYLES[l.status] ??
                    "border-border bg-muted text-muted-foreground",
                )}
              >
                {l.status.replace(/_/g, " ")}
              </span>
              {l.follow_up_count > 0 && (
                <span className="rounded-full border border-border bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
                  {l.follow_up_count} sent
                </span>
              )}
              {l.next_follow_up_at && (
                <span className="rounded-full border border-border bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
                  follow up {formatDistanceToNow(l.next_follow_up_at)}
                </span>
              )}
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}
