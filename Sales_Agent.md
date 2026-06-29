# NirnayaIQ — Sales Agent Pipeline Roadmap (4 Days)

**Source:** Reconstructed from a Claude Code terminal session that designed the Sales Agent pipeline in the same pattern as the existing Onboarding Agent. The original transcript had several truncated/garbled lines (terminal wrapping artifacts) — every place this roadmap fills a gap is marked with ⚠️ **RECONSTRUCTED** so Claude Code can sanity-check against actual intent before building.

**Confirmed Architecture (do not re-litigate mid-implementation):**
- Stack: same as NirnayaIQ core — FastAPI, Supabase (Postgres + RLS + pgvector), Redis, Inngest, Resend, Langfuse, Railway, Gemini. `uv` for Python, `pnpm` for frontend.
- Pattern: **bespoke implementation** that copies the Onboarding Agent's *pattern* (status-column state machine, re-entrant via Inngest events, human-in-the-loop gates) — this is a fresh build, not a refactor or extension of onboarding's actual code/abstractions. Do not introduce a shared `AgentPipeline` base class linking the two.
- Approvals: **dedicated `deal_approvals` table**, not the existing `approval_requests` table used elsewhere. Reasoning: deal gates need draft → edit → approve tracking (revision count, edited text, edited file path), which is a different shape than a generic approve/reject queue, and unifying with an already-unconfirmed-schema table compounds risk for no shared benefit here.
- Lead research (Stage 1): **KB search first, live web fallback second.** Only fall back to web search/fetch if KB search returns no usable signal on the company. This keeps the common case fast and cheap, and avoids unnecessary external calls.
- Follow-up cadence: Day 3 → follow-up #1. ⚠️ **RECONSTRUCTED:** the original transcript only specified the 3-day trigger for follow-up #1 and a max of 3 follow-ups; spacing for #2 and #3 was not specified. This roadmap defaults to a flat 3-day interval between all three (Day 3, Day 6, Day 9 from outreach_sent), then `no_reply_closed`. Confirm this spacing assumption before Day 3 implementation — trivial to change (single config constant) if you want decreasing/increasing intervals instead.
- Re-entrancy: a deal can loop back into `meeting_booked` → `prep_generating` → `prep_ready` multiple times (multi-call deals). The status machine and `deal_events` audit log must support this — `meeting_booked` is not a terminal state, and stage-5/6 transitions must be re-triggerable per meeting, not just once per deal.
- Every "Backend Changes" section assumes the existing FastAPI app structure (`routers/`, `services/`, `models/`, `inngest/`) used in prior roadmaps (Agent Roadmap V1, Agent2_Roadmap). Adjust paths if the real structure differs, but preserve the logical separation.

**⚠️ Reconciliation step before Day 1 starts:** confirm whether an `agents` or `pipeline_runs`-style shared table already exists for the Onboarding Agent that this should NOT collide with:
```bash
grep -rln "onboarding" --include="*.sql" --include="*.py" . | head -30
grep -rn "CREATE TABLE.*runs" supabase/migrations/
```
Use this to confirm naming conventions (e.g. does onboarding use `hire_runs` or similar?) so `deal_runs` follows the same convention, and to confirm there's no accidental table name collision.

---

## Full Status Flow (reconstructed, gaps filled)

```
lead_entered
  ↓
researching ─────────────────────────── [agent: KB search + web fallback for company/contact info]
  ↓
icp_scoring ──────────────────────────── [agent scores against ICP doc from KB]
  ↓
icp_scored
  ↓ (blocked_missing_icp_doc if no ICP doc in KB — halts here until doc uploaded)
outreach_drafting ────────────────────── [agent drafts cold email + LinkedIn msg using KB: value props, case studies]
  ↓
outreach_pending_rep_review ──────────── [HUMAN GATE: rep edits / approves]
  ↓
outreach_sent ────────────────────────── [email logged, waiting for reply]
  ↓
awaiting_reply ───────────────────────── [cron follow-up reminders if no reply in N days; loops on itself up to 3x]
  ↓ (no_reply_closed if 3 follow-ups exhausted with no reply)
  ↓ (on reply received)
meeting_booked ───────────────────────── [re-entrant: can be revisited multiple times per deal]
  ↓
prep_generating ──────────────────────── [agent: prospect brief + battle cards + talking points]
  ↓
prep_ready ───────────────────────────── [HUMAN GATE: informational only, rep reads before call]
  ↓
call_summarizing ─────────────────────── [agent extracts CRM notes from transcript/notes]
  ↓
call_summarized ──────────────────────── [loops back to meeting_booked if another call is scheduled,
  │                                        OR proceeds to proposal_drafting if ready to propose]
  ↓
proposal_drafting ────────────────────── [agent fills proposal template from KB with deal data]
  ↓
proposal_pending_rep_review ──────────── [HUMAN GATE: rep edits / approves]
  ↓ (blocked_missing_template if no proposal template in KB)
proposal_sent ────────────────────────── [proposal emailed to prospect]
  ↓
awaiting_decision ────────────────────── [cron check-in nudges, up to 2x, then at_risk flag]
  ↓
closed_won | closed_lost ─────────────── [manual rep transition, reason required]
```

**Re-entrancy note:** `meeting_booked` → `call_summarized` is a loop, not a line. After `call_summarized`, the agent (or rep) decides: another meeting needed → back to `meeting_booked`, or ready to propose → forward to `proposal_drafting`. This decision point is itself a small human gate (see Stage 5 below) since it affects deal trajectory.

---

## Core Tables (full DDL)

### `deal_runs` — state machine + timestamps

