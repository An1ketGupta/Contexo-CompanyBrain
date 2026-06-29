"use client";

import { use, useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import {
  AlertTriangle,
  ArrowLeft,
  Award,
  CheckCircle2,
  Loader2,
  MailCheck,
  RotateCw,
  Trophy,
  XCircle,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { SalesStatusBadge, SALES_STATUS_LABELS } from "@/components/sales-agent/status-badge";
import { OutreachReview } from "@/components/sales-agent/outreach-review";
import { MeetingActions } from "@/components/sales-agent/meeting-actions";
import { ProposalReview } from "@/components/sales-agent/proposal-review";

interface DealDoc {
  id: string;
  kind: string;
  storage_path: string;
  pdf_storage_path: string | null;
  signed_url: string | null;
  pdf_signed_url: string | null;
  source: string;
  revision: number;
  sign_status: string;
  created_at: string;
}

interface DealEvent {
  id: string;
  actor_kind: string;
  event_type: string;
  message: string | null;
  from_status: string | null;
  to_status: string | null;
  created_at: string;
}

interface DealResearch {
  id: string;
  source: string;
  company_summary: string | null;
  industry: string | null;
  headcount_estimate: string | null;
  tech_stack_hints: string[] | null;
  pain_point_hypothesis: string | null;
  competitors_detected: string[] | null;
  created_at: string;
}

interface PrepBrief {
  agenda?: string[];
  talking_points?: string[];
  objections?: { objection: string; response: string }[];
  case_studies?: { title: string; takeaway: string }[];
  pricing_scenarios?: string[];
  questions_to_ask?: string[];
  risks?: string[];
}

interface DealDetail {
  id: string;
  company_name: string;
  company_website: string | null;
  company_industry: string | null;
  contact_name: string | null;
  contact_title: string | null;
  contact_email: string | null;
  status: string;
  blocked_reason: string | null;
  blocked_template_kind: string | null;
  icp_score: number | null;
  icp_rationale: string | null;
  outreach_subject: string | null;
  outreach_email_body: string | null;
  outreach_linkedin_body: string | null;
  outreach_sent_at: string | null;
  reply_received_at: string | null;
  meeting_at: string | null;
  meeting_count: number;
  prep_brief: PrepBrief | null;
  call_summary: string | null;
  bant_json: Record<string, string | null> | null;
  objections_json: { objection: string; rep_response: string | null }[] | null;
  next_steps_json: { action: string; owner: string; due_hint: string | null }[] | null;
  recommended_stage: string | null;
  proposal_sent_at: string | null;
  closed_at: string | null;
  close_outcome: string | null;
  close_reason: string | null;
  close_notes: string | null;
  deal_value_amount: number | null;
  deal_value_currency: string | null;
  followup_count: number;
  checkin_count: number;
  latest_research: DealResearch | null;
  documents: DealDoc[];
  events: DealEvent[];
  created_at: string;
  updated_at: string;
}

const fetcher = (url: string) =>
  fetch(url).then((r) => {
    if (!r.ok) throw new Error("fetch_failed");
    return r.json();
  });

export default function DealDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data, error, isLoading, mutate } = useSWR<DealDetail>(
    `/api/sales/deals/runs/${id}`,
    fetcher,
    { refreshInterval: 10_000 },
  );

  if (isLoading) {
    return (
      <div className="container mx-auto max-w-5xl space-y-4 p-6">
        <Skeleton className="h-10 w-72" />
        <Skeleton className="h-48" />
        <Skeleton className="h-64" />
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="container mx-auto max-w-3xl p-6">
        <div className="rounded-md border border-red-500/30 bg-red-500/5 p-4 text-sm text-red-600">
          Failed to load deal. {error ? String(error) : "Not found."}
        </div>
      </div>
    );
  }

  return (
    <div className="container mx-auto max-w-5xl space-y-6 p-6">
      <Link
        href="/sales/deals"
        className="inline-flex items-center text-sm text-muted-foreground hover:underline"
      >
        <ArrowLeft className="mr-1 h-4 w-4" />
        All deals
      </Link>

      <Header deal={data} onMutated={() => mutate()} />

      {data.blocked_reason ? (
        <div className="flex items-center gap-2 rounded-md border border-amber-500/30 bg-amber-500/5 p-3 text-sm text-amber-700">
          <AlertTriangle className="h-4 w-4" />
          {data.blocked_reason}
        </div>
      ) : null}

      <StagePanel deal={data} onMutated={() => mutate()} />

      <ResearchPanel research={data.latest_research} icpScore={data.icp_score} icpRationale={data.icp_rationale} />

      {data.prep_brief ? <PrepBriefPanel brief={data.prep_brief} /> : null}
      {data.call_summary ? (
        <CallSummaryPanel
          summary={data.call_summary}
          bant={data.bant_json}
          objections={data.objections_json}
          nextSteps={data.next_steps_json}
        />
      ) : null}

      <DocumentsPanel docs={data.documents} />
      <Timeline events={data.events} />
    </div>
  );
}

