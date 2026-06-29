"use client";

import { Badge } from "@/components/ui/badge";

export const SALES_STATUS_LABELS: Record<string, string> = {
  lead_entered: "New lead",
  researching: "Researching",
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

const ATTENTION = new Set([
  "outreach_pending_rep_review",
  "followup_pending_rep_review",
  "checkin_pending_rep_review",
  "proposal_pending_rep_review",
  "awaiting_next_step_decision",
  "at_risk",
  "blocked_missing_icp_doc",
  "blocked_missing_template",
  "failed",
]);

const SUCCESS = new Set(["closed_won"]);
const FAIL = new Set(["closed_lost", "no_reply_closed", "cancelled"]);

export function SalesStatusBadge({ status }: { status: string }) {
  const label = SALES_STATUS_LABELS[status] ?? status;
  let variant: "default" | "secondary" | "destructive" | "outline" = "secondary";
  if (ATTENTION.has(status)) variant = "default";
  else if (SUCCESS.has(status)) variant = "outline";
  else if (FAIL.has(status)) variant = "destructive";
  return <Badge variant={variant}>{label}</Badge>;
}