```sql
CREATE TABLE deal_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  created_by UUID NOT NULL REFERENCES users(id), -- the rep who owns this deal

  -- Lead identity
  company_name TEXT NOT NULL,
  company_website TEXT,
  contact_name TEXT,
  contact_title TEXT,
  contact_email TEXT,

  -- State machine
  status TEXT NOT NULL DEFAULT 'lead_entered' CHECK (status IN (
    'lead_entered', 'researching', 'icp_scoring', 'icp_scored',
    'blocked_missing_icp_doc',
    'outreach_drafting', 'outreach_pending_rep_review', 'outreach_sent',
    'awaiting_reply', 'no_reply_closed',
    'meeting_booked', 'prep_generating', 'prep_ready',
    'call_summarizing', 'call_summarized',
    'proposal_drafting', 'proposal_pending_rep_review',
    'blocked_missing_template',
    'proposal_sent', 'awaiting_decision', 'at_risk',
    'closed_won', 'closed_lost'
  )),
  previous_status TEXT, -- for resuming blocked_* states back to where they came from

  -- ICP scoring
  icp_score INT CHECK (icp_score BETWEEN 1 AND 10),
  icp_rationale TEXT,

  -- Outreach
  outreach_draft TEXT,
  outreach_linkedin_draft TEXT,
  outreach_edited_at TIMESTAMPTZ,
  outreach_revision INT NOT NULL DEFAULT 0,
  outreach_sent_at TIMESTAMPTZ,

  -- Follow-ups
  followup_count INT NOT NULL DEFAULT 0,
  last_followup_at TIMESTAMPTZ,

  -- Meeting / prep (most recent meeting's data; full history in deal_events)
  meeting_at TIMESTAMPTZ,
  meeting_count INT NOT NULL DEFAULT 0, -- increments each time meeting_booked is re-entered
  prep_generated_at TIMESTAMPTZ,

  -- Call summary (most recent; full history in deal_events)
  call_transcript TEXT,
  call_summary TEXT,
  bant_json JSONB, -- {budget, authority, need, timeline}
  next_steps_json JSONB, -- [{step, owner, due_date}]
  recommended_stage TEXT, -- 'MQL' | 'SQL' | 'Proposal' | 'Negotiation'

  -- Proposal
  proposal_draft_path TEXT, -- storage path to generated .docx
  proposal_edited_path TEXT, -- storage path if rep uploads an edited version
  proposal_revision INT NOT NULL DEFAULT 0,
  proposal_sent_at TIMESTAMPTZ,

  -- Decision / close
  checkin_count INT NOT NULL DEFAULT 0,
  last_checkin_at TIMESTAMPTZ,
  closed_at TIMESTAMPTZ,
  close_reason TEXT, -- required when status is closed_won/closed_lost

  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_deal_runs_org_status ON deal_runs(org_id, status);
CREATE INDEX idx_deal_runs_awaiting_reply ON deal_runs(status, last_followup_at) WHERE status = 'awaiting_reply';
CREATE INDEX idx_deal_runs_awaiting_decision ON deal_runs(status, last_checkin_at) WHERE status = 'awaiting_decision';

ALTER TABLE deal_runs ENABLE ROW LEVEL SECURITY;
CREATE POLICY deal_runs_org_isolation ON deal_runs
  USING (org_id = (SELECT org_id FROM users WHERE id = auth.uid()));
```