function Header({ deal, onMutated }: { deal: DealDetail; onMutated: () => void }) {
  const [resuming, setResuming] = useState(false);
  async function resume() {
    setResuming(true);
    try {
      await fetch(`/api/sales/deals/runs/${deal.id}/resume`, { method: "POST" });
      onMutated();
    } finally {
      setResuming(false);
    }
  }
  return (
    <header className="flex items-start justify-between gap-4">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold">{deal.company_name}</h1>
        <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
          <SalesStatusBadge status={deal.status} />
          {deal.contact_name ? (
            <span>
              · {deal.contact_name}
              {deal.contact_title ? `, ${deal.contact_title}` : ""}
            </span>
          ) : null}
          {deal.contact_email ? <span>· {deal.contact_email}</span> : null}
          {deal.company_industry ? <span>· {deal.company_industry}</span> : null}
        </div>
      </div>
      <div className="flex items-center gap-2">
        <Button variant="outline" size="sm" onClick={resume} disabled={resuming}>
          {resuming ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : <RotateCw className="mr-1 h-3 w-3" />}
          Re-drive agent
        </Button>
      </div>
    </header>
  );
}

function StagePanel({ deal, onMutated }: { deal: DealDetail; onMutated: () => void }) {
  const s = deal.status;

  if (
    s === "outreach_pending_rep_review" ||
    s === "followup_pending_rep_review" ||
    s === "checkin_pending_rep_review"
  ) {
    return (
      <OutreachReview
        dealId={deal.id}
        initialSubject={deal.outreach_subject ?? ""}
        initialBody={deal.outreach_email_body ?? ""}
        initialLinkedIn={deal.outreach_linkedin_body ?? null}
        contactEmail={deal.contact_email}
        variant={
          s === "outreach_pending_rep_review"
            ? "cold"
            : s === "followup_pending_rep_review"
              ? "followup"
              : "checkin"
        }
        onMutated={onMutated}
      />
    );
  }

  if (s === "outreach_sent" || s === "awaiting_reply") {
    return <AwaitingReplyPanel deal={deal} onMutated={onMutated} />;
  }

  if (s === "meeting_booked" || s === "prep_generating" || s === "prep_ready") {
    return <MeetingActions dealId={deal.id} hasPrepReady={s === "prep_ready"} onMutated={onMutated} />;
  }

  if (s === "call_summarizing") {
    return (
      <div className="flex items-center gap-2 rounded-lg border p-4 text-sm">
        <Loader2 className="h-4 w-4 animate-spin" />
        Summarizing call — this usually takes 10–30 seconds.
      </div>
    );
  }

  if (s === "awaiting_next_step_decision") {
    return <NextStepDecision deal={deal} onMutated={onMutated} />;
  }

  if (s === "proposal_drafting") {
    return (
      <div className="flex items-center gap-2 rounded-lg border p-4 text-sm">
        <Loader2 className="h-4 w-4 animate-spin" />
        Drafting proposal — pulling KB context and rendering DOCX.
      </div>
    );
  }

  if (s === "proposal_pending_rep_review") {
    return <ProposalReview dealId={deal.id} documents={deal.documents} onMutated={onMutated} />;
  }

  if (s === "proposal_sent" || s === "awaiting_decision" || s === "at_risk") {
    return <AwaitingDecisionPanel deal={deal} onMutated={onMutated} />;
  }

  if (s === "closed_won" || s === "closed_lost" || s === "no_reply_closed") {
    return (
      <div className="rounded-lg border p-4">
        <div className="flex items-center gap-2 text-lg font-semibold">
          {deal.close_outcome === "won" ? (
            <>
              <Trophy className="h-5 w-5 text-emerald-600" />
              Won
            </>
          ) : (
            <>
              <XCircle className="h-5 w-5 text-red-600" />
              {SALES_STATUS_LABELS[s]}
            </>
          )}
        </div>
        {deal.close_reason ? (
          <p className="mt-2 text-sm">{deal.close_reason}</p>
        ) : null}
        {deal.close_notes ? (
          <p className="mt-1 text-sm text-muted-foreground">{deal.close_notes}</p>
        ) : null}
      </div>
    );
  }

  return null;
}

