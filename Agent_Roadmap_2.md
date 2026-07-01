# NirnayaIQ — Agent2 Roadmap (6 Days)

**Scope:** Features #14, #8, #20, #22, #23, #24, #25, #33, #37, #46, #51, #52, #44, #39, #36 (15 features — note doc had #14 + 14 others, totaling 15).

**Confirmed Architecture (do not re-litigate these mid-implementation):**
- Stack: Next.js (frontend), FastAPI (backend), Supabase (Postgres + RLS + pgvector), Redis, Inngest (background jobs/scheduling), Resend (email), Langfuse (LLM observability), Railway (hosting), GitHub Actions (CI), Stripe (billing — not touched this roadmap), Gemini (LLM provider).
- Package managers: `uv` for Python, `pnpm` for frontend.
- External integrations (Greenhouse, Lever, LinkedIn readonly, Notion, Asana/Linear, Google Calendar, Slack send, Gmail send): **already provisioned**. OAuth apps, API keys, and scopes exist. This roadmap only wires calls against them — no provisioning steps included. If a key is missing at implementation time, stop and flag rather than stub silently.
- Autoflow engine: backend core built Day 1 as **shared infra**. The `autoflows` table, trigger evaluator, and Inngest dispatch functions are built first; Sequences (#8), Knowledge Curator (#24) scheduled scans, and Internal Comms (#23) scheduling all run on top of this engine rather than rolling their own cron/trigger logic. The **no-code builder UI** (#46) is built last (Day 6) once there are real autoflows in the system to visualize and manage.
- Agent pattern: **bespoke pipeline per agent**. Recruiting (#20), Sales Enablement (#22), Internal Comms (#23), Knowledge Curator (#24), Executive Assistant (#25) each get their own purpose-built pipeline. No shared `AgentExecutor` abstraction — do not refactor toward one mid-roadmap even if duplication appears.
- Approvals: **reuse the existing `approval_requests` table** from Agent Roadmap V1. ⚠️ **Schema not confirmed in this roadmap.** Before writing ANY code that touches `approval_requests`, run:
  ```bash
  grep -rn "approval_requests" --include="*.py" --include="*.sql" .
  ```
  and inspect the actual columns via Supabase (`\d approval_requests` in psql, or the migration file that created it). The field names used below (`id, org_id, type, payload, status, requested_by, approved_by, created_at`) are a **placeholder guess** — reconcile against the real schema first and adjust every snippet below accordingly. Do not proceed with approval-gated features until this is confirmed.
- Document export: **separate implementation per feature**. RFP response (#22) builds its own Word export using `python-docx` (or the `docx` skill pattern if available). Executive briefing (#25) builds its own Google Docs export via the Google Docs API. No shared `DocumentExporter` service — do not unify these.
- Every "Backend Changes" section below assumes the existing FastAPI app structure (`routers/`, `services/`, `models/`) used in prior roadmaps. If actual paths differ, adjust paths but keep the same logical separation.

**Day-to-day dependency order (why this sequence):**
1. **Day 1** — Autoflow engine core + Webhook expansion (#36, infra half of #46). Everything downstream depends on triggers/dispatch existing.
2. **Day 2** — Document intelligence layer: Duplicate Detection (#14) + Smart Routing (#33) + Persona Chat Modes (#37) + Context Pinning (#39). These are all "smaller" (2-6 complexity) and sit close to the existing document pipeline — clears the deck before the big agents.
3. **Day 3** — Knowledge Curator Agent (#24). Needs duplicate detection (#14) and routing (#33) from Day 2 as building blocks (similarity scores, collection assignment logic).
4. **Day 4** — Follow-up Sequence Agent (#8) + Internal Communications Agent (#23). Both are scheduling/multi-channel-send heavy, both ride on the Day 1 autoflow/Inngest scheduling infra.
5. **Day 5** — Recruiting Agent (#20) + Sales Enablement Agent (#22). The two highest-complexity, highest-external-integration agents — placed once the team has full familiarity with the org's KB search + external API patterns from Days 1-4.
6. **Day 6** — Executive Assistant Agent (#25) + Calendar Intelligence (#51) + Meeting Prep → Action Tracker (#44) + Output Quality Scoring (#52) + No-Code Autoflow Builder UI (#46). Calendar-related features cluster together; quality scoring and the no-code builder UI are last because they instrument/manage everything built in Days 1-5.

---

## DAY 1 — Autoflow Engine Core + Webhook Expansion

**Goal:** Build the shared trigger/action backend that Sequences, Knowledge Curator, and Internal Comms will run on top of. Expand webhooks so Nirnaya IQ becomes a Zapier-compatible source of truth.

### Morning Block: Database Schema

**File:** `supabase/migrations/0XX_autoflows_core.sql`

```sql
-- Autoflow definitions
CREATE TABLE autoflows (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  description TEXT,
  trigger_type TEXT NOT NULL CHECK (trigger_type IN (
    'document_uploaded', 'document_ready', 'query_no_results',
    'scheduled', 'feedback_negative', 'employee_joined',
    'knowledge_gap_detected', 'approval_requested'
  )),
  trigger_config JSONB NOT NULL DEFAULT '{}', -- e.g. {"cron": "0 9 * * 1"} for scheduled
  actions JSONB NOT NULL DEFAULT '[]', -- ordered array of action steps
  confidence_threshold FLOAT DEFAULT NULL, -- if set, actions below this hold for approval
  is_active BOOLEAN NOT NULL DEFAULT true,
  created_by UUID NOT NULL REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_autoflows_org_trigger ON autoflows(org_id, trigger_type) WHERE is_active = true;

-- Execution log (every time an autoflow fires)
CREATE TABLE autoflow_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  autoflow_id UUID NOT NULL REFERENCES autoflows(id) ON DELETE CASCADE,
  org_id UUID NOT NULL REFERENCES organizations(id),
  trigger_payload JSONB NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'completed', 'failed', 'held_for_approval')),
  steps_completed INT NOT NULL DEFAULT 0,
  total_steps INT NOT NULL DEFAULT 0,
  error_message TEXT,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at TIMESTAMPTZ
);

CREATE INDEX idx_autoflow_runs_autoflow ON autoflow_runs(autoflow_id, started_at DESC);

-- RLS
ALTER TABLE autoflows ENABLE ROW LEVEL SECURITY;
ALTER TABLE autoflow_runs ENABLE ROW LEVEL SECURITY;

CREATE POLICY autoflows_org_isolation ON autoflows
  USING (org_id = (SELECT org_id FROM users WHERE id = auth.uid()));

CREATE POLICY autoflow_runs_org_isolation ON autoflow_runs
  USING (org_id = (SELECT org_id FROM users WHERE id = auth.uid()));
```

**⚠️ Reconciliation step before running this migration:** check whether `organizations`, `users` tables and `auth.uid()` RLS pattern match exactly what's used elsewhere in the codebase (e.g. `grep -rn "org_id = (SELECT org_id FROM users" supabase/migrations/`). Match the existing convention exactly rather than introducing a second pattern.

### Mid-Morning: Trigger Action Schema (JSONB shape, documented for both backend and future no-code UI)

**File:** `apps/backend/models/autoflow.py`

```python
from pydantic import BaseModel
from typing import Literal, Optional, Any
from enum import Enum

class AutoflowActionType(str, Enum):
    GENERATE_OUTPUT = "generate_output"
    SEND_EMAIL = "send_email"
    POST_SLACK = "post_slack"
    CREATE_NOTION_PAGE = "create_notion_page"
    NOTIFY_ADMIN = "notify_admin"
    CREATE_TASK = "create_task"  # Asana/Linear/Notion via #44 pattern
    HOLD_FOR_APPROVAL = "hold_for_approval"

class AutoflowAction(BaseModel):
    type: AutoflowActionType
    config: dict[str, Any]  # action-specific params, e.g. {"recipient": "...", "template": "..."}
    order: int

class AutoflowTriggerConfig(BaseModel):
    cron: Optional[str] = None  # only for trigger_type == 'scheduled'
    filters: Optional[dict[str, Any]] = None  # e.g. {"collection_id": "..."} to scope the trigger

class AutoflowCreate(BaseModel):
    name: str
    description: Optional[str] = None
    trigger_type: Literal[
        "document_uploaded", "document_ready", "query_no_results",
        "scheduled", "feedback_negative", "employee_joined",
        "knowledge_gap_detected", "approval_requested"
    ]
    trigger_config: AutoflowTriggerConfig = AutoflowTriggerConfig()
    actions: list[AutoflowAction]
    confidence_threshold: Optional[float] = None
```

### Afternoon Block: Inngest Dispatch Functions

**File:** `apps/backend/inngest/autoflow_dispatch.py`

```python
import inngest
from services.autoflow_service import (
    get_matching_autoflows, execute_autoflow_actions, log_autoflow_run
)

# Fired by every existing event emitter — document processing, query handler, feedback handler
@inngest_client.create_function(
    fn_id="autoflow-trigger-dispatch",
    trigger=inngest.TriggerEvent(event="autoflow/trigger.fired"),
)
async def dispatch_autoflow_trigger(ctx: inngest.Context, step: inngest.Step):
    trigger_type = ctx.event.data["trigger_type"]
    org_id = ctx.event.data["org_id"]
    payload = ctx.event.data["payload"]

    matching = await step.run(
        "fetch-matching-autoflows",
        lambda: get_matching_autoflows(org_id, trigger_type, payload)
    )

    for autoflow in matching:
        await step.run(
            f"execute-autoflow-{autoflow['id']}",
            lambda af=autoflow: execute_autoflow_actions(af, payload)
        )

# Scheduled autoflows poll on a cron, NOT per-org — single Inngest cron checks all due schedules
@inngest_client.create_function(
    fn_id="autoflow-scheduled-check",
    trigger=inngest.TriggerCron(cron="* * * * *"),  # every minute, checks which cron strings are due
)
async def check_scheduled_autoflows(ctx: inngest.Context, step: inngest.Step):
    due_autoflows = await step.run(
        "find-due-scheduled-autoflows",
        lambda: get_due_scheduled_autoflows()  # compares trigger_config.cron against current time
    )
    for autoflow in due_autoflows:
        await step.run(
            f"execute-scheduled-{autoflow['id']}",
            lambda af=autoflow: execute_autoflow_actions(af, {})
        )
```

**File:** `apps/backend/services/autoflow_service.py`

```python
async def get_matching_autoflows(org_id: str, trigger_type: str, payload: dict) -> list[dict]:
    """Query autoflows table for active flows matching this org + trigger type.
    Apply trigger_config.filters against payload if present."""
    ...

async def execute_autoflow_actions(autoflow: dict, trigger_payload: dict):
    """Walk autoflow['actions'] in order. Before each action, if autoflow['confidence_threshold']
    is set and the action has a confidence score below it, write to approval_requests
    (RECONCILE SCHEMA FIRST) and set autoflow_runs.status = 'held_for_approval', then stop.
    Otherwise execute the action via the matching adapter function and continue."""
    ...

ACTION_HANDLERS = {
    "send_email": "services.email_service.send_via_resend",
    "post_slack": "services.slack_service.post_message",
    "create_notion_page": "services.notion_service.create_page",
    "notify_admin": "services.notification_service.notify_admin",
    "create_task": "services.task_service.create_task",  # built Day 4/6, stub today
}

async def log_autoflow_run(autoflow_id: str, status: str, steps_completed: int, error: str | None = None):
    ...
```

### Late Afternoon: Webhook Expansion (#36)

**File:** `apps/backend/services/webhook_functions.py` — extend existing enum

```python
class WebhookEvent(str, Enum):
    QUERY_COMPLETED = "query.completed"  # existing
    DOCUMENT_UPLOADED = "document.uploaded"  # NEW
    DOCUMENT_READY = "document.ready"  # NEW
    KNOWLEDGE_GAP_DETECTED = "knowledge_gap.detected"  # NEW
    AGENT_COMPLETED = "agent.completed"  # NEW
    APPROVAL_REQUESTED = "approval.requested"  # NEW
    MESSAGE_FEEDBACK_NEGATIVE = "message.feedback.negative"  # NEW
```

**File:** `apps/backend/routers/webhooks.py` — extend existing emit logic so every place in the codebase that currently fires `query.completed` gets a sibling call pattern:

```python
async def emit_webhook_event(org_id: str, event: WebhookEvent, payload: dict):
    """Existing function — verify signature, fetch org's registered webhook URLs,
    POST payload with event type header. No changes needed here, just new call sites."""
    ...

# New call sites to add (search codebase for these locations and insert the emit call):
# 1. apps/backend/services/document_service.py — after document insert → emit DOCUMENT_UPLOADED
# 2. apps/backend/inngest/process_document.py — after status set to 'ready' → emit DOCUMENT_READY
# 3. services/feedback_service.py — after negative feedback recorded → emit MESSAGE_FEEDBACK_NEGATIVE
# 4. services/autoflow_service.py — when writing to approval_requests → emit APPROVAL_REQUESTED
```

**Zapier/Make connector scaffolding:**
- `integrations/zapier/triggers.js` — polling trigger definitions for each new webhook event (Zapier CLI app structure: `triggers/document_ready.js`, etc.)
- `integrations/zapier/actions.js` — action definitions (e.g. "Create Document", "Trigger Query") calling existing public REST API endpoints from Agent Roadmap V1
- Document Zapier app submission checklist in `integrations/zapier/README.md` — auth (API key), trigger/action list, sample payloads per event.

### End of Day 1 — Verification Checklist
- [ ] `autoflows` and `autoflow_runs` tables exist with RLS confirmed against real `users`/`organizations` pattern
- [ ] Inngest dispatch function fires correctly when manually emitting a test `autoflow/trigger.fired` event
- [ ] Scheduled cron check correctly identifies due autoflows (test with a 1-minute-future cron string)
- [ ] All 6 new webhook events fire from their real call sites (manually trigger each, confirm payload arrives at a test endpoint like webhook.site)
- [ ] `approval_requests` schema has been inspected and real field names noted in a comment at the top of `autoflow_service.py` for Days 2-6 to reference

---

## DAY 2 — Duplicate Detection, Smart Routing, Persona Modes, Context Pinning

**Goal:** Four smaller, self-contained features (complexity 2-5/10) that improve document intake quality and personalization. All ride on existing embedding/document infra.

### Feature #14: Duplicate Document Detection Autoflow

**Backend — SQL function for summary-level similarity:**

`supabase/migrations/0XX_duplicate_detection.sql`
```sql
CREATE OR REPLACE FUNCTION find_similar_documents(
  p_org_id UUID,
  p_document_id UUID,
  p_summary_embedding VECTOR(1536),
  p_threshold FLOAT DEFAULT 0.85
)
RETURNS TABLE (
  doc_id UUID,
  doc_title TEXT,
  similarity FLOAT
) AS $$
  SELECT
    d.id AS doc_id,
    d.title AS doc_title,
    1 - (d.summary_embedding <=> p_summary_embedding) AS similarity
  FROM documents d
  WHERE d.org_id = p_org_id
    AND d.id != p_document_id
    AND d.status = 'ready'
    AND 1 - (d.summary_embedding <=> p_summary_embedding) > p_threshold
  ORDER BY similarity DESC
  LIMIT 5;
$$ LANGUAGE sql STABLE;
```

**⚠️ Reconciliation:** confirm `documents.summary_embedding` column exists and is `VECTOR(1536)` (Gemini embedding dimension — verify against actual Gemini embedding model dimension in use, e.g. `text-embedding-004` is 768-dim, not 1536. Check `services/embedding_service.py` for the real dimension before writing this migration).

**Inngest post-processing step:**

`apps/backend/inngest/process_document.py` — add new step after existing `process-document` function completes:

```python
@inngest_client.create_function(
    fn_id="check-duplicate-documents",
    trigger=inngest.TriggerEvent(event="document/ready"),
)
async def check_duplicate_documents(ctx: inngest.Context, step: inngest.Step):
    doc_id = ctx.event.data["document_id"]
    org_id = ctx.event.data["org_id"]

    similar = await step.run(
        "find-similar-docs",
        lambda: find_similar_documents_query(org_id, doc_id)
    )

    if similar:
        await step.run(
            "create-duplicate-warning",
            lambda: create_duplicate_warning_notification(org_id, doc_id, similar[0])
        )
```

**File:** `apps/backend/services/duplicate_detection_service.py`
```python
async def find_similar_documents_query(org_id: str, document_id: str) -> list[dict]:
    """Fetch document's summary_embedding, call find_similar_documents SQL function."""
    ...

async def create_duplicate_warning_notification(org_id: str, doc_id: str, match: dict):
    """Insert into notifications table (or admin_alerts if that's the real table name —
    grep for existing admin notification pattern). Payload: doc title, match title, similarity %."""
    ...

async def get_document_diff(doc_id_a: str, doc_id_b: str) -> dict:
    """Fetch full text of both documents, return a structural diff (use difflib.HtmlDiff
    or a simple line-based diff) for the admin UI to render."""
    import difflib
    ...
```

**Frontend:** `apps/frontend/components/admin/DuplicateWarningBanner.tsx` — shows "You may already have 'X' which is Y% similar. Merge or replace?" with two buttons calling `POST /admin/documents/{id}/merge` and `POST /admin/documents/{id}/replace` (new endpoints, write stubs if merge logic doesn't exist yet — flag as P2 follow-up if full merge UX is out of scope for this roadmap).

### Feature #33: Smart Document Routing (Auto-Collection Assignment)

**File:** `apps/backend/services/auto_collection_service.py`
```python
async def suggest_collection_for_document(document_id: str) -> dict:
    """1. Fetch doc title, first paragraph, auto-summary (reuse existing auto-tagger
    output from Feature #15 if already built — grep for 'auto_tag' or 'Feature #15').
    2. Embed the combined text.
    3. For each existing collection, compute average embedding of its member documents.
    4. Cosine similarity between doc embedding and each collection's centroid.
    5. Return top match if similarity > 0.80, else None."""
    ...

async def batch_assign_pending_documents(org_id: str) -> list[dict]:
    """Run suggest_collection_for_document for all unassigned docs in org.
    Group results by suggested collection. Return for admin batch-approval UI."""
    ...
```

**New endpoint:** `apps/backend/routers/collections.py`
```python
@router.get("/admin/collections/suggestions")
async def get_collection_suggestions(org_id: str = Depends(get_current_org)):
    """Returns: [{collection_id, collection_name, suggested_doc_ids: [...], count}]"""
    ...

@router.post("/admin/collections/suggestions/approve")
async def approve_collection_suggestions(body: ApproveSuggestionsRequest):
    """body: {assignments: [{doc_id, collection_id}]}. Bulk insert into
    document_collections junction table."""
    ...
```

**Frontend:** `apps/frontend/components/admin/CollectionSuggestionBanner.tsx` — "8 documents auto-assigned to HR collection — review?" with batch approve/reject.

### Feature #37: Persona-Based Chat Modes

**SQL:** `supabase/migrations/0XX_user_roles.sql`
```sql
ALTER TABLE users ADD COLUMN role TEXT DEFAULT NULL
  CHECK (role IN ('hr', 'sales', 'engineering', 'finance', 'operations', 'executive') OR role IS NULL);
```

**File:** `apps/backend/services/org_instructions_service.py` — extend existing `get_org_instructions()`:
```python
ROLE_OVERLAYS = {
    "hr": "Bias search toward HR/Policy collections. Cite policy document names and effective dates explicitly. Use formal, compliance-conscious tone.",
    "sales": "Bias search toward competitor analysis, pricing, case study collections. Format output as ready-to-send talking points. Favor brevity and persuasive framing.",
    "engineering": "Bias search toward technical spec and architecture collections. Include code snippets and exact technical terminology. Favor precision over brevity.",
    "finance": "Bias search toward financial/budget collections. Include specific figures and citations to source documents.",
    "operations": "Bias search toward process/SOP collections. Format as step-by-step procedures.",
    "executive": "Synthesize across all collections. Lead with executive summary. Favor brevity, strategic framing, and risk/opportunity framing.",
}

async def get_org_instructions(org_id: str, user_id: str) -> str:
    base_instructions = await fetch_base_org_instructions(org_id)  # existing logic
    user_role = await fetch_user_role(user_id)  # NEW
    if user_role and user_role in ROLE_OVERLAYS:
        return f"{base_instructions}\n\n{ROLE_OVERLAYS[user_role]}"
    return base_instructions
```

**Frontend:** `apps/frontend/components/settings/RoleSelector.tsx` — dropdown in user settings, `PATCH /users/me` with `{role: "hr"}`.

### Feature #39: Context Pinning

**SQL:** `supabase/migrations/0XX_pinned_context.sql`
```sql
ALTER TABLE conversations ADD COLUMN pinned_context TEXT DEFAULT NULL;
```

**File:** `apps/backend/services/execute_task_service.py` — modify system prompt assembly:
```python
async def build_system_prompt(org_id: str, user_id: str, conversation_id: str) -> str:
    org_instructions = await get_org_instructions(org_id, user_id)  # includes role overlay from #37
    pinned = await fetch_pinned_context(conversation_id)  # NEW
    if pinned:
        return f"{org_instructions}\n\n[Session Context — user has pinned this for the conversation]\n{pinned}"
    return org_instructions
```

**New endpoint:** `apps/backend/routers/conversations.py`
```python
@router.patch("/conversations/{conversation_id}/pin-context")
async def pin_context(conversation_id: str, body: PinContextRequest):
    """body: {context: str | None}. None clears the pin."""
    ...
```

**Frontend:** `apps/frontend/components/chat/PinContextButton.tsx` — small pin icon + textarea at top of chat session, persists via the above endpoint, shows a visible chip "📌 Q3 sales campaign" while active.

### End of Day 2 — Verification Checklist
- [ ] Embedding dimension in `find_similar_documents` matches actual Gemini embedding model output
- [ ] Duplicate warning fires on a manually uploaded near-duplicate test doc
- [ ] Collection suggestion correctly proposes a collection for a test doc with >0.80 similarity
- [ ] Setting a user's role to `sales` measurably changes a test query's system prompt (log and inspect)
- [ ] Pinning context in one conversation does not leak into a second conversation for the same user

---

## DAY 3 — Knowledge Curator Agent (#24)

**Goal:** Weekly autonomous scan that produces a Knowledge Health Report, suggests merges (reusing #14's similarity function) and gaps, and drafts missing-document stubs for approval.

### Backend: Scheduled Trigger via Day 1 Autoflow Engine

This is **not** a bespoke cron — it registers as a system-level autoflow (`trigger_type: 'scheduled'`) seeded at org-creation time, or via a one-time backfill migration for existing orgs:

`supabase/migrations/0XX_seed_curator_autoflow.sql`
```sql
INSERT INTO autoflows (org_id, name, trigger_type, trigger_config, actions, created_by)
SELECT id, 'Weekly Knowledge Curator Scan', 'scheduled',
  '{"cron": "0 8 * * 1"}'::jsonb,  -- Monday 8am
  '[{"type": "generate_output", "config": {"handler": "knowledge_curator_scan"}, "order": 1}]'::jsonb,
  (SELECT id FROM users WHERE org_id = organizations.id ORDER BY created_at LIMIT 1)
FROM organizations;
```

**File:** `apps/backend/services/knowledge_curator_service.py`
```python
async def run_knowledge_curator_scan(org_id: str) -> dict:
    """Orchestrates the full weekly scan. Returns the Knowledge Health Report dict."""
    outdated = await find_outdated_documents(org_id)
    broken_links = await find_broken_links(org_id)
    merge_suggestions = await find_merge_candidates(org_id)  # reuses Day 2's find_similar_documents
    gaps = await find_knowledge_gaps(org_id)
    stubs = await draft_gap_stubs(org_id, gaps)

    report = {
        "org_id": org_id,
        "generated_at": datetime.utcnow().isoformat(),
        "outdated_documents": outdated,
        "broken_links": broken_links,
        "merge_suggestions": merge_suggestions,
        "knowledge_gaps": gaps,
        "drafted_stubs": stubs,
    }
    await save_health_report(report)
    return report

async def find_outdated_documents(org_id: str) -> list[dict]:
    """Query documents where metadata contains date references older than e.g. 18 months,
    OR document references a policy name that has since been superseded (cross-check
    against documents tagged as 'superseded_by' if that field exists — else flag for
    keyword-based heuristic: 'policy v1' style version strings where a higher version exists)."""
    ...

async def find_broken_links(org_id: str) -> list[dict]:
    """Extract URLs from document content (regex for http(s) links), HEAD request each,
    flag non-200 responses. Batch with asyncio.gather, rate-limit to avoid hammering targets."""
    ...

async def find_merge_candidates(org_id: str) -> list[dict]:
    """For every document pair with similarity > 0.75 (lower threshold than #14's 0.85,
    since this is a suggestion not an upload-time block), group into clusters using
    a simple union-find, return clusters with >= 2 members and avg similarity."""
    ...

async def find_knowledge_gaps(org_id: str) -> list[dict]:
    """Query queries table (or query_log — grep for the real table) from the last 7 days
    where result_count == 0 or confidence_score < 0.4. Cluster by embedding similarity
    of the query text to find recurring themes (e.g. 5 questions about contractor
    classification = 1 gap, not 5)."""
    ...

async def draft_gap_stubs(org_id: str, gaps: list[dict]) -> list[dict]:
    """For each gap cluster, call Gemini to draft a stub document (~200 words) covering
    the likely answer based on general knowledge, clearly marked '[DRAFT — needs review]'.
    Insert into documents table with status='draft_pending_review', NOT status='ready'
    (so it doesn't enter the RAG pipeline until approved). Write to approval_requests
    (RECONCILE SCHEMA) with type='knowledge_gap_stub'."""
    ...
```

### Frontend: Knowledge Health Report Dashboard

**File:** `apps/frontend/app/admin/knowledge-health/page.tsx`
- Weekly report view: outdated docs list (with "mark reviewed" action), broken links list, merge suggestion cards (similarity %, "Merge" / "Dismiss" buttons calling Day 2's merge endpoint), knowledge gap cards with the drafted stub preview and Approve/Reject/Edit buttons.
- `GET /admin/knowledge-health/latest` and `GET /admin/knowledge-health/history` endpoints.

**File:** `apps/backend/routers/knowledge_health.py`
```python
@router.get("/admin/knowledge-health/latest")
async def get_latest_report(org_id: str = Depends(get_current_org)):
    ...

@router.post("/admin/knowledge-health/stubs/{stub_id}/approve")
async def approve_stub(stub_id: str):
    """Sets document status from 'draft_pending_review' to 'ready', triggers normal
    embedding pipeline so it enters RAG search."""
    ...
```

### End of Day 3 — Verification Checklist
- [ ] Manually trigger the curator scan (don't wait for Monday cron) via a test endpoint, confirm report generates
- [ ] Merge suggestions correctly cluster 3 known-similar test documents
- [ ] A knowledge gap from 5 clustered zero-result queries produces exactly 1 gap entry, not 5
- [ ] Drafted stub document does NOT appear in normal chat search until explicitly approved
- [ ] Approving a stub correctly flips status and triggers embedding

---

## DAY 4 — Follow-up Sequence Agent (#8) + Internal Communications Agent (#23)

**Goal:** Two multi-step, scheduled, multi-channel features that exercise the Day 1 autoflow/Inngest scheduling infra directly.

### Feature #8: Follow-up Sequence Agent

**SQL:** `supabase/migrations/0XX_sequences.sql`
```sql
CREATE TABLE sequences (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  created_by UUID NOT NULL REFERENCES users(id),
  name TEXT NOT NULL,
  prospect_context TEXT NOT NULL, -- e.g. "enterprise prospects who haven't replied"
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'scheduled', 'active', 'completed', 'cancelled')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE sequence_steps (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  sequence_id UUID NOT NULL REFERENCES sequences(id) ON DELETE CASCADE,
  step_order INT NOT NULL, -- 0, 1, 2 for Day 0, Day 3, Day 7
  send_offset_days INT NOT NULL,
  subject TEXT NOT NULL,
  body TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'scheduled', 'sent', 'opened', 'failed')),
  scheduled_for TIMESTAMPTZ,
  sent_at TIMESTAMPTZ,
  opened_at TIMESTAMPTZ,
  gmail_message_id TEXT
);

CREATE INDEX idx_sequence_steps_sequence ON sequence_steps(sequence_id, step_order);
```

**File:** `apps/backend/services/sequence_service.py`
```python
async def generate_sequence(org_id: str, prospect_context: str, user_id: str) -> dict:
    """1. Search KB for sales templates, objection handling, case studies relevant
    to prospect_context. 2. Call Gemini with a 3-email generation prompt (Day 0 intro,
    Day 3 value-add follow-up, Day 7 final nudge). 3. Insert sequence + 3 sequence_steps
    rows with status='pending'. Return for user review."""
    ...

async def schedule_sequence(sequence_id: str) -> None:
    """User clicked 'Schedule sequence'. For each step, compute scheduled_for =
    now() + send_offset_days. Set sequence.status='scheduled', step.status='scheduled'.
    Inngest step.sleepUntil pattern handles the actual delayed send (see below)."""
    ...

async def record_email_sent(step_id: str, gmail_message_id: str) -> None:
    ...

async def record_email_opened(step_id: str) -> None:
    """Called from Gmail webhook / read-receipt pixel if that infra exists —
    else flag as best-effort/optional tracking, grep for existing open-tracking pattern."""
    ...
```

**Inngest:** `apps/backend/inngest/sequence_dispatch.py`
```python
@inngest_client.create_function(
    fn_id="sequence-send-step",
    trigger=inngest.TriggerEvent(event="sequence/scheduled"),
)
async def send_sequence_step(ctx: inngest.Context, step: inngest.Step):
    sequence_id = ctx.event.data["sequence_id"]
    steps = await step.run("fetch-steps", lambda: get_sequence_steps(sequence_id))

    for s in steps:
        await step.sleep_until(f"wait-for-step-{s['id']}", s["scheduled_for"])
        result = await step.run(
            f"send-step-{s['id']}",
            lambda s=s: send_via_gmail_adapter(s)
        )
        await step.run(f"record-sent-{s['id']}", lambda s=s, r=result: record_email_sent(s["id"], r["message_id"]))
```

**Frontend:** `apps/frontend/app/sales/sequences/page.tsx` — generation form, per-step review/edit, "Schedule sequence" button, sequence status dashboard table (sent/opened per step).

**New endpoints:** `apps/backend/routers/sequences.py` — `POST /sequences/generate`, `POST /sequences/{id}/schedule`, `GET /sequences/{id}/status`.

### Feature #23: Internal Communications Agent

**File:** `apps/backend/services/internal_comms_service.py`
```python
async def draft_multichannel_announcement(org_id: str, request: str, user_id: str) -> dict:
    """e.g. request = "Send all-hands recap of Q3 planning".
    1. Search KB for Q3 OKRs, planning docs, decisions.
    2. Call Gemini three times (or one call with structured JSON output) for:
       email_version, slack_version, notion_version — same content, channel-appropriate format.
    3. Return all three as a draft for the single-view admin approval UI."""
    ...

async def approve_and_schedule_announcement(draft_id: str, scheduled_for: datetime) -> None:
    """On approval, create an autoflow_run-style scheduled dispatch (or directly an
    Inngest step.sleepUntil) that fires all three sends simultaneously at scheduled_for."""
    ...
```

**SQL:** `supabase/migrations/0XX_internal_comms.sql`
```sql
CREATE TABLE internal_announcements (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  request_text TEXT NOT NULL,
  email_draft TEXT,
  slack_draft TEXT,
  notion_draft TEXT,
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'approved', 'scheduled', 'sent', 'failed')),
  scheduled_for TIMESTAMPTZ,
  created_by UUID NOT NULL REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

**Inngest:** `apps/backend/inngest/announcement_dispatch.py`
```python
@inngest_client.create_function(
    fn_id="announcement-simultaneous-send",
    trigger=inngest.TriggerEvent(event="announcement/scheduled"),
)
async def send_announcement(ctx: inngest.Context, step: inngest.Step):
    announcement_id = ctx.event.data["announcement_id"]
    announcement = await step.run("fetch", lambda: get_announcement(announcement_id))

    await step.sleep_until("wait-for-scheduled-time", announcement["scheduled_for"])

    # Fire all three concurrently within the same step group
    await step.run("send-email", lambda: send_via_resend(announcement["email_draft"]))
    await step.run("post-slack", lambda: post_to_slack(announcement["slack_draft"]))
    await step.run("publish-notion", lambda: create_notion_page(announcement["notion_draft"]))
    await step.run("mark-sent", lambda: mark_announcement_sent(announcement_id))
```

**Frontend:** `apps/frontend/app/admin/announcements/page.tsx` — single-view three-tab draft review (Email / Slack / Notion), one "Approve All" action, scheduled-for datetime picker.

### End of Day 4 — Verification Checklist
- [ ] Generated sequence produces exactly 3 distinct, context-relevant emails
- [ ] Scheduling a sequence correctly delays sends by 0/3/7 days (test with shortened offsets, e.g. minutes, in dev)
- [ ] Sequence status dashboard accurately reflects sent/opened per step
- [ ] Announcement drafts generate three genuinely different formats from the same source content
- [ ] Approving an announcement fires email + Slack + Notion at the same scheduled instant (verify timestamps within seconds of each other)

---

## DAY 5 — Recruiting Agent (#20) + Sales Enablement Agent (#22)

**Goal:** The two highest-complexity (8/10, 7/10), highest external-integration agents. Built bespoke per the confirmed decision — no shared agent framework.

### Feature #20: Recruiting Agent

**File:** `apps/backend/services/recruiting_agent_service.py`
```python
async def generate_job_requisition(org_id: str, role_request: str, user_id: str) -> dict:
    """role_request e.g. "We need to hire a Senior Product Designer".
    1. Search KB: JD templates, compensation bands, role requirements, team structure docs.
    2. Call Gemini to generate 5 JD variants (startup-casual, enterprise-formal, and 3
       intermediate tones) using retrieved context for accuracy on comp/requirements.
    3. Return variants for user selection — DO NOT auto-publish yet."""
    ...

async def publish_job_to_ats(variant_text: str, ats_platform: Literal["greenhouse", "lever", "ashby"], org_id: str) -> dict:
    """Calls the already-provisioned ATS API. Greenhouse: POST /v1/jobs. Lever: POST /postings
    (reuse the public API pattern already built per Agent Roadmap — grep for existing
    Lever integration code). Returns external job posting ID + URL."""
    ...

async def create_hiring_tracker_page(job_id: str, ats_url: str, org_id: str) -> dict:
    """Creates a Notion page from a hiring-process template (sections: JD, pipeline stages,
    candidates, notes). Uses Notion API create-page-from-template pattern."""
    ...

async def draft_sourcing_templates(role_request: str, jd_variant: str) -> list[dict]:
    """Calls Gemini to draft 2-3 LinkedIn outreach message templates tailored to the role.
    NOTE: LinkedIn integration is read-only per the feature spec (sourcing research only,
    NOT auto-sending outreach) — these are drafts for the recruiter to send manually."""
    ...

async def notify_hiring_manager(org_id: str, role_request: str, job_id: str, slack_channel: str) -> None:
    """Slack message via existing Slack send adapter (built in Agent Roadmap V1 — reuse,
    do not rebuild)."""
    ...
```

**Orchestration endpoint:** `apps/backend/routers/recruiting.py`
```python
@router.post("/recruiting/requisitions/generate")
async def generate_requisition(body: RoleRequest):
    return await generate_job_requisition(body.org_id, body.role_request, body.user_id)

@router.post("/recruiting/requisitions/{req_id}/publish")
async def publish_requisition(req_id: str, body: PublishRequest):
    """body: {selected_variant_index, ats_platform}. Runs publish → tracker page →
    sourcing drafts → Slack notify in sequence, returns combined result."""
    job = await publish_job_to_ats(...)
    tracker = await create_hiring_tracker_page(job["id"], job["url"], ...)
    templates = await draft_sourcing_templates(...)
    await notify_hiring_manager(...)
    return {"job": job, "tracker": tracker, "sourcing_templates": templates}
```

**SQL:** `supabase/migrations/0XX_recruiting.sql`
```sql
CREATE TABLE job_requisitions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  role_request TEXT NOT NULL,
  jd_variants JSONB NOT NULL DEFAULT '[]', -- [{tone, text}, ...]
  selected_variant_index INT,
  ats_platform TEXT,
  ats_job_id TEXT,
  ats_url TEXT,
  notion_tracker_url TEXT,
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'Published')),
  created_by UUID NOT NULL REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

**Frontend:** `apps/frontend/app/hr/recruiting/new/page.tsx` — role input → 5-variant comparison view (tabs or side-by-side) → ATS platform selector → "Publish" triggers the full chain, shows combined result (job link, tracker link, sourcing template previews).

### Feature #22: Sales Enablement Agent

This feature has **two distinct flows** — Pre-Call Brief and RFP Response. Build as two services sharing only KB search utilities, per the no-shared-framework decision.

**Pre-Call Brief:**

**File:** `apps/backend/services/precall_brief_service.py`
```python
async def generate_precall_brief(org_id: str, prospect_name: str, company: str) -> dict:
    """1. Search KB: competitor analysis, pricing, case studies, product positioning,
    objection handling — scoped query per category for better recall than one combined query.
    2. Synthesize via Gemini into a 1-page brief: talking points, likely objections + responses,
    relevant case studies, pricing scenario.
    3. Return structured brief (not just a blob) so frontend can render sections distinctly."""
    sections = {
        "talking_points": await search_kb_and_synthesize(org_id, "talking points", company),
        "objections": await search_kb_and_synthesize(org_id, "objection handling", company),
        "case_studies": await search_kb_and_synthesize(org_id, "case studies", company),
        "pricing_scenario": await search_kb_and_synthesize(org_id, "pricing", company),
    }
    return sections
```

**New endpoint:** `POST /sales/precall-brief` — `{prospect_name, company}` → returns brief, target response time matters here (rep is about to get on a call) — ensure KB searches run concurrently via `asyncio.gather`, not sequentially.

**RFP Response:**

**File:** `apps/backend/services/rfp_response_service.py`
```python
async def extract_rfp_requirements(rfp_file_path: str) -> list[dict]:
    """Parse uploaded RFP PDF (reuse existing PDF extraction utility if present —
    grep for 'pdf' extraction in services/). Use Gemini to identify discrete
    requirements/questions as a structured list: [{id, requirement_text, category}]."""
    ...

async def match_requirements_to_kb(org_id: str, requirements: list[dict]) -> list[dict]:
    """For each requirement, run KB search for matching capability/case study/proof point.
    If no match found above a confidence threshold (e.g. 0.5), mark as a GAP:
    {requirement_id, status: 'gap', flag_message: 'We have no documentation on X — flagging for legal team'}.
    Else: {requirement_id, status: 'matched', source_doc, response_text}."""
    ...

async def generate_rfp_document(org_id: str, requirements_with_matches: list[dict]) -> str:
    """Assembles a complete Word document via python-docx:
    - Title page, requirement-by-requirement response sections, gap-flagged sections
      visually distinct (e.g. highlighted/red text) for legal team attention.
    Returns file path to generated .docx."""
    from docx import Document
    doc = Document()
    ...
    output_path = f"/tmp/rfp_response_{uuid4()}.docx"
    doc.save(output_path)
    return output_path
```

**New endpoints:** `apps/backend/routers/sales_enablement.py`
```python
@router.post("/sales/rfp/upload")
async def upload_rfp(file: UploadFile):
    """Save file, extract requirements, return requirements list for rep review
    before generation (rep may want to correct mis-parsed requirements)."""
    ...

@router.post("/sales/rfp/{rfp_id}/generate")
async def generate_rfp_response(rfp_id: str):
    """Runs match_requirements_to_kb + generate_rfp_document, returns download link
    to the .docx."""
    ...
```

**Frontend:**
- `apps/frontend/app/sales/precall-brief/page.tsx` — name/company input, sectioned brief display, optimized for fast pre-call scanning (large headers, scannable bullets).
- `apps/frontend/app/sales/rfp/page.tsx` — PDF upload → requirement review/edit table → "Generate Response" → download link + inline gap-flag summary ("3 requirements flagged for legal review").

### End of Day 5 — Verification Checklist
- [ ] Recruiting agent's 5 JD variants are genuinely distinct in tone, not superficially reworded
- [ ] Publishing to Greenhouse/Lever returns a real external job ID and live URL (test against provisioned sandbox/test org if available)
- [ ] Sourcing templates are clearly marked draft-only, no auto-send occurs
- [ ] Pre-call brief generates in an acceptable latency window (concurrent KB search confirmed, not sequential)
- [ ] RFP extraction correctly identifies discrete requirements from a real test RFP PDF
- [ ] Generated Word doc opens correctly in Word/Google Docs, gap-flagged sections are visually distinct

---

## DAY 6 — Executive Assistant Agent (#25), Calendar Intelligence (#51), Meeting Prep → Action Tracker (#44), Output Quality Scoring (#52), No-Code Autoflow Builder UI (#46)

**Goal:** Close out with the calendar-cluster features, instrumentation, and finally the visual layer on top of Day 1's autoflow engine.

### Feature #25: Executive Assistant Agent

**File:** `apps/backend/services/executive_assistant_service.py`
```python
async def generate_executive_briefing(org_id: str, request: str, recipients: list[str], user_id: str) -> dict:
    """e.g. request = "Prepare a briefing document for our board meeting on competitive positioning".
    1. Search KB across multiple angles concurrently: competitive landscape, differentiation,
       customer case studies, financial projections (asyncio.gather, same pattern as #22 pre-call brief).
    2. Synthesize into structured briefing via Gemini: executive_summary, market_context,
       competitive_advantages, risks, recommendations.
    3. Create Google Doc via Google Docs API (separate from #22's Word export, per decision).
    4. Send to recipients via Gmail send adapter.
    5. Optionally schedule a follow-up meeting via Google Calendar API."""
    sections = await gather_briefing_sections(org_id, request)
    doc_url = await create_google_doc_briefing(sections, request)
    await send_briefing_email(recipients, doc_url, request)
    return {"doc_url": doc_url, "sections": sections}

async def create_google_doc_briefing(sections: dict, title: str) -> str:
    """Uses Google Docs API: documents.create then batchUpdate to insert structured
    content (headers, bullet lists per section). Returns the doc's webViewLink."""
    ...

async def schedule_followup_meeting(org_id: str, attendees: list[str], topic: str, proposed_time: datetime) -> dict:
    """Google Calendar API events.insert. Optional — only called if user requests it."""
    ...
```

**New endpoint:** `POST /executive/briefing/generate` — `{request, recipients, schedule_followup: bool}`.

**Frontend:** `apps/frontend/app/executive/briefing/page.tsx` — request textarea, recipient picker, generate → shows Google Doc link + section preview + optional "schedule follow-up" toggle with datetime picker.

### Feature #51: Calendar Intelligence (Meeting Context)

**File:** `apps/backend/services/calendar_intelligence_service.py`
```python
async def sync_upcoming_meetings(org_id: str, user_id: str) -> list[dict]:
    """Google Calendar API events.list for next 24-48h. Store/cache in a meetings table
    (or reuse calendar_events if it already exists — grep first)."""
    ...

async def generate_meeting_prep_brief(meeting_id: str) -> dict:
    """1. Fetch meeting: attendee emails, topic/agenda from calendar event description.
    2. Cross-reference attendee emails/names against KB (e.g. CRM-linked documents, past
       interaction notes) if such linkage exists.
    3. Search KB for documents relevant to meeting topic.
    4. Synthesize prep brief via Gemini.
    5. Store, set available_at = meeting_start - 2 hours."""
    ...
```

**Inngest:** `apps/backend/inngest/calendar_brief_scheduler.py`
```python
@inngest_client.create_function(
    fn_id="generate-meeting-briefs",
    trigger=inngest.TriggerCron(cron="0 6,18 * * *"),  # morning and evening
)
async def generate_briefs_for_upcoming_meetings(ctx: inngest.Context, step: inngest.Step):
    meetings = await step.run("fetch-upcoming", lambda: get_meetings_needing_brief())
    for m in meetings:
        await step.run(f"brief-{m['id']}", lambda m=m: generate_meeting_prep_brief(m["id"]))
```

**Frontend:** `apps/frontend/app/dashboard/upcoming-meetings/page.tsx` — meeting list, click → prep brief view, "Share with attendees" one-click button (sends brief link via Gmail to all attendees).

### Feature #44: Meeting Prep → Action Item Tracker

**File:** `apps/backend/services/action_tracker_service.py`
```python
async def extract_action_items(meeting_notes: str, meeting_id: str) -> list[dict]:
    """User pastes notes after the meeting. Gemini extracts: [{action_text, owner, due_date}].
    Owner matching: fuzzy-match extracted names against org's users table."""
    ...

async def create_tracked_tasks(action_items: list[dict], target: Literal["notion", "asana", "linear"]) -> list[dict]:
    """Calls the appropriate already-provisioned API adapter. Reuse Notion adapter from
    #20/#23 if target == notion. For Asana/Linear, build thin adapter functions:
    create_asana_task(name, assignee, due_on), create_linear_issue(title, assignee, due_date)."""
    ...

async def check_incomplete_actions() -> None:
    """Inngest scheduled function, runs daily. Find tasks created via this feature where
    due_date has passed and status != complete (poll the target system's API for status,
    or rely on a webhook if Asana/Linear support task-completion webhooks — check provisioned
    scopes). Send reminder via existing notification pattern."""
    ...
```

**Inngest:** `apps/backend/inngest/action_item_reminders.py`
```python
@inngest_client.create_function(
    fn_id="check-incomplete-action-items",
    trigger=inngest.TriggerCron(cron="0 9 * * *"),  # daily 9am
)
async def daily_action_item_check(ctx: inngest.Context, step: inngest.Step):
    await step.run("check-incomplete", lambda: check_incomplete_actions())
```

**Frontend:** add to meeting prep view — "Log decisions from this meeting" textarea → extracted action items table (editable owner/due date before confirming) → "Create tasks" button with target system selector.

### Feature #52: Output Quality Scoring and Benchmarking

**File:** `apps/backend/services/quality_metrics_service.py`
```python
async def compute_message_quality_score(message_id: str) -> float:
    """Components, each weighted:
    - copy_count > 0: +3 points (used = high quality signal)
    - feedback == 'positive': +3, feedback == 'negative': -3, no feedback: 0
    - confidence_score (already computed at generation time, 0-1): scaled to 0-3
    - sources_count > 0: +1
    Normalize to a 0-10 scale. Store on the message row or a separate quality_scores table —
    grep for existing messages table schema first to decide column vs. new table."""
    ...

async def get_org_quality_trend(org_id: str, weeks: int = 8) -> dict:
    """SQL aggregation: AVG(quality_score) grouped by week, for the org, last N weeks.
    Also: GROUP BY query_category (if query categorization exists) to surface which
    query types are improving vs declining."""
    ...

async def check_quality_threshold_alerts(org_id: str) -> None:
    """If org has set a quality_alert_threshold (new column on organizations or a
    settings table), and this week's average < threshold, send admin notification."""
    ...
```

**SQL:** `supabase/migrations/0XX_quality_scoring.sql`
```sql
ALTER TABLE organizations ADD COLUMN quality_alert_threshold FLOAT DEFAULT NULL;

CREATE TABLE message_quality_scores (
  message_id UUID PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
  score FLOAT NOT NULL,
  copy_count INT NOT NULL DEFAULT 0,
  feedback TEXT,
  confidence_score FLOAT,
  sources_count INT NOT NULL DEFAULT 0,
  computed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

**New endpoint:** `apps/backend/routers/admin_quality.py`
```python
@router.get("/admin/quality-metrics")
async def get_quality_metrics(org_id: str = Depends(get_current_org), weeks: int = 8):
    return await get_org_quality_trend(org_id, weeks)

@router.patch("/admin/quality-metrics/threshold")
async def set_quality_threshold(body: SetThresholdRequest):
    ...
```

**Frontend:** `apps/frontend/app/admin/quality/page.tsx` — trend line chart (this week vs last week, 8-week sparkline), breakdown table by query category, threshold setting input.

### Feature #46: No-Code Autoflow Builder UI

This is the **visual layer on top of Day 1's already-built backend** (`autoflows` table, dispatch functions). No new backend trigger/action logic — only:

**New endpoints (thin CRUD over Day 1's tables):** `apps/backend/routers/autoflows.py`
```python
@router.get("/admin/autoflows")
async def list_autoflows(org_id: str = Depends(get_current_org)):
    ...

@router.post("/admin/autoflows")
async def create_autoflow(body: AutoflowCreate):  # reuses Day 1's Pydantic model
    ...

@router.patch("/admin/autoflows/{id}")
async def update_autoflow(id: str, body: AutoflowUpdate):
    ...

@router.delete("/admin/autoflows/{id}")
async def delete_autoflow(id: str):
    ...

@router.get("/admin/autoflows/{id}/runs")
async def get_autoflow_run_history(id: str):
    """Reads from autoflow_runs — lets admin debug why a flow did/didn't fire."""
    ...
```

**Frontend:** `apps/frontend/app/admin/autoflows/page.tsx` and `apps/frontend/components/admin/AutoflowBuilder.tsx`
```
Visual builder UI:
- WHEN dropdown: maps 1:1 to autoflows.trigger_type enum values
- IF confidence < [slider]: maps to autoflows.confidence_threshold
- THEN multi-step builder: each step is one entry in the actions JSONB array,
  step type dropdown maps to AutoflowActionType enum from Day 1's model
- "Add another action" button appends to the actions array (drag-to-reorder updates `order` field)
- Save button → POST/PATCH against the endpoints above
- Run history tab per autoflow → calls GET .../runs, shows status timeline
  (pending/running/completed/failed/held_for_approval) with timestamps and error messages
```

Use a library like `@dnd-kit/core` (already likely available via `pnpm`) for the drag-to-reorder action steps, rather than building custom drag logic.

### End of Day 6 — Verification Checklist
- [ ] Executive briefing creates a real, correctly formatted Google Doc (not just plain text dump)
- [ ] Meeting prep brief becomes available exactly 2 hours before a test meeting's start time
- [ ] Action items extracted from pasted notes correctly match owners to real org users
- [ ] Quality score computation produces sane 0-10 values across a range of test messages (one with positive feedback + copy, one with negative feedback, one neutral)
- [ ] No-code builder can create an autoflow via UI that — when triggered — fires correctly using Day 1's existing dispatch engine (this is the integration test that proves Day 1-6 cohere)
- [ ] Run history view accurately reflects a real autoflow execution, including a deliberately-failed test case

---

## Cross-Cutting Reminders for Implementation (Claude Code)

1. **Before touching `approval_requests`:** confirm real schema. Every snippet above using `approval_requests` is a placeholder.
2. **Before writing any vector/embedding SQL:** confirm actual embedding dimension from `services/embedding_service.py` — do not assume 1536.
3. **Reuse, don't rebuild:** Slack send adapter, Gmail send adapter, Notion create-page adapter, and the public API/Lever integration pattern all already exist from prior roadmaps — grep before writing new adapter code.
4. **No shared agent framework, no shared document export service** — these were explicit decisions. Resist the urge to refactor toward either mid-implementation even if duplication across Recruiting/Sales/Exec Assistant/Internal Comms looks repetitive.
5. **Autoflow engine is foundational** — Days 3-6 features that use scheduling/multi-step dispatch should register as autoflows where natural (Knowledge Curator scan, sequence sends, announcement sends) rather than building parallel cron/dispatch logic.
6. **Langfuse tracing:** every new Gemini call site (JD generation, brief synthesis, RFP matching, etc.) should get a Langfuse trace/span consistent with existing instrumentation — grep for the existing `@observe` or tracing decorator pattern and apply uniformly.