**⚠️ Reconciliation:** confirm the RLS pattern `org_id = (SELECT org_id FROM users WHERE id = auth.uid())` matches existing convention exactly (per Agent2_Roadmap's same note) before applying this migration.

### `deal_documents` — proposals, SOWs, pricing sheets

```sql
CREATE TABLE deal_documents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  deal_run_id UUID NOT NULL REFERENCES deal_runs(id) ON DELETE CASCADE,
  org_id UUID NOT NULL REFERENCES organizations(id),
  kind TEXT NOT NULL CHECK (kind IN ('proposal', 'sow', 'pricing_sheet')),
  storage_path TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'agent_generated' CHECK (source IN ('agent_generated', 'rep_uploaded')),
  revision INT NOT NULL DEFAULT 1,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_deal_documents_deal ON deal_documents(deal_run_id, kind, revision DESC);
ALTER TABLE deal_documents ENABLE ROW LEVEL SECURITY;
CREATE POLICY deal_documents_org_isolation ON deal_documents
  USING (org_id = (SELECT org_id FROM users WHERE id = auth.uid()));
```

### `deal_events` — audit timeline

```sql
CREATE TABLE deal_events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  deal_run_id UUID NOT NULL REFERENCES deal_runs(id) ON DELETE CASCADE,
  org_id UUID NOT NULL REFERENCES organizations(id),
  actor_kind TEXT NOT NULL CHECK (actor_kind IN ('agent', 'rep', 'system')),
  actor_id UUID, -- user id if actor_kind = 'rep', null otherwise
  event_type TEXT NOT NULL, -- e.g. 'status_changed', 'outreach_approved', 'meeting_logged', 'followup_sent'
  from_status TEXT,
  to_status TEXT,
  payload JSONB NOT NULL DEFAULT '{}', -- event-specific detail (e.g. meeting notes snapshot)
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_deal_events_deal ON deal_events(deal_run_id, created_at DESC);
ALTER TABLE deal_events ENABLE ROW LEVEL SECURITY;
CREATE POLICY deal_events_org_isolation ON deal_events
  USING (org_id = (SELECT org_id FROM users WHERE id = auth.uid()));
```

### `deal_research` — stored research output

```sql
CREATE TABLE deal_research (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  deal_run_id UUID NOT NULL REFERENCES deal_runs(id) ON DELETE CASCADE,
  org_id UUID NOT NULL REFERENCES organizations(id),
  source TEXT NOT NULL CHECK (source IN ('kb', 'web_fallback')),
  company_summary TEXT,
  headcount_estimate TEXT,
  tech_stack_hints JSONB, -- array of detected technologies
  pain_point_hypothesis TEXT,
  similar_past_deals JSONB, -- [{deal_run_id, company_name, outcome, similarity}]
  raw_sources JSONB, -- URLs/doc IDs used, for traceability
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_deal_research_deal ON deal_research(deal_run_id);
ALTER TABLE deal_research ENABLE ROW LEVEL SECURITY;
CREATE POLICY deal_research_org_isolation ON deal_research
  USING (org_id = (SELECT org_id FROM users WHERE id = auth.uid()));
```

### `deal_approvals` — dedicated human-gate table (the architectural decision)

```sql
CREATE TABLE deal_approvals (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  deal_run_id UUID NOT NULL REFERENCES deal_runs(id) ON DELETE CASCADE,
  org_id UUID NOT NULL REFERENCES organizations(id),
  gate_type TEXT NOT NULL CHECK (gate_type IN ('outreach', 'proposal')),
  draft_content TEXT, -- original agent-generated draft (text for outreach, null for proposal — see edited_path)
  draft_path TEXT, -- storage path for proposal draft (.docx)
  edited_content TEXT, -- rep's inline edit, if outreach
  edited_path TEXT, -- rep's uploaded replacement, if proposal
  revision INT NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'regenerate_requested')),
  decided_by UUID REFERENCES users(id),
  decided_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_deal_approvals_deal ON deal_approvals(deal_run_id, gate_type, revision DESC);
ALTER TABLE deal_approvals ENABLE ROW LEVEL SECURITY;
CREATE POLICY deal_approvals_org_isolation ON deal_approvals
  USING (org_id = (SELECT org_id FROM users WHERE id = auth.uid()));
```

---

## Inngest Events (full table)

| Event | Trigger | Resulting status transition |
|---|---|---|
| `sales/lead-entered` | Rep submits lead form | `lead_entered` → `researching` |
| `sales/research-completed` | Research stage finishes | `researching` → `icp_scoring` → `icp_scored` (or `blocked_missing_icp_doc`) |
| `sales/outreach-approved` | Rep approves outreach draft via API | `outreach_pending_rep_review` → `outreach_sent` |
| `sales/outreach-regenerate-requested` | Rep clicks "regenerate" instead of approve | stays in `outreach_drafting`, re-runs draft generation |
| `sales/reply-received` | Inbound email reply detected (Gmail watch / webhook — ⚠️ **RECONSTRUCTED**, see Stage 3 note) | `awaiting_reply` → `meeting_booked` (assumes reply leads to booking; see Stage 3 detail for the actual two-step path) |
| `sales/meeting-booked` | Rep clicks "meeting booked" or calendar webhook fires | → `meeting_booked`, increments `meeting_count` |
| `sales/transcript-submitted` | Rep pastes call transcript/notes | `call_summarizing` → `call_summarized` |
| `sales/proposal-approved` | Rep approves proposal | `proposal_pending_rep_review` → `proposal_sent` |
| `sales/deal-closed` | Rep marks won/lost | → `closed_won` / `closed_lost` |
| `sales/follow-up-reminders` | Daily cron | scans `awaiting_reply` deals, drafts follow-ups |
| `sales/checkin-reminders` | Daily cron | scans `awaiting_decision` deals, drafts check-ins |
| `sales/template-uploaded` | KB template uploaded matching `template_kind` | resumes any `blocked_missing_icp_doc` / `blocked_missing_template` runs |

**⚠️ RECONSTRUCTED — reply detection:** the original transcript shows `outreach_sent → awaiting_reply` then loops on itself for follow-ups, then jumps straight to `meeting_booked` with no explicit "reply received" event shown. This roadmap adds an explicit `sales/reply-received` event as the missing link, since "no reply" and "got a reply, now what" need different handling and the transcript's arrow from `awaiting_reply` to `meeting_booked` skips a step. Two reasonable implementations:
1. **Manual:** rep marks "reply received" in the dashboard, which immediately surfaces a "Schedule meeting" action — this is the simpler Day 1 implementation.
2. **Automated:** Gmail inbox watch (push notification via Gmail API watch()) detects a reply on the thread and flips status automatically.

This roadmap builds **(1) first** as the baseline (Day 3), with (2) noted as a clean Day-5-if-extended addition — confirm if you want automated detection in scope for these 4 days specifically, otherwise manual-mark is the default.

---

## DAY 1 — Schema, Lead Intake, Research Stage (lead_entered → icp_scored)

**Goal:** Stand up all five core tables, build the lead intake form/endpoint, and implement Stage 1 (Research + ICP Scoring) end to end including the KB-first/web-fallback logic and the missing-ICP-doc block.

### Morning Block: Full Schema Migration

**File:** `supabase/migrations/0XX_sales_agent_core.sql` — contains all five `CREATE TABLE` statements from the "Core Tables" section above, run as a single migration (they're interdependent via foreign keys, so they must land atomically).

Run the reconciliation grep commands from the top of this doc **before** writing this migration file, to confirm naming conventions and avoid collisions.

### Mid-Morning: Lead Intake

**File:** `apps/backend/models/deal.py`

```python
from pydantic import BaseModel, EmailStr
from typing import Optional

class LeadIntakeRequest(BaseModel):
    company_name: str
    company_website: Optional[str] = None
    contact_name: Optional[str] = None
    contact_title: Optional[str] = None
    contact_email: Optional[EmailStr] = None

class DealRunResponse(BaseModel):
    id: str
    status: str
    company_name: str
    icp_score: Optional[int] = None
    icp_rationale: Optional[str] = None
    created_at: str
```

**File:** `apps/backend/routers/deals.py`

```python
@router.post("/deals/runs", response_model=DealRunResponse)
async def create_deal_run(body: LeadIntakeRequest, user=Depends(get_current_user)):
    """Inserts deal_runs row with status='lead_entered'. Logs a deal_events row
    (actor_kind='rep', event_type='lead_submitted'). Emits sales/lead-entered."""
    deal = await create_deal_run_record(body, user)
    await log_deal_event(deal["id"], actor_kind="rep", actor_id=user.id,
                          event_type="lead_submitted", to_status="lead_entered")
    await emit_event("sales/lead-entered", {"deal_run_id": deal["id"], "org_id": user.org_id})
    return deal

@router.get("/deals/runs", response_model=list[DealRunResponse])
async def list_deal_runs(status: Optional[str] = None, org_id: str = Depends(get_current_org)):
    """List view for rep dashboard, optional status filter."""
    ...

@router.get("/deals/runs/{deal_id}")
async def get_deal_run(deal_id: str):
    """Full detail view: deal_runs row + latest deal_research + deal_events timeline
    + any pending deal_approvals."""
    ...
```

**Frontend:** `apps/frontend/app/sales/deals/new/page.tsx` — simple form: company name, website, contact name/title/email. Submit → `POST /deals/runs` → redirect to deal detail view showing live status as it progresses through Stage 1.

### Afternoon Block: Research Stage (Inngest)

**File:** `apps/backend/inngest/deal_research.py`

```python
@inngest_client.create_function(
    fn_id="sales-lead-research",
    trigger=inngest.TriggerEvent(event="sales/lead-entered"),
)
async def research_lead(ctx: inngest.Context, step: inngest.Step):
    deal_id = ctx.event.data["deal_run_id"]
    org_id = ctx.event.data["org_id"]

    await step.run("set-status-researching", lambda: update_deal_status(deal_id, "researching"))

    kb_research = await step.run("kb-research", lambda: research_from_kb(deal_id, org_id))

    # KB-first, web-fallback-second per confirmed architecture
    if not kb_research.get("has_usable_signal"):
        web_research = await step.run("web-fallback-research", lambda: research_from_web(deal_id))
        research = merge_research(kb_research, web_research)
    else:
        research = kb_research

    await step.run("save-research", lambda: save_deal_research(deal_id, org_id, research))

    await step.run("set-status-icp-scoring", lambda: update_deal_status(deal_id, "icp_scoring"))

    icp_doc = await step.run("fetch-icp-doc", lambda: fetch_icp_document(org_id))
    if not icp_doc:
        await step.run("block-missing-icp", lambda: update_deal_status(deal_id, "blocked_missing_icp_doc",
                                                                          previous_status="icp_scoring"))
        await step.run("log-blocked-event", lambda: log_deal_event(
            deal_id, "system", None, "blocked_missing_icp_doc"))
        return  # halts here, resumed by sales/template-uploaded

    score_result = await step.run("score-icp", lambda: score_against_icp(deal_id, research, icp_doc))
    await step.run("save-icp-score", lambda: save_icp_score(deal_id, score_result))
    await step.run("set-status-icp-scored", lambda: update_deal_status(deal_id, "icp_scored"))
    await step.run("emit-research-completed", lambda: emit_event("sales/research-completed",
                                                                    {"deal_run_id": deal_id}))
```

**File:** `apps/backend/services/deal_research_service.py`

```python
async def research_from_kb(deal_id: str, org_id: str) -> dict:
    """1. Run KB search for the company name + website domain (in case prior
    correspondence/notes already exist in the KB — e.g. a past deal or a doc
    mentioning this company).
    2. Run KB search for 'similar past deals' — embed company_summary-equivalent
    fields (industry hints from company name/website) against past deal_research
    rows' company_summary embeddings, OR against case study documents.
    3. Return {has_usable_signal: bool, company_summary, tech_stack_hints,
    pain_point_hypothesis, similar_past_deals, raw_sources}.
    has_usable_signal = True only if KB search returned non-trivial matches —
    a single weak/irrelevant hit should NOT count as usable, to correctly trigger
    web fallback."""
    ...

async def research_from_web(deal_id: str) -> dict:
    """Fallback only. Use web_search-equivalent tool for:
    1. '{company_name} company overview' — company summary, headcount estimate
    2. '{company_name} tech stack' / check website for tech hints (e.g. job postings
       mentioning stack, BuiltWith-style signals if accessible)
    3. LinkedIn public company page search (read-only, no auth required for public data)
    Returns same shape as research_from_kb, source='web_fallback'."""
    ...

async def merge_research(kb: dict, web: dict) -> dict:
    """Combine both — KB fields take precedence where present, web fills gaps.
    raw_sources concatenates both source lists for traceability."""
    ...

async def fetch_icp_document(org_id: str) -> dict | None:
    """KB query for a document tagged template_kind='icp' or similar marker —
    grep existing template tagging convention from Agent2_Roadmap's #33/#37 work
    before assuming a column name."""
    ...

async def score_against_icp(deal_id: str, research: dict, icp_doc: dict) -> dict:
    """Calls Gemini with icp_doc content + research summary, asks for a 1-10 score
    with rationale. Return {score: int, rationale: str}. Langfuse-trace this call
    per the cross-cutting tracing convention."""
    ...

async def save_icp_score(deal_id: str, result: dict) -> None:
    ...
```

### End of Day 1 — Verification Checklist
- [ ] All 5 tables created, RLS confirmed against real org isolation pattern
- [ ] Submitting a lead form correctly creates `deal_runs` row at `lead_entered` and fires the Inngest event
- [ ] A deal with strong KB signal does NOT trigger web fallback (verify via logs/Langfuse trace — fallback step should be skipped)
- [ ] A deal with no KB signal correctly triggers web fallback and merges results
- [ ] A test org with no ICP document in KB correctly halts at `blocked_missing_icp_doc` rather than crashing or silently scoring with no rationale
- [ ] Uploading an ICP-tagged doc and firing `sales/template-uploaded` correctly resumes the blocked deal

---

## DAY 2 — Outreach Drafting + Human Gate + Outreach Sent (icp_scored → outreach_sent)

**Goal:** Agent drafts personalized outreach, rep reviews/edits/approves via the dedicated `deal_approvals` gate, approved outreach gets sent and logged.

### Morning Block: Outreach Drafting

**File:** `apps/backend/inngest/deal_outreach.py`

```python
@inngest_client.create_function(
    fn_id="sales-draft-outreach",
    trigger=inngest.TriggerEvent(event="sales/research-completed"),
)
async def draft_outreach(ctx: inngest.Context, step: inngest.Step):
    deal_id = ctx.event.data["deal_run_id"]

    await step.run("set-status-drafting", lambda: update_deal_status(deal_id, "outreach_drafting"))

    draft = await step.run("generate-outreach-draft", lambda: generate_outreach_draft(deal_id))

    await step.run("create-approval-gate", lambda: create_deal_approval(
        deal_id=deal_id, gate_type="outreach",
        draft_content=draft["email_body"]
    ))
    await step.run("save-outreach-drafts", lambda: save_outreach_drafts(deal_id, draft))
    await step.run("set-status-pending-review", lambda: update_deal_status(
        deal_id, "outreach_pending_rep_review"))
    await step.run("notify-rep", lambda: notify_rep_of_pending_review(deal_id, "outreach"))
```

**File:** `apps/backend/services/deal_outreach_service.py`

```python
async def generate_outreach_draft(deal_id: str) -> dict:
    """1. Fetch deal_research for this deal (industry/pain-point hypothesis).
    2. KB search: value props, relevant case studies (filtered/ranked by relevance
    to the prospect's industry from research), tone guide, objection pre-empts.
    3. Call Gemini: generate cold email (subject + body) tailored using ICP score
    rationale + research. Also generate optional LinkedIn connection message
    (shorter, less formal).
    4. Return {subject, email_body, linkedin_message}. Langfuse-trace."""
    ...

async def save_outreach_drafts(deal_id: str, draft: dict) -> None:
    """Writes outreach_draft (email) and outreach_linkedin_draft to deal_runs."""
    ...

async def create_deal_approval(deal_id: str, gate_type: str, draft_content: str = None,
                                  draft_path: str = None) -> dict:
    """Inserts into deal_approvals with status='pending', revision=1
    (or revision = existing_max + 1 if this is a regenerate)."""
    ...

async def notify_rep_of_pending_review(deal_id: str, gate_type: str) -> None:
    """Reuse existing notification pattern (Slack DM or in-app notification —
    grep for the pattern used in Agent2_Roadmap's admin notifications)."""
    ...
```

### Afternoon Block: Human Gate Endpoints

**File:** `apps/backend/routers/deals.py` (extend)

```python
@router.get("/deals/runs/{deal_id}/outreach")
async def get_outreach_draft(deal_id: str):
    """Returns current draft + approval gate status for rep review UI."""
    ...

@router.patch("/deals/runs/{deal_id}/outreach")
async def edit_outreach_draft(deal_id: str, body: EditOutreachRequest):
    """Rep edits inline. Updates deal_approvals.edited_content,
    deal_runs.outreach_edited_at, increments outreach_revision (on the deal_runs row,
    distinct from deal_approvals.revision which tracks regenerate cycles)."""
    ...

@router.post("/deals/runs/{deal_id}/outreach/approve")
async def approve_outreach(deal_id: str, user=Depends(get_current_user)):
    """1. Marks deal_approvals row status='approved', decided_by, decided_at.
    2. Sends the email (edited_content if present, else draft_content) via the
    existing Gmail send adapter (reuse from Agent2_Roadmap — do not rebuild).
    3. Sets deal_runs.outreach_sent_at = now(), status = 'outreach_sent'.
    4. Logs deal_event (actor_kind='rep', event_type='outreach_approved').
    5. Emits sales/outreach-approved, which a downstream Inngest function picks up
    to immediately transition outreach_sent → awaiting_reply (no human action needed
    for this specific transition — it's automatic bookkeeping, not a real gate)."""
    ...

@router.post("/deals/runs/{deal_id}/outreach/regenerate")
async def regenerate_outreach(deal_id: str):
    """Rep wants a fresh draft instead of editing. Sets deal_approvals.status =
    'regenerate_requested', deal_runs.status back to 'outreach_drafting',
    re-fires sales/research-completed equivalent (or a dedicated
    sales/outreach-regenerate-requested event) to re-run generate_outreach_draft."""
    ...
```

**File:** `apps/backend/inngest/deal_outreach.py` (extend) — the automatic `outreach_sent → awaiting_reply` bookkeeping transition:

```python
@inngest_client.create_function(
    fn_id="sales-outreach-sent-followup",
    trigger=inngest.TriggerEvent(event="sales/outreach-approved"),
)
async def transition_to_awaiting_reply(ctx: inngest.Context, step: inngest.Step):
    deal_id = ctx.event.data["deal_run_id"]
    await step.run("set-awaiting-reply", lambda: update_deal_status(deal_id, "awaiting_reply"))
```

### Frontend

**File:** `apps/frontend/app/sales/deals/[id]/outreach/page.tsx` — shows draft email (editable textarea), LinkedIn message draft (editable), "Approve & Send", "Regenerate", inline edit auto-saves to the PATCH endpoint on blur.

### End of Day 2 — Verification Checklist
- [ ] Drafted outreach correctly reflects research + ICP rationale (spot-check tone/personalization against a known test prospect)
- [ ] Editing the draft persists correctly and is what actually gets sent (not the original draft) on approve
- [ ] Approving sends a real email via the Gmail adapter and the message appears in the sent folder
- [ ] Regenerate produces a genuinely different draft (revision count increments) and does not double-send anything
- [ ] Status correctly lands on `awaiting_reply` immediately after send with no manual step required

---

## DAY 3 — Follow-ups, Reply Handling, Meeting Booked, Prep Generation (awaiting_reply → prep_ready)

**Goal:** Daily follow-up cron with 3-day spacing and 3-follow-up cap, manual reply-received handling (Day 1 implementation per the reconciliation note), meeting booking, and the prep-brief agent stage.

### Morning Block: Follow-up Cron

**File:** `apps/backend/inngest/deal_followups.py`

```python
FOLLOWUP_INTERVAL_DAYS = 3  # ⚠️ RECONSTRUCTED assumption — flat interval for all 3 follow-ups
MAX_FOLLOWUPS = 3

@inngest_client.create_function(
    fn_id="sales-followup-reminders",
    trigger=inngest.TriggerCron(cron="0 9 * * *"),  # daily 9am
)
async def daily_followup_check(ctx: inngest.Context, step: inngest.Step):
    due_deals = await step.run("find-due-followups", lambda: find_deals_due_for_followup(
        interval_days=FOLLOWUP_INTERVAL_DAYS, max_followups=MAX_FOLLOWUPS))

    for deal in due_deals:
        if deal["followup_count"] >= MAX_FOLLOWUPS:
            await step.run(f"close-no-reply-{deal['id']}", lambda d=deal: update_deal_status(
                d["id"], "no_reply_closed"))
            await step.run(f"log-closed-{deal['id']}", lambda d=deal: log_deal_event(
                d["id"], "system", None, "no_reply_closed"))
            continue

        draft = await step.run(f"draft-followup-{deal['id']}", lambda d=deal: generate_followup_draft(d))
        await step.run(f"create-gate-{deal['id']}", lambda d=deal, dr=draft: create_deal_approval(
            deal_id=d["id"], gate_type="outreach", draft_content=dr["email_body"]))
        await step.run(f"notify-rep-{deal['id']}", lambda d=deal: notify_rep_of_pending_review(
            d["id"], "followup"))
```

**File:** `apps/backend/services/deal_followup_service.py`

```python
async def find_deals_due_for_followup(interval_days: int, max_followups: int) -> list[dict]:
    """SQL: status = 'awaiting_reply' AND
    (last_followup_at IS NULL AND outreach_sent_at <= now() - interval_days)
    OR (last_followup_at <= now() - interval_days).
    Includes deals at followup_count >= max_followups so the loop above can close them."""
    ...

async def generate_followup_draft(deal: dict) -> dict:
    """Calls Gemini with: original outreach content, days elapsed, followup_count
    (so follow-up #2 doesn't repeat #1's exact framing — pass prior follow-up text
    as context to avoid repetition). Shorter, lower-pressure tone for later follow-ups."""
    ...
```

Note: follow-ups reuse the **same `outreach` gate_type** in `deal_approvals` (not a new gate type) since they're still outreach-category messages requiring the same review pattern — increment `deal_approvals.revision` per follow-up rather than inventing a `followup` gate_type, to keep the gate model simple per the bespoke-but-not-overengineered principle.

### Mid-Morning: Reply Received (Manual, Day-1 Implementation)

**File:** `apps/backend/routers/deals.py` (extend)

```python
@router.post("/deals/runs/{deal_id}/reply-received")
async def mark_reply_received(deal_id: str, user=Depends(get_current_user)):
    """Manual mark per the confirmed Day-1 approach (vs. automated Gmail watch,
    which is a clean future extension if you want it added later).
    Logs deal_event. Does NOT auto-transition to meeting_booked — surfaces a
    'Schedule meeting' prompt in the UI instead, since reply != meeting confirmed."""
    await log_deal_event(deal_id, "rep", user.id, "reply_received")
    return {"status": "reply_logged", "next_action": "schedule_meeting"}
```

### Afternoon Block: Meeting Booked + Prep Generation

**File:** `apps/backend/routers/deals.py` (extend)

```python
@router.post("/deals/runs/{deal_id}/meeting-booked")
async def mark_meeting_booked(deal_id: str, body: MeetingBookedRequest):
    """body: {meeting_at: datetime}. Sets deal_runs.meeting_at, increments
    meeting_count, status='meeting_booked'. Logs deal_event with payload
    snapshot of meeting_count (for re-entrancy tracking — 1st call, 2nd call, etc).
    Emits sales/meeting-booked."""
    ...
```

**File:** `apps/backend/inngest/deal_prep.py`

```python
@inngest_client.create_function(
    fn_id="sales-generate-prep-brief",
    trigger=inngest.TriggerEvent(event="sales/meeting-booked"),
)
async def generate_prep_brief(ctx: inngest.Context, step: inngest.Step):
    deal_id = ctx.event.data["deal_run_id"]

    await step.run("set-status-prep-generating", lambda: update_deal_status(deal_id, "prep_generating"))

    brief = await step.run("build-prep-brief", lambda: build_meeting_prep_brief(deal_id))

    await step.run("save-prep", lambda: save_prep_brief(deal_id, brief))
    await step.run("set-status-prep-ready", lambda: update_deal_status(deal_id, "prep_ready"))
    await step.run("notify-rep-prep-ready", lambda: notify_rep_prep_ready(deal_id))
    # No approval gate — informational only, per confirmed Stage 4 spec
```

**File:** `apps/backend/services/deal_prep_service.py`

```python
async def build_meeting_prep_brief(deal_id: str) -> dict:
    """1. Fetch deal_runs + latest deal_research for this deal.
    2. Detect known competitor mentions (from research's tech_stack_hints or
    explicit mention in research/notes) — if matched, fetch relevant battle card
    doc from KB (tagged template_kind='battle_card' or similar — grep convention).
    3. Generate via Gemini: 1-page brief — company/contact summary, pain points,
    budget signals (from BANT if a prior call already happened — re-entrant case),
    suggested discovery questions, relevant customer win stories from KB search.
    4. Return structured brief dict for both storage and frontend rendering."""
    ...

async def save_prep_brief(deal_id: str, brief: dict) -> None:
    """Stores on deal_runs.prep_generated_at = now() and a JSONB brief field —
    ⚠️ note: deal_runs DDL above doesn't have a dedicated prep brief column,
    add `prep_brief JSONB` via an ALTER TABLE in this day's migration, or store
    on deal_events as the payload of a 'prep_generated' event if you prefer not
    to widen deal_runs further — pick one and stay consistent."""
    ...
```

**Migration addition:** `supabase/migrations/0XX_deal_prep_brief.sql`
```sql
ALTER TABLE deal_runs ADD COLUMN prep_brief JSONB;
```

### Frontend

**File:** `apps/frontend/app/sales/deals/[id]/prep/page.tsx` — read-only prep brief view (no approve/reject UI, per informational-only spec), prominent "Mark meeting complete → Summarize call" CTA leading into Day 4's Stage 5.

### End of Day 3 — Verification Checklist
- [ ] Follow-up cron correctly identifies only deals exactly at the 3/6/9-day marks, not all `awaiting_reply` deals
- [ ] A deal that exhausts 3 follow-ups with no reply correctly lands on `no_reply_closed`, not stuck in a loop
- [ ] Marking reply received does NOT auto-create a meeting — confirms manual-mark design intent
- [ ] Booking a meeting on a deal that already had one before (re-entrant case) correctly increments `meeting_count` rather than overwriting silently
- [ ] Prep brief correctly surfaces a battle card when research detected a known competitor, and omits that section cleanly when none is detected
- [ ] Prep stage requires no rep approval action to proceed — confirmed informational-only

---

## DAY 4 — Call Summarization, Proposal Generation, Decision Tracking, Close (call_summarizing → closed_won/closed_lost)

**Goal:** Finish the pipeline — transcript-to-CRM-notes extraction, the re-entrancy decision point (another call vs. propose), proposal generation through its own human gate, awaiting-decision check-ins, and final close.

### Morning Block: Call Summarization

**File:** `apps/backend/routers/deals.py` (extend)

```python
@router.post("/deals/runs/{deal_id}/call-transcript")
async def submit_call_transcript(deal_id: str, body: TranscriptRequest):
    """body: {transcript: str}. Saves to deal_runs.call_transcript, sets
    status='call_summarizing', emits sales/transcript-submitted."""
    ...
```

**File:** `apps/backend/inngest/deal_call_summary.py`

```python
@inngest_client.create_function(
    fn_id="sales-summarize-call",
    trigger=inngest.TriggerEvent(event="sales/transcript-submitted"),
)
async def summarize_call(ctx: inngest.Context, step: inngest.Step):
    deal_id = ctx.event.data["deal_run_id"]

    summary = await step.run("extract-summary", lambda: extract_call_summary(deal_id))

    await step.run("save-summary", lambda: save_call_summary(deal_id, summary))
    await step.run("log-call-event", lambda: log_deal_event(
        deal_id, "agent", None, "call_summarized",
        payload={"meeting_count": summary["meeting_count_at_time"]}))
    await step.run("set-status-summarized", lambda: update_deal_status(deal_id, "call_summarized"))
    # Does NOT auto-advance further — rep makes the next-call-vs-propose decision (see below)
```

**File:** `apps/backend/services/deal_call_service.py`

```python
async def extract_call_summary(deal_id: str) -> dict:
    """Calls Gemini on the transcript to extract:
    - human_readable_summary: str
    - bant: {budget: str, authority: str, need: str, timeline: str} — each field
      is a short extracted signal or null if not discussed
    - objections: list[str]
    - next_steps: [{step: str, owner: str, due_date: str | null}]
    - recommended_stage: 'MQL' | 'SQL' | 'Proposal' | 'Negotiation'
    Returns combined dict. Langfuse-trace."""
    ...

async def save_call_summary(deal_id: str, summary: dict) -> None:
    """Writes call_summary, bant_json, next_steps_json, recommended_stage to deal_runs."""
    ...
```

### Mid-Morning: The Next-Call-vs-Propose Decision Point

This is the re-entrancy fork the transcript implied but didn't make explicit as its own gate. ⚠️ **RECONSTRUCTED:** treated as a lightweight rep decision (not a full `deal_approvals` gate, since there's no draft content to approve — just a fork).

**File:** `apps/backend/routers/deals.py` (extend)

```python
@router.post("/deals/runs/{deal_id}/next-step")
async def choose_next_step(deal_id: str, body: NextStepRequest):
    """body: {decision: 'schedule_another_call' | 'proceed_to_proposal'}.
    If schedule_another_call: rep is redirected to the meeting-booked flow again
    (status → 'meeting_booked' loop, per re-entrancy requirement).
    If proceed_to_proposal: status → 'proposal_drafting', emits a
    sales/proposal-drafting-requested event (or directly invoke the Day 4 afternoon
    Inngest function below)."""
    ...
```

**Frontend:** `apps/frontend/app/sales/deals/[id]/call-summary/page.tsx` — shows extracted summary, BANT grid, objections, next steps table, recommended stage — with two buttons at the bottom: "Schedule another call" / "Move to proposal", calling the endpoint above.

### Afternoon Block: Proposal Generation + Gate

**File:** `apps/backend/inngest/deal_proposal.py`

```python
@inngest_client.create_function(
    fn_id="sales-draft-proposal",
    trigger=inngest.TriggerEvent(event="sales/proposal-drafting-requested"),
)
async def draft_proposal(ctx: inngest.Context, step: inngest.Step):
    deal_id = ctx.event.data["deal_run_id"]
    org_id = ctx.event.data["org_id"]

    await step.run("set-status-drafting", lambda: update_deal_status(deal_id, "proposal_drafting"))

    template = await step.run("fetch-proposal-template", lambda: fetch_template(org_id, "proposal"))
    if not template:
        await step.run("block-missing-template", lambda: update_deal_status(
            deal_id, "blocked_missing_template", previous_status="proposal_drafting"))
        await step.run("log-blocked", lambda: log_deal_event(
            deal_id, "system", None, "blocked_missing_template"))
        return  # resumed by sales/template-uploaded

    proposal_path = await step.run("render-proposal", lambda: render_proposal_docx(deal_id, template))

    # Optionally generate SOW / pricing sheet if those templates also exist
    sow_template = await step.run("fetch-sow-template", lambda: fetch_template(org_id, "sow"))
    if sow_template:
        await step.run("render-sow", lambda: render_sow_docx(deal_id, sow_template))

    pricing_template = await step.run("fetch-pricing-template", lambda: fetch_template(org_id, "pricing_sheet"))
    if pricing_template:
        await step.run("render-pricing", lambda: render_pricing_docx(deal_id, pricing_template))

    await step.run("create-approval-gate", lambda: create_deal_approval(
        deal_id=deal_id, gate_type="proposal", draft_path=proposal_path))
    await step.run("set-status-pending-review", lambda: update_deal_status(
        deal_id, "proposal_pending_rep_review"))
    await step.run("notify-rep", lambda: notify_rep_of_pending_review(deal_id, "proposal"))
```

**File:** `apps/backend/services/deal_proposal_service.py`

```python
async def fetch_template(org_id: str, kind: str) -> dict | None:
    """KB query for document tagged template_kind=kind. Same tagging convention
    as fetch_icp_document — grep and reuse, don't reinvent the tagging mechanism."""
    ...

async def render_proposal_docx(deal_id: str, template: dict) -> str:
    """Uses python-docx (same library as Agent2_Roadmap's RFP response feature —
    reuse patterns, not code, per bespoke-pipeline decision) to fill the template
    with: company_name, use case (from call_summary/BANT), recommended plan,
    pricing, ROI estimate (computed from deal data if a formula exists in the
    template, else AI-estimated with a clear 'estimated' label).
    Saves to deal_documents (kind='proposal', source='agent_generated') AND
    deal_runs.proposal_draft_path. Returns storage path."""
    ...

async def render_sow_docx(deal_id: str, template: dict) -> str:
    """Same pattern, kind='sow'."""
    ...

async def render_pricing_docx(deal_id: str, template: dict) -> str:
    """Same pattern, kind='pricing_sheet'."""
    ...
```

**File:** `apps/backend/routers/deals.py` (extend) — proposal gate endpoints

```python
@router.get("/deals/runs/{deal_id}/proposal")
async def get_proposal_draft(deal_id: str):
    """Returns download links for all generated documents (proposal + sow +
    pricing sheet if present) and the deal_approvals gate status."""
    ...

@router.post("/deals/runs/{deal_id}/proposal/upload-edited")
async def upload_edited_proposal(deal_id: str, file: UploadFile):
    """Rep downloads, edits locally, re-uploads. Saves to deal_documents
    (kind='proposal', source='rep_uploaded'), updates deal_approvals.edited_path,
    deal_runs.proposal_edited_path, increments proposal_revision."""
    ...

@router.post("/deals/runs/{deal_id}/proposal/approve")
async def approve_proposal(deal_id: str, user=Depends(get_current_user)):
    """1. Marks deal_approvals approved.
    2. Emails the final proposal (edited_path if present, else draft_path) via
    Gmail send adapter, with rendered docx as attachment.
    3. Sets proposal_sent_at, status='proposal_sent'.
    4. Logs deal_event.
    5. Emits sales/proposal-approved → downstream auto-transition to awaiting_decision."""
    ...
```

**File:** `apps/backend/inngest/deal_proposal.py` (extend) — automatic bookkeeping transition, same pattern as Day 2's outreach_sent → awaiting_reply:

```python
@inngest_client.create_function(
    fn_id="sales-proposal-sent-transition",
    trigger=inngest.TriggerEvent(event="sales/proposal-approved"),
)
async def transition_to_awaiting_decision(ctx: inngest.Context, step: inngest.Step):
    deal_id = ctx.event.data["deal_run_id"]
    await step.run("set-awaiting-decision", lambda: update_deal_status(deal_id, "awaiting_decision"))
```

### Late Afternoon: Check-in Cron + Close

**File:** `apps/backend/inngest/deal_checkins.py`

```python
CHECKIN_INTERVAL_DAYS = 5
MAX_CHECKINS = 2

@inngest_client.create_function(
    fn_id="sales-checkin-reminders",
    trigger=inngest.TriggerCron(cron="0 9 * * *"),
)
async def daily_checkin_check(ctx: inngest.Context, step: inngest.Step):
    due_deals = await step.run("find-due-checkins", lambda: find_deals_due_for_checkin(
        interval_days=CHECKIN_INTERVAL_DAYS, max_checkins=MAX_CHECKINS))

    for deal in due_deals:
        if deal["checkin_count"] >= MAX_CHECKINS:
            await step.run(f"flag-at-risk-{deal['id']}", lambda d=deal: update_deal_status(
                d["id"], "at_risk"))
            await step.run(f"log-at-risk-{deal['id']}", lambda d=deal: log_deal_event(
                d["id"], "system", None, "flagged_at_risk"))
            continue

        nudge = await step.run(f"draft-checkin-{deal['id']}", lambda d=deal: generate_checkin_nudge(d))
        await step.run(f"create-gate-{deal['id']}", lambda d=deal, n=nudge: create_deal_approval(
            deal_id=d["id"], gate_type="outreach", draft_content=n["email_body"]))
        await step.run(f"notify-rep-{deal['id']}", lambda d=deal: notify_rep_of_pending_review(
            d["id"], "checkin"))
```

**File:** `apps/backend/services/deal_checkin_service.py`

```python
async def find_deals_due_for_checkin(interval_days: int, max_checkins: int) -> list[dict]:
    """Same pattern as find_deals_due_for_followup, scoped to status='awaiting_decision'
    and last_checkin_at / proposal_sent_at."""
    ...

async def generate_checkin_nudge(deal: dict) -> dict:
    """Lighter-touch than outreach follow-ups — references the sent proposal,
    asks for status/timeline, offers to answer questions."""
    ...
```

**File:** `apps/backend/routers/deals.py` (extend) — manual close

```python
@router.post("/deals/runs/{deal_id}/close")
async def close_deal(deal_id: str, body: CloseDealRequest, user=Depends(get_current_user)):
    """body: {outcome: 'closed_won' | 'closed_lost', reason: str}. reason is
    REQUIRED (validate non-empty) — this is the only deal_runs field explicitly
    called out as mandatory at close in the original transcript.
    Sets closed_at, close_reason, status. Logs deal_event. Emits sales/deal-closed."""
    ...
```

### Frontend

**File:** `apps/frontend/app/sales/deals/[id]/proposal/page.tsx` — document preview/download links, "Upload edited version" file picker, "Approve & Send" button.

**File:** `apps/frontend/app/sales/deals/[id]/page.tsx` (main deal detail view, tie everything together) — full `deal_events` timeline rendered chronologically, current status badge, contextual action buttons depending on current status (this is the page reps live in throughout the whole pipeline — build it last since it needs every stage's data shape finalized first).

### End of Day 4 — Verification Checklist
- [ ] Call summary extraction correctly populates BANT fields as null (not fabricated) when a topic wasn't discussed in the transcript
- [ ] Choosing "Schedule another call" correctly loops the deal back to `meeting_booked` and increments `meeting_count` again — re-entrancy confirmed end-to-end
- [ ] Choosing "Move to proposal" with no proposal template in KB correctly halts at `blocked_missing_template`, and uploading one resumes it
- [ ] Proposal generation correctly produces SOW/pricing sheet only when those templates exist, skipping cleanly otherwise
- [ ] Uploading an edited proposal and approving sends the EDITED version, not the original draft
- [ ] Check-in cron correctly flags `at_risk` after 2 check-ins with no rep update, doesn't loop forever
- [ ] Closing a deal without a `reason` is rejected by validation
- [ ] Full pipeline smoke test: create one test lead and walk it manually through every stage to `closed_won`, confirming `deal_events` timeline reads as a coherent audit trail at the end

---

## Cross-Cutting Reminders for Implementation (Claude Code)

1. **Run the reconciliation greps from the top of this doc before Day 1** — confirm no naming collision with onboarding agent's tables, and confirm the RLS/org-isolation pattern.
2. **Reuse, don't rebuild:** Gmail send adapter, KB search utilities, notification pattern, python-docx rendering pattern (from Agent2_Roadmap's RFP feature), and template tagging convention (`template_kind`) all should already exist or have established conventions — grep before writing new code for any of these.
3. **No shared pipeline abstraction with Onboarding Agent** — this was an explicit decision. The state-machine *pattern* is copied; the *code* is not shared.
4. **`deal_approvals` is separate from `approval_requests`** — do not conflate these two tables or attempt to migrate one into the other mid-build.
5. **Follow-up/check-in interval constants are reconstructed assumptions** (`FOLLOWUP_INTERVAL_DAYS = 3`, `CHECKIN_INTERVAL_DAYS = 5`) — both are defined as named constants specifically so they're trivial to change in one place if the actual intended cadence differs from what was inferred from the garbled transcript.
6. **Re-entrancy is not optional polish** — `meeting_count` incrementing correctly and `deal_events` capturing each loop iteration distinctly is core to this pipeline's correctness, not an edge case. Test it explicitly, don't assume the happy-path single-meeting flow covers it.
7. **Langfuse tracing** on every new Gemini call site (research, ICP scoring, outreach drafting, follow-up drafting, call summarization, proposal rendering, check-in nudges) — apply the existing tracing decorator convention uniformly, per the same cross-cutting note as Agent2_Roadmap.