function AwaitingReplyPanel({ deal, onMutated }: { deal: DealDetail; onMutated: () => void }) {
  const [marking, setMarking] = useState(false);
  async function markReply() {
    setMarking(true);
    try {
      await fetch(`/api/sales/deals/runs/${deal.id}/reply-received`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      onMutated();
    } finally {
      setMarking(false);
    }
  }
  return (
    <div className="space-y-3 rounded-lg border p-4">
      <h3 className="text-lg font-semibold">Awaiting reply</h3>
      <p className="text-sm text-muted-foreground">
        Follow-ups will be drafted automatically (3-day cadence, max 3). The agent uses Gmail watch
        to detect replies — if it misses one, mark it manually.
      </p>
      <div className="text-sm">
        Outreach sent: {deal.outreach_sent_at ? new Date(deal.outreach_sent_at).toLocaleString() : "—"}
        <br />
        Follow-ups sent: {deal.followup_count}
      </div>
      <Button variant="outline" onClick={markReply} disabled={marking}>
        {marking ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <MailCheck className="mr-2 h-4 w-4" />}
        I got a reply
      </Button>
    </div>
  );
}

function NextStepDecision({ deal, onMutated }: { deal: DealDetail; onMutated: () => void }) {
  const [submitting, setSubmitting] = useState<null | "proposal" | "another_call">(null);
  const [notes, setNotes] = useState("");
  async function choose(decision: "proceed_to_proposal" | "schedule_another_call") {
    setSubmitting(decision === "proceed_to_proposal" ? "proposal" : "another_call");
    try {
      await fetch(`/api/sales/deals/runs/${deal.id}/next-step`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision, notes: notes || null }),
      });
      onMutated();
    } finally {
      setSubmitting(null);
    }
  }
  return (
    <div className="space-y-3 rounded-lg border p-4">
      <h3 className="text-lg font-semibold">What&apos;s next?</h3>
      {deal.recommended_stage ? (
        <p className="text-sm text-muted-foreground">
          Agent recommends:{" "}
          <span className="font-medium">
            {deal.recommended_stage === "propose"
              ? "Send proposal"
              : deal.recommended_stage === "another_call"
                ? "Schedule another call"
                : "Qualify out"}
          </span>
        </p>
      ) : null}
      <Textarea
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        rows={2}
        placeholder="Notes (optional)"
      />
      <div className="flex flex-wrap gap-2">
        <Button onClick={() => choose("proceed_to_proposal")} disabled={submitting !== null}>
          {submitting === "proposal" ? (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          ) : (
            <Award className="mr-2 h-4 w-4" />
          )}
          Proceed to proposal
        </Button>
        <Button
          variant="outline"
          onClick={() => choose("schedule_another_call")}
          disabled={submitting !== null}
        >
          Schedule another call
        </Button>
      </div>
    </div>
  );
}

