"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import useSWR from "swr";
import { Loader2, Plus, Search, AlertTriangle } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { NewDealDialog } from "@/components/sales-agent/new-deal-dialog";

interface DealRow {
  id: string;
  company_name: string;
  contact_name: string | null;
  contact_title: string | null;
  contact_email: string | null;
  status: string;
  icp_score: number | null;
  blocked_reason: string | null;
  outreach_sent_at: string | null;
  reply_received_at: string | null;
  meeting_at: string | null;
  proposal_sent_at: string | null;
  followup_count: number;
  checkin_count: number;
  deal_value_amount: number | null;
  deal_value_currency: string | null;
  closed_at: string | null;
  close_outcome: string | null;
  created_at: string;
  updated_at: string;
}

const STATUS_LABELS: Record<string, string> = {
  lead_entered: "New lead",
  researching: "Researching prospect",
  icp_scoring: "Scoring against ICP",
  icp_scored: "ICP scored",
  outreach_drafting: "Drafting outreach",
  outreach_pending_rep_review: "Outreach awaiting your review",
  outreach_sent: "Outreach sent",
  awaiting_reply: "Awaiting reply",
  followup_pending_rep_review: "Follow-up awaiting review",
  no_reply_closed: "Closed — no reply",
  meeting_booked: "Meeting booked",
  prep_generating: "Preparing brief",
  prep_ready: "Prep brief ready",
  call_summarizing: "Summarizing call",
  call_summarized: "Call summarized",
  awaiting_next_step_decision: "Choose next step",
  proposal_drafting: "Drafting proposal",
  proposal_pending_rep_review: "Proposal awaiting review",
  proposal_sent: "Proposal sent",
  awaiting_decision: "Awaiting decision",
  checkin_pending_rep_review: "Check-in awaiting review",
  at_risk: "At risk",
  closed_won: "Won",
  closed_lost: "Lost",
  cancelled: "Cancelled",
  blocked_missing_icp_doc: "Blocked — missing ICP doc",
  blocked_missing_template: "Blocked — missing template",
  failed: "Failed",
};

const TERMINAL = new Set([
  "closed_won",
  "closed_lost",
  "cancelled",
  "no_reply_closed",
  "failed",
]);
const NEEDS_REP = new Set([
  "outreach_pending_rep_review",
  "followup_pending_rep_review",
  "checkin_pending_rep_review",
  "proposal_pending_rep_review",
  "awaiting_next_step_decision",
  "at_risk",
]);

type Tab = "all" | "needs_action" | "active" | "won" | "lost";

const fetcher = (url: string) =>
  fetch(url).then((r) => {
    if (!r.ok) throw new Error("fetch_failed");
    return r.json();
  });

export default function SalesDealsPage() {
  const [tab, setTab] = useState<Tab>("needs_action");
  const [query, setQuery] = useState("");
  const [showNew, setShowNew] = useState(false);

  const { data, error, isLoading, mutate } = useSWR<DealRow[]>(
    "/api/sales/deals/runs?limit=200",
    fetcher,
    { refreshInterval: 15_000 },
  );

  const rows = data ?? [];
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return rows.filter((r) => {
      if (tab === "needs_action" && !NEEDS_REP.has(r.status)) return false;
      if (tab === "active" && TERMINAL.has(r.status)) return false;
      if (tab === "won" && r.status !== "closed_won") return false;
      if (tab === "lost" && (r.status !== "closed_lost" && r.status !== "no_reply_closed")) return false;
      if (!q) return true;
      return (
        r.company_name.toLowerCase().includes(q) ||
        (r.contact_name ?? "").toLowerCase().includes(q) ||
        (r.contact_email ?? "").toLowerCase().includes(q)
      );
    });
  }, [rows, tab, query]);

  const counts = useMemo(
    () => ({
      all: rows.length,
      needs_action: rows.filter((r) => NEEDS_REP.has(r.status)).length,
      active: rows.filter((r) => !TERMINAL.has(r.status)).length,
      won: rows.filter((r) => r.status === "closed_won").length,
      lost: rows.filter((r) => r.status === "closed_lost" || r.status === "no_reply_closed").length,
    }),
    [rows],
  );

  return (
    <div className="container mx-auto max-w-6xl space-y-6 p-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Sales deals</h1>
          <p className="text-sm text-muted-foreground">
            Autonomous deal pipeline — research, outreach, proposals, close.
          </p>
        </div>
        <Button onClick={() => setShowNew(true)}>
          <Plus className="mr-2 h-4 w-4" />
          New deal
        </Button>
      </header>

      <div className="flex items-center gap-2">
        {(
          [
            ["needs_action", "Needs your attention"],
            ["active", "Active"],
            ["all", "All"],
            ["won", "Won"],
            ["lost", "Lost"],
          ] as const
        ).map(([key, label]) => (
          <Button
            key={key}
            variant={tab === key ? "default" : "outline"}
            size="sm"
            onClick={() => setTab(key)}
          >
            {label}
            <span className="ml-2 text-xs opacity-70">{counts[key]}</span>
          </Button>
        ))}
        <div className="ml-auto flex items-center gap-2">
          <Search className="h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search company, contact, email"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="w-72"
          />
        </div>
      </div>

      {error ? (
        <div className="rounded-md border border-red-500/30 bg-red-500/5 p-4 text-sm text-red-600">
          Failed to load deals. {String(error)}
        </div>
      ) : null}

      {isLoading ? (
        <div className="space-y-2">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-20" />
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <div className="rounded-md border border-dashed p-10 text-center text-sm text-muted-foreground">
          No deals match. Create a new one to get started.
        </div>
      ) : (
        <div className="overflow-hidden rounded-lg border">
          <table className="w-full text-sm">
            <thead className="bg-muted/50 text-left text-xs uppercase tracking-wide text-muted-foreground">
              <tr>
                <th className="px-4 py-3">Company</th>
                <th className="px-4 py-3">Contact</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3">ICP</th>
                <th className="px-4 py-3">Touches</th>
                <th className="px-4 py-3">Updated</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((r) => (
                <tr key={r.id} className="border-t hover:bg-muted/30">
                  <td className="px-4 py-3">
                    <Link href={`/sales/deals/${r.id}`} className="font-medium hover:underline">
                      {r.company_name}
                    </Link>
                    {r.blocked_reason ? (
                      <div className="mt-1 flex items-center gap-1 text-xs text-amber-600">
                        <AlertTriangle className="h-3 w-3" />
                        {r.blocked_reason}
                      </div>
                    ) : null}
                  </td>
                  <td className="px-4 py-3">
                    <div>{r.contact_name ?? "—"}</div>
                    <div className="text-xs text-muted-foreground">{r.contact_email ?? ""}</div>
                  </td>
                  <td className="px-4 py-3">
                    <Badge variant={NEEDS_REP.has(r.status) ? "default" : "secondary"}>
                      {STATUS_LABELS[r.status] ?? r.status}
                    </Badge>
                  </td>
                  <td className="px-4 py-3">{r.icp_score ?? "—"}</td>
                  <td className="px-4 py-3 text-xs text-muted-foreground">
                    {r.followup_count > 0 ? `${r.followup_count} follow-up(s)` : "—"}
                    {r.checkin_count > 0 ? ` · ${r.checkin_count} check-in(s)` : ""}
                  </td>
                  <td className="px-4 py-3 text-xs text-muted-foreground">
                    {new Date(r.updated_at).toLocaleString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <NewDealDialog
        open={showNew}
        onOpenChange={setShowNew}
        onCreated={() => {
          setShowNew(false);
          mutate();
        }}
      />
    </div>
  );
}