function AwaitingDecisionPanel({ deal, onMutated }: { deal: DealDetail; onMutated: () => void }) {
  const [outcome, setOutcome] = useState<"won" | "lost">("won");
  const [reason, setReason] = useState("");
  const [notes, setNotes] = useState("");
  const [finalValue, setFinalValue] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function closeDeal() {
    if (!reason.trim()) {
      setError("Reason is required.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const res = await fetch(`/api/sales/deals/runs/${deal.id}/close`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          outcome,
          reason,
          notes: notes || null,
          final_deal_value: finalValue ? Number(finalValue) : null,
        }),
      });
      if (!res.ok) throw new Error(`Failed: ${res.status}`);
      onMutated();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-4 rounded-lg border p-4">
      <header>
        <h3 className="text-lg font-semibold">Awaiting prospect decision</h3>
        <p className="text-sm text-muted-foreground">
          Check-in nudges auto-draft on a 4-day cadence (max 2). Close manually when you have an
          outcome.
        </p>
      </header>
      <div className="text-sm">
        Proposal sent: {deal.proposal_sent_at ? new Date(deal.proposal_sent_at).toLocaleString() : "—"}
        <br />
        Check-ins sent: {deal.checkin_count}
      </div>

      <div className="grid gap-3 border-t pt-4">
        <div className="flex items-center gap-2">
          <Button
            variant={outcome === "won" ? "default" : "outline"}
            size="sm"
            onClick={() => setOutcome("won")}
          >
            <CheckCircle2 className="mr-1 h-3 w-3" />
            Won
          </Button>
          <Button
            variant={outcome === "lost" ? "destructive" : "outline"}
            size="sm"
            onClick={() => setOutcome("lost")}
          >
            <XCircle className="mr-1 h-3 w-3" />
            Lost
          </Button>
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="reason">Reason *</Label>
          <Input id="reason" value={reason} onChange={(e) => setReason(e.target.value)} />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="final-value">Final deal value (optional)</Label>
          <Input
            id="final-value"
            type="number"
            min={0}
            value={finalValue}
            onChange={(e) => setFinalValue(e.target.value)}
          />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="close-notes">Notes</Label>
          <Textarea
            id="close-notes"
            rows={2}
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
          />
        </div>
        {error ? <p className="text-sm text-red-600">{error}</p> : null}
        <div className="flex justify-end">
          <Button onClick={closeDeal} disabled={saving}>
            {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
            Close deal as {outcome}
          </Button>
        </div>
      </div>
    </div>
  );
}

function ResearchPanel({
  research,
  icpScore,
  icpRationale,
}: {
  research: DealResearch | null;
  icpScore: number | null;
  icpRationale: string | null;
}) {
  if (!research && !icpScore) return null;
  return (
    <section className="rounded-lg border p-4">
      <h2 className="text-lg font-semibold">Research</h2>
      {icpScore !== null ? (
        <div className="mt-2 text-sm">
          <span className="font-medium">ICP fit: {icpScore}/10.</span>{" "}
          {icpRationale ? <span className="text-muted-foreground">{icpRationale}</span> : null}
        </div>
      ) : null}
      {research ? (
        <dl className="mt-3 grid grid-cols-2 gap-3 text-sm">
          {research.company_summary ? (
            <div className="col-span-2">
              <dt className="font-medium">Summary</dt>
              <dd className="text-muted-foreground">{research.company_summary}</dd>
            </div>
          ) : null}
          {research.industry ? (
            <div>
              <dt className="font-medium">Industry</dt>
              <dd className="text-muted-foreground">{research.industry}</dd>
            </div>
          ) : null}
          {research.headcount_estimate ? (
            <div>
              <dt className="font-medium">Size</dt>
              <dd className="text-muted-foreground">{research.headcount_estimate}</dd>
            </div>
          ) : null}
          {research.tech_stack_hints?.length ? (
            <div className="col-span-2">
              <dt className="font-medium">Tech stack hints</dt>
              <dd className="text-muted-foreground">{research.tech_stack_hints.join(", ")}</dd>
            </div>
          ) : null}
          {research.pain_point_hypothesis ? (
            <div className="col-span-2">
              <dt className="font-medium">Pain point hypothesis</dt>
              <dd className="text-muted-foreground">{research.pain_point_hypothesis}</dd>
            </div>
          ) : null}
          {research.competitors_detected?.length ? (
            <div className="col-span-2">
              <dt className="font-medium">Competitors detected</dt>
              <dd className="text-muted-foreground">{research.competitors_detected.join(", ")}</dd>
            </div>
          ) : null}
        </dl>
      ) : null}
    </section>
  );
}

function PrepBriefPanel({ brief }: { brief: PrepBrief }) {
  return (
    <section className="rounded-lg border p-4">
      <h2 className="text-lg font-semibold">Meeting prep brief</h2>
      {brief.agenda?.length ? <BulletList title="Agenda" items={brief.agenda} /> : null}
      {brief.talking_points?.length ? (
        <BulletList title="Talking points" items={brief.talking_points} />
      ) : null}
      {brief.questions_to_ask?.length ? (
        <BulletList title="Questions to ask" items={brief.questions_to_ask} />
      ) : null}
      {brief.objections?.length ? (
        <div className="mt-3">
          <h3 className="text-sm font-medium">Likely objections</h3>
          <ul className="mt-1 space-y-2 text-sm">
            {brief.objections.map((o, i) => (
              <li key={i}>
                <span className="font-medium">{o.objection}</span> —{" "}
                <span className="text-muted-foreground">{o.response}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {brief.case_studies?.length ? (
        <div className="mt-3">
          <h3 className="text-sm font-medium">Case studies</h3>
          <ul className="mt-1 space-y-2 text-sm">
            {brief.case_studies.map((c, i) => (
              <li key={i}>
                <span className="font-medium">{c.title}</span> —{" "}
                <span className="text-muted-foreground">{c.takeaway}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {brief.risks?.length ? <BulletList title="Risks" items={brief.risks} /> : null}
    </section>
  );
}

function BulletList({ title, items }: { title: string; items: string[] }) {
  return (
    <div className="mt-3">
      <h3 className="text-sm font-medium">{title}</h3>
      <ul className="mt-1 list-inside list-disc text-sm text-muted-foreground">
        {items.map((it, i) => (
          <li key={i}>{it}</li>
        ))}
      </ul>
    </div>
  );
}

function CallSummaryPanel({
  summary,
  bant,
  objections,
  nextSteps,
}: {
  summary: string;
  bant: Record<string, string | null> | null;
  objections: { objection: string; rep_response: string | null }[] | null;
  nextSteps: { action: string; owner: string; due_hint: string | null }[] | null;
}) {
  return (
    <section className="rounded-lg border p-4">
      <h2 className="text-lg font-semibold">Last call summary</h2>
      <p className="mt-2 text-sm">{summary}</p>
      {bant ? (
        <dl className="mt-3 grid grid-cols-2 gap-2 text-sm">
          {(["budget", "authority", "need", "timeline"] as const).map((k) =>
            bant[k] ? (
              <div key={k}>
                <dt className="font-medium capitalize">{k}</dt>
                <dd className="text-muted-foreground">{bant[k]}</dd>
              </div>
            ) : null,
          )}
        </dl>
      ) : null}
      {objections?.length ? (
        <div className="mt-3">
          <h3 className="text-sm font-medium">Objections</h3>
          <ul className="mt-1 space-y-1 text-sm">
            {objections.map((o, i) => (
              <li key={i}>
                <span className="font-medium">{o.objection}</span>
                {o.rep_response ? (
                  <span className="text-muted-foreground"> — {o.rep_response}</span>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {nextSteps?.length ? (
        <div className="mt-3">
          <h3 className="text-sm font-medium">Agreed next steps</h3>
          <ul className="mt-1 list-inside list-disc text-sm text-muted-foreground">
            {nextSteps.map((s, i) => (
              <li key={i}>
                [{s.owner}] {s.action}
                {s.due_hint ? ` (${s.due_hint})` : ""}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}

function DocumentsPanel({ docs }: { docs: DealDoc[] }) {
  if (docs.length === 0) return null;
  return (
    <section className="rounded-lg border p-4">
      <h2 className="text-lg font-semibold">Documents</h2>
      <ul className="mt-2 space-y-2 text-sm">
        {docs.map((d) => (
          <li key={d.id} className="flex items-center justify-between">
            <div>
              <span className="font-medium">{d.kind}</span> v{d.revision}{" "}
              <span className="text-xs text-muted-foreground">
                ({d.source === "agent_generated" ? "agent" : "rep upload"})
              </span>
            </div>
            <div className="flex items-center gap-3">
              {d.pdf_signed_url ? (
                <a href={d.pdf_signed_url} target="_blank" rel="noreferrer" className="underline">
                  PDF
                </a>
              ) : null}
              {d.signed_url && d.signed_url !== d.pdf_signed_url ? (
                <a href={d.signed_url} target="_blank" rel="noreferrer" className="underline">
                  DOCX
                </a>
              ) : null}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Timeline({ events }: { events: DealEvent[] }) {
  if (!events.length) return null;
  return (
    <section className="rounded-lg border p-4">
      <h2 className="text-lg font-semibold">Timeline</h2>
      <ol className="mt-2 space-y-2 text-sm">
        {events.map((e) => (
          <li key={e.id} className="flex items-start gap-3 border-l pl-3">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <span className="font-medium uppercase tracking-wide">{e.actor_kind}</span>
                <span>·</span>
                <span>{new Date(e.created_at).toLocaleString()}</span>
              </div>
              <div className="text-sm">
                <span className="font-medium">{e.event_type}</span>
                {e.message ? <span className="ml-1 text-muted-foreground">— {e.message}</span> : null}
              </div>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
