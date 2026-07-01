# Nirnaya IQ — V3 Feature Roadmap (6 Days)

## Confirmed Architecture Decisions

| Decision | Choice |
|---|---|
| PDF Export | Browser print CSS (zero new dependencies) |
| Real-Time Collaboration | Skipped — P3, out of scope |
| Shareable Links expiry | Never expire (simpler) |
| Intent Classification | Keyword matching (fast, free, zero token cost) |
| Caching | Redis (already in stack) |
| Confetti | canvas-confetti (~7kb) |
| Competitor list scope | Both org-level + user-level |
| Conversation archive threshold | 45 days default, configurable |

## Feature Index

| # | Feature | Priority | Day |
|---|---|---|---|
| #4 | No Documents Warning in Chat | P0 | 1 |
| #5 | Guided Onboarding Checklist | P0 | 1 |
| #19 | Document Tags + Collections | P1 | 2 |
| #20 | Prompt Template Library | P1 | 2 |
| #27 | Mobile Responsive Layout | P2 | 3 |
| #30 | Pinned/Starred Conversations | P2 | 3 |
| #42 | Conversation Branching | P2 | 3 |
| #25 | Conversation Export | P2 | 4 |
| #62 | Shareable Output Links | P2 | 4 |
| #51 | Query Intent Classification | P2 | 4 |
| #80 | Redis Caching Layer | P2 | 5 |
| #50 | Intelligent Document Recommendations | P2 | 5 |
| #91 | Question History / Query Logs | P2 | 5 |
| #102 | Competitor Mention Detection | P2 | 6 |
| #104 | Conversation Archive | P2 | 6 |

---

## Day 1 — P0 Foundations: Empty State Warning + Guided Onboarding

**Goal:** No new user should ever stare at a blank screen wondering what to do. No user should ever get a hallucinated AI response because they forgot to upload documents. These two features directly gate first-session retention.

**Features:** #4 No Documents Warning, #5 Guided Onboarding Checklist

---

### #4 — "No Documents" Warning in Chat

This is a two-state banner: one for zero documents, one for documents that are still processing.

**Backend — `GET /api/organizations/document-status`**

New lightweight endpoint that returns the document state for the current org without fetching the full document list:

```python
@router.get("/organizations/document-status")
async def get_document_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(
            func.count(Document.id).label("total"),
            func.count(Document.id).filter(Document.status == "ready").label("ready"),
            func.count(Document.id).filter(Document.status == "processing").label("processing"),
            func.count(Document.id).filter(Document.status == "pending").label("pending")
        ).where(Document.org_id == current_user.org_id)
    )
    row = result.one()
    return {
        "total": row.total,
        "ready": row.ready,
        "processing": row.processing + row.pending,  # combine both "not yet ready" states
        "has_ready": row.ready > 0
    }
```

**Frontend — `hooks/useDocumentStatus.ts`**

```tsx
export function useDocumentStatus() {
  return useQuery(
    ['document-status'],
    () => fetch('/api/organizations/document-status').then(r => r.json()),
    {
      refetchInterval: (data) => {
        // Poll every 5 seconds while documents are processing, stop when all ready
        return data?.processing > 0 ? 5000 : false
      }
    }
  )
}
```

**Frontend — `components/chat/DocumentStatusBanner.tsx`**

Three states: no documents, processing, and ready (banner hidden):

```tsx
import { AlertTriangle, Clock, Upload } from 'lucide-react'
import Link from 'next/link'

export function DocumentStatusBanner() {
  const { data } = useDocumentStatus()
  if (!data || data.has_ready) return null  // Hidden once at least one doc is ready

  // State 1: Zero documents at all
  if (data.total === 0) {
    return (
      <div className="mx-4 mt-3 flex items-start gap-3 rounded-lg border border-amber-200 bg-amber-50 dark:border-amber-800 dark:bg-amber-950 p-3">
        <AlertTriangle size={16} className="text-amber-500 shrink-0 mt-0.5" />
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-amber-800 dark:text-amber-200">
            Your knowledge base is empty
          </p>
          <p className="text-xs text-amber-700 dark:text-amber-300 mt-0.5">
            Upload documents before asking questions — without them, answers will be generic.
          </p>
        </div>
        <Link
          href="/documents?upload=true"
          className="text-xs font-medium text-amber-700 dark:text-amber-300 underline whitespace-nowrap shrink-0"
        >
          Upload now →
        </Link>
      </div>
    )
  }

  // State 2: Documents exist but none are ready yet
  if (data.total > 0 && data.processing > 0 && data.ready === 0) {
    return (
      <div className="mx-4 mt-3 flex items-center gap-3 rounded-lg border border-blue-200 bg-blue-50 dark:border-blue-800 dark:bg-blue-950 p-3">
        <Clock size={16} className="text-blue-500 animate-pulse shrink-0" />
        <p className="text-sm text-blue-800 dark:text-blue-200">
          Your {data.processing} document{data.processing > 1 ? 's are' : ' is'} still being
          processed — usually takes 30–60 seconds. You can ask questions once they're ready.
        </p>
      </div>
    )
  }

  return null
}
```

**Placement:** Render `<DocumentStatusBanner />` at the top of `app/chat/[id]/page.tsx` and `app/chat/new/page.tsx`, above the message list and below the chat header. It auto-dismisses (returns null) as soon as `has_ready` becomes true via the polling hook.

**Also disable the chat input** while no docs are ready:

```tsx
// In the chat input component
const { data: docStatus } = useDocumentStatus()
const inputDisabled = docStatus && !docStatus.has_ready

<Textarea
  disabled={inputDisabled}
  placeholder={inputDisabled
    ? "Upload documents to start asking questions..."
    : "Ask anything about your knowledge base..."
  }
  className={cn(inputDisabled && "opacity-50 cursor-not-allowed")}
/>
```

---

### #5 — Guided Onboarding Checklist

**Database Migration**

The spec says store progress in `organizations.metadata JSONB`. Ensure the column exists:

```sql
-- Run only if metadata column doesn't already exist
ALTER TABLE organizations
  ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}';
```

The onboarding state lives inside `metadata` as:
```json
{
  "onboarding": {
    "workspace_created": true,
    "first_doc_uploaded": false,
    "first_question_asked": false,
    "completed": false,
    "dismissed": false
  }
}
```

**Backend — Step tracking**

Add a utility function in `app/services/onboarding.py`:

```python
from sqlalchemy import update
from app.models import Organization

STEPS = ["workspace_created", "first_doc_uploaded", "first_question_asked"]

async def complete_onboarding_step(org_id: str, step: str, db: AsyncSession):
    """Mark an onboarding step complete. Idempotent."""
    org = await db.get(Organization, org_id)
    onboarding = org.metadata.get("onboarding", {})

    if onboarding.get(step):
        return  # Already done, skip DB write

    onboarding[step] = True

    # Check if all steps done
    all_done = all(onboarding.get(s) for s in STEPS)
    if all_done:
        onboarding["completed"] = True

    await db.execute(
        update(Organization)
        .where(Organization.id == org_id)
        .values(metadata=Organization.metadata.op('||')(
            {"onboarding": onboarding}
        ))
    )
    await db.commit()
```

**Wire up the three step triggers:**

1. `workspace_created` — already true the moment the org is created. Set it in `POST /api/auth/register` right after org creation:
```python
await complete_onboarding_step(org.id, "workspace_created", db)
```

2. `first_doc_uploaded` — trigger in `POST /api/documents/upload` after successful document creation:
```python
await complete_onboarding_step(current_user.org_id, "first_doc_uploaded", db)
```

3. `first_question_asked` — trigger in `POST /api/chat/messages` (or wherever a new user message is created) on the first successful send:
```python
await complete_onboarding_step(current_user.org_id, "first_question_asked", db)
```

**Backend — GET and PATCH routes:**

```python
# GET /api/organizations/onboarding — returns current step state
@router.get("/organizations/onboarding")
async def get_onboarding(current_user: User = Depends(get_current_user), ...):
    org = await db.get(Organization, current_user.org_id)
    return org.metadata.get("onboarding", {
        "workspace_created": True,
        "first_doc_uploaded": False,
        "first_question_asked": False,
        "completed": False,
        "dismissed": False
    })

# POST /api/organizations/onboarding/dismiss — user manually closes the banner
@router.post("/organizations/onboarding/dismiss")
async def dismiss_onboarding(current_user: User = Depends(get_current_user), ...):
    await complete_onboarding_step(current_user.org_id, "dismissed", db)
    # Actually sets dismissed: true in metadata
    return {"dismissed": True}
```

**Frontend — `components/onboarding/OnboardingChecklist.tsx`**

Install canvas-confetti:
```bash
npm install canvas-confetti
npm install -D @types/canvas-confetti
```

Full component:

```tsx
import { useEffect, useRef } from 'react'
import confetti from 'canvas-confetti'
import { Check, ChevronRight, X } from 'lucide-react'
import Link from 'next/link'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'

interface OnboardingState {
  workspace_created: boolean
  first_doc_uploaded: boolean
  first_question_asked: boolean
  completed: boolean
  dismissed: boolean
}

const STEPS = [
  {
    key: 'workspace_created',
    label: 'Create your workspace',
    cta: null,
    href: null,
  },
  {
    key: 'first_doc_uploaded',
    label: 'Upload your first document',
    cta: 'Upload now →',
    href: '/documents?upload=true',
  },
  {
    key: 'first_question_asked',
    label: 'Ask your first question',
    cta: 'Go to Chat →',
    href: '/chat/new',
  },
]

export function OnboardingChecklist() {
  const qc = useQueryClient()
  const confettiFired = useRef(false)

  const { data: state } = useQuery<OnboardingState>(
    ['onboarding'],
    () => fetch('/api/organizations/onboarding').then(r => r.json()),
    { refetchInterval: 3000 }  // Poll so steps update as user acts
  )

  const dismiss = useMutation(
    () => fetch('/api/organizations/onboarding/dismiss', { method: 'POST' }),
    { onSuccess: () => qc.invalidateQueries(['onboarding']) }
  )

  // Fire confetti exactly once when all steps complete
  useEffect(() => {
    if (state?.completed && !confettiFired.current) {
      confettiFired.current = true
      confetti({
        particleCount: 120,
        spread: 80,
        origin: { y: 0.4 },
        colors: ['#6366f1', '#8b5cf6', '#06b6d4', '#10b981']
      })
      // Auto-dismiss banner after 4 seconds
      setTimeout(() => dismiss.mutate(), 4000)
    }
  }, [state?.completed])

  // Don't render if dismissed or completed+confetti already done
  if (!state || state.dismissed) return null

  const completedCount = STEPS.filter(s => state[s.key as keyof OnboardingState]).length

  return (
    <div className="mx-4 mt-4 rounded-xl border border-indigo-200 bg-indigo-50 dark:border-indigo-800 dark:bg-indigo-950 p-4">
      <div className="flex items-start justify-between mb-3">
        <div>
          <p className="text-sm font-semibold text-indigo-900 dark:text-indigo-100">
            {state.completed ? "You're all set! 🎉" : "Get started with Nirnaya IQ"}
          </p>
          <p className="text-xs text-indigo-600 dark:text-indigo-400 mt-0.5">
            {completedCount} of {STEPS.length} steps complete
          </p>
        </div>
        <button
          onClick={() => dismiss.mutate()}
          className="text-indigo-400 hover:text-indigo-600 p-0.5 rounded"
        >
          <X size={14} />
        </button>
      </div>

      {/* Progress bar */}
      <div className="h-1.5 bg-indigo-200 dark:bg-indigo-800 rounded-full mb-3 overflow-hidden">
        <div
          className="h-full bg-indigo-500 rounded-full transition-all duration-500"
          style={{ width: `${(completedCount / STEPS.length) * 100}%` }}
        />
      </div>

      {/* Steps */}
      <div className="space-y-2">
        {STEPS.map((step) => {
          const done = !!state[step.key as keyof OnboardingState]
          return (
            <div key={step.key} className="flex items-center gap-2.5">
              <div className={cn(
                "w-5 h-5 rounded-full flex items-center justify-center shrink-0 border-2 transition-colors",
                done
                  ? "bg-indigo-500 border-indigo-500"
                  : "border-indigo-300 dark:border-indigo-600"
              )}>
                {done && <Check size={11} className="text-white" strokeWidth={3} />}
              </div>
              <span className={cn(
                "text-sm flex-1",
                done
                  ? "line-through text-indigo-400 dark:text-indigo-600"
                  : "text-indigo-800 dark:text-indigo-200"
              )}>
                {step.label}
              </span>
              {!done && step.href && (
                <Link
                  href={step.href}
                  className="text-xs font-medium text-indigo-600 dark:text-indigo-400 hover:underline flex items-center gap-0.5"
                >
                  {step.cta} <ChevronRight size={10} />
                </Link>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
```

**Placement:** Render `<OnboardingChecklist />` at the top of the main layout sidebar (below the "New Chat" button, above the conversation list). It's persistent across all pages until dismissed.

**End of Day 1 checklist:**
- [ ] `GET /api/organizations/document-status` returns total/ready/processing counts
- [ ] Chat banner renders correctly for all three states (empty, processing, has-ready)
- [ ] Chat input disabled with correct placeholder when no ready docs
- [ ] Banner auto-dismisses when first doc becomes ready (via polling)
- [ ] `organizations.metadata` JSONB column exists
- [ ] All three onboarding steps wire to correct backend triggers
- [ ] Onboarding banner renders in sidebar with progress bar
- [ ] Confetti fires exactly once on completion, banner auto-dismisses after 4s
- [ ] Manual dismiss (X button) works and persists

---

## Day 2 — Knowledge Base Organization: Tags + Prompt Templates

**Goal:** Documents become manageable at scale. Prompts stop being retyped. Both features compound in value the longer a team uses the product.

**Features:** #19 Document Tags + Collections, #20 Prompt Template Library

---

### #19 — Document Organization (Tags + Collections)

**Database Migration:**

```sql
-- Add tags to documents (may already exist from V2 Day 7 bulk tag feature)
ALTER TABLE documents
  ADD COLUMN IF NOT EXISTS tags TEXT[] NOT NULL DEFAULT '{}';

CREATE INDEX IF NOT EXISTS idx_documents_tags ON documents USING GIN(tags);
```

**Backend — Tag management**

Update `PATCH /api/documents/{id}` to accept tags:
```python
class DocumentUpdateBody(BaseModel):
    name: Optional[str]
    tags: Optional[List[str]]  # Full replacement, not append

@router.patch("/documents/{document_id}")
async def update_document(document_id: str, body: DocumentUpdateBody, ...):
    doc = await db.get(Document, document_id)
    if doc.org_id != current_user.org_id:
        raise HTTPException(403)
    if body.name is not None:
        doc.name = body.name
    if body.tags is not None:
        # Normalize: lowercase, strip whitespace, dedupe
        doc.tags = list(set(t.lower().strip() for t in body.tags if t.strip()))
    await db.commit()
    return doc
```

New endpoint to get all tags used by the org (for autocomplete):
```python
@router.get("/organizations/tags")
async def get_org_tags(current_user: User = Depends(get_current_user), ...):
    """Returns all unique tags used across org's documents, sorted by frequency."""
    result = await db.execute(
        select(func.unnest(Document.tags).label("tag"), func.count().label("count"))
        .where(Document.org_id == current_user.org_id)
        .group_by("tag")
        .order_by(func.count().desc())
    )
    return [{"tag": row.tag, "count": row.count} for row in result]
```

Update `GET /api/documents` to filter by tags (extend existing filters from V2):
```python
if tags:  # tags is List[str] from query param
    query = query.where(Document.tags.overlap(tags))  # PostgreSQL && operator
```

Update `hybrid_search` in `app/services/search.py` to accept tags filter:
```python
async def hybrid_search(
    query: str,
    org_id: str,
    document_id: Optional[str] = None,
    tags: Optional[List[str]] = None,  # NEW
    top_k: int = 5
) -> List[Chunk]:
    # Build filter for vector search
    filter_conditions = {"org_id": org_id}
    if document_id:
        filter_conditions["document_id"] = document_id
    if tags:
        # Join chunks → documents to filter by tag
        # Depends on your vector DB — for pgvector, add a JOIN
        filter_conditions["document_tags_overlap"] = tags
    ...
```

**Frontend — `components/documents/TagInput.tsx`**

Inline tag editor on each document row (appears on hover or click):

```tsx
import { useState, useRef } from 'react'
import { X, Plus } from 'lucide-react'

const PRESET_TAGS = ['HR', 'Product', 'Legal', 'Finance', 'Marketing', 'Operations', 'Engineering', 'Sales']

export function TagInput({
  documentId,
  initialTags,
  onUpdate
}: {
  documentId: string
  initialTags: string[]
  onUpdate: (tags: string[]) => void
}) {
  const [tags, setTags] = useState(initialTags)
  const [inputValue, setInputValue] = useState('')
  const [showSuggestions, setShowSuggestions] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const { data: orgTags } = useQuery(['org-tags'], () =>
    fetch('/api/organizations/tags').then(r => r.json())
  )

  const save = async (newTags: string[]) => {
    setTags(newTags)
    onUpdate(newTags)
    await fetch(`/api/documents/${documentId}`, {
      method: 'PATCH',
      body: JSON.stringify({ tags: newTags }),
      headers: { 'Content-Type': 'application/json' }
    })
  }

  const addTag = (tag: string) => {
    const normalized = tag.toLowerCase().trim()
    if (!normalized || tags.includes(normalized)) return
    save([...tags, normalized])
    setInputValue('')
    setShowSuggestions(false)
  }

  const removeTag = (tag: string) => save(tags.filter(t => t !== tag))

  const suggestions = (orgTags || [])
    .map((t: any) => t.tag)
    .filter((t: string) => t.includes(inputValue.toLowerCase()) && !tags.includes(t))
    .slice(0, 6)

  return (
    <div className="flex flex-wrap items-center gap-1 min-h-6">
      {tags.map(tag => (
        <span key={tag} className="flex items-center gap-1 px-2 py-0.5 bg-indigo-100 dark:bg-indigo-900 text-indigo-700 dark:text-indigo-300 rounded-full text-xs">
          {tag}
          <button onClick={() => removeTag(tag)} className="hover:text-indigo-900">
            <X size={10} />
          </button>
        </span>
      ))}
      <div className="relative">
        <input
          ref={inputRef}
          value={inputValue}
          onChange={e => { setInputValue(e.target.value); setShowSuggestions(true) }}
          onKeyDown={e => {
            if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); addTag(inputValue) }
            if (e.key === 'Backspace' && !inputValue && tags.length) removeTag(tags[tags.length - 1])
          }}
          onFocus={() => setShowSuggestions(true)}
          onBlur={() => setTimeout(() => setShowSuggestions(false), 150)}
          placeholder={tags.length === 0 ? "Add tag..." : ""}
          className="text-xs bg-transparent outline-none w-20 placeholder:text-muted-foreground"
        />
        {showSuggestions && (inputValue || tags.length === 0) && suggestions.length > 0 && (
          <div className="absolute top-full left-0 mt-1 bg-popover border rounded-lg shadow-md z-10 min-w-32">
            {suggestions.map((s: string) => (
              <button
                key={s}
                onMouseDown={() => addTag(s)}
                className="w-full text-left px-3 py-1.5 text-xs hover:bg-muted"
              >
                {s}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
```

**Tag filter in sidebar for chat scoping:**

In `components/chat/ChatScopeSelector.tsx` (new component), add a tag scope selector above the chat input:

```tsx
export function ChatScopeSelector({ onScopeChange }: { onScopeChange: (tags: string[]) => void }) {
  const [activeTags, setActiveTags] = useState<string[]>([])
  const { data: orgTags } = useQuery(['org-tags'], ...)

  if (!orgTags?.length) return null

  return (
    <div className="flex items-center gap-1.5 px-3 py-1.5 border-b flex-wrap">
      <span className="text-xs text-muted-foreground">Search in:</span>
      {(orgTags || []).slice(0, 8).map((t: any) => (
        <button
          key={t.tag}
          onClick={() => {
            const next = activeTags.includes(t.tag)
              ? activeTags.filter(x => x !== t.tag)
              : [...activeTags, t.tag]
            setActiveTags(next)
            onScopeChange(next)
          }}
          className={cn(
            "px-2 py-0.5 rounded-full text-xs border transition-colors",
            activeTags.includes(t.tag)
              ? "bg-indigo-500 text-white border-indigo-500"
              : "border-border text-muted-foreground hover:border-indigo-300"
          )}
        >
          {t.tag}
        </button>
      ))}
      {activeTags.length > 0 && (
        <button onClick={() => { setActiveTags([]); onScopeChange([]) }}
          className="text-xs text-muted-foreground hover:text-foreground">
          Clear
        </button>
      )}
    </div>
  )
}
```

Pass active tags into `execute_task()` via the chat message POST body.

---

### #20 — Prompt Template Library

**Database Migration:**

```sql
CREATE TABLE prompt_templates (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  created_by UUID NOT NULL REFERENCES users(id),
  title VARCHAR(255) NOT NULL,
  description TEXT,
  template_text TEXT NOT NULL,
  category VARCHAR(100) NOT NULL DEFAULT 'Other',
  is_shared BOOLEAN NOT NULL DEFAULT false,   -- Visible to whole org
  is_builtin BOOLEAN NOT NULL DEFAULT false,  -- Seeded starter templates
  use_count INTEGER NOT NULL DEFAULT 0,       -- Track popularity
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_prompt_templates_org ON prompt_templates(org_id);
CREATE INDEX idx_prompt_templates_category ON prompt_templates(org_id, category);
```

**Backend — Seed built-in templates**

Create a migration that inserts the three starter templates with `is_builtin=true` and `org_id=NULL` (global) — or seed them per org on creation:

```python
BUILTIN_TEMPLATES = [
    {
        "title": "Write an Email",
        "category": "Email",
        "description": "Professional email using company voice",
        "template_text": "Write a professional email to [person/role] about [topic], using our company voice and referencing our [relevant policy or document]. Keep the tone [formal/friendly] and include a clear call to action.",
        "is_builtin": True,
        "is_shared": True,
    },
    {
        "title": "Job Description",
        "category": "Job Description",
        "description": "Full JD with requirements and culture",
        "template_text": "Write a complete job description for a [seniority level] [job title] at our company. Include: role summary, key responsibilities (6-8 bullet points), required qualifications, nice-to-have skills, and a section about our company culture. Base requirements on our existing team documentation if available.",
        "is_builtin": True,
        "is_shared": True,
    },
    {
        "title": "Company Announcement",
        "category": "Announcement",
        "description": "Company-wide announcement draft",
        "template_text": "Draft a company-wide announcement about [topic/change]. Keep the tone [formal/casual]. Include: what is changing, why it matters, when it takes effect, and next steps employees should take. Reference any relevant policy documents.",
        "is_builtin": True,
        "is_shared": True,
    },
    {
        "title": "Policy Q&A",
        "category": "Policy Q&A",
        "description": "Answer employee questions about a policy",
        "template_text": "Answer the following employee question about our policies: [question]. Be concise and direct. If the answer has multiple parts, use bullet points. Cite the relevant policy document section.",
        "is_builtin": True,
        "is_shared": True,
    },
    {
        "title": "Meeting Prep Brief",
        "category": "Meeting Prep",
        "description": "Prep notes for an upcoming meeting",
        "template_text": "Prepare a brief for an upcoming meeting about [topic]. Include: background context from our knowledge base, key points to cover, potential questions to address, and any relevant decisions previously made. Keep it under 300 words.",
        "is_builtin": True,
        "is_shared": True,
    },
]
```

Seed these in a new Alembic migration or in the org creation flow.

**Backend — CRUD routes in `app/api/routes/templates.py`:**

```python
# GET /api/templates — list templates for current user
@router.get("/templates")
async def list_templates(
    category: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    ...
):
    query = select(PromptTemplate).where(
        or_(
            # Builtin (global) templates
            PromptTemplate.is_builtin == True,
            # Org shared templates
            and_(PromptTemplate.org_id == current_user.org_id, PromptTemplate.is_shared == True),
            # User's own private templates
            PromptTemplate.created_by == current_user.id,
        )
    )
    if category:
        query = query.where(PromptTemplate.category == category)
    query = query.order_by(PromptTemplate.use_count.desc(), PromptTemplate.created_at.desc())
    return await db.execute(query)

# POST /api/templates — create new template
@router.post("/templates")
async def create_template(body: CreateTemplateBody, ...):
    template = PromptTemplate(
        org_id=current_user.org_id,
        created_by=current_user.id,
        **body.dict()
    )
    db.add(template)
    await db.commit()
    return template

# PATCH /api/templates/{id} — update (creator or admin only)
# DELETE /api/templates/{id} — delete (creator or admin only)

# POST /api/templates/{id}/use — increment use_count
@router.post("/templates/{template_id}/use")
async def record_template_use(template_id: str, ...):
    await db.execute(
        update(PromptTemplate)
        .where(PromptTemplate.id == template_id)
        .values(use_count=PromptTemplate.use_count + 1)
    )
    await db.commit()
```

**Frontend — `components/chat/TemplateButton.tsx`**

Button in the chat input area (left side, next to attachment button):

```tsx
import { LayoutTemplate } from 'lucide-react'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'

const CATEGORIES = ['All', 'Email', 'Job Description', 'Announcement', 'Policy Q&A', 'Meeting Prep', 'Customer Response', 'Other']

export function TemplateButton({ onSelect }: { onSelect: (text: string) => void }) {
  const [activeCategory, setActiveCategory] = useState('All')
  const [search, setSearch] = useState('')

  const { data: templates } = useQuery(['templates', activeCategory], () =>
    fetch(`/api/templates${activeCategory !== 'All' ? `?category=${activeCategory}` : ''}`).then(r => r.json())
  )

  const filtered = (templates || []).filter((t: any) =>
    search === '' || t.title.toLowerCase().includes(search.toLowerCase())
  )

  const handleSelect = async (template: any) => {
    onSelect(template.template_text)
    await fetch(`/api/templates/${template.id}/use`, { method: 'POST' })
  }

  return (
    <Popover>
      <PopoverTrigger asChild>
        <button className="p-2 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
          title="Templates">
          <LayoutTemplate size={16} />
        </button>
      </PopoverTrigger>
      <PopoverContent className="w-96 p-0" align="start" side="top">
        <div className="p-3 border-b">
          <p className="text-sm font-semibold mb-2">Templates</p>
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Search templates..."
            className="w-full text-sm bg-muted rounded px-2.5 py-1.5 outline-none"
          />
        </div>
        {/* Category tabs */}
        <div className="flex gap-1 p-2 border-b overflow-x-auto">
          {CATEGORIES.map(cat => (
            <button
              key={cat}
              onClick={() => setActiveCategory(cat)}
              className={cn(
                "px-2.5 py-1 rounded text-xs whitespace-nowrap transition-colors",
                activeCategory === cat
                  ? "bg-indigo-100 dark:bg-indigo-900 text-indigo-700 dark:text-indigo-300"
                  : "text-muted-foreground hover:bg-muted"
              )}
            >
              {cat}
            </button>
          ))}
        </div>
        {/* Template list */}
        <div className="max-h-72 overflow-y-auto">
          {filtered.length === 0 ? (
            <p className="text-sm text-muted-foreground text-center py-6">No templates found</p>
          ) : (
            filtered.map((template: any) => (
              <button
                key={template.id}
                onClick={() => handleSelect(template)}
                className="w-full text-left px-3 py-2.5 hover:bg-muted border-b last:border-0 transition-colors"
              >
                <div className="flex items-start justify-between gap-2">
                  <p className="text-sm font-medium">{template.title}</p>
                  <span className="text-xs text-muted-foreground shrink-0 mt-0.5">{template.category}</span>
                </div>
                {template.description && (
                  <p className="text-xs text-muted-foreground mt-0.5">{template.description}</p>
                )}
              </button>
            ))
          )}
        </div>
        <div className="p-2 border-t">
          <button
            onClick={() => {/* Open create template modal */}}
            className="w-full text-xs text-center text-indigo-600 hover:underline py-1"
          >
            + Create new template
          </button>
        </div>
      </PopoverContent>
    </Popover>
  )
}
```

**"Save as Template" button on assistant messages:**

In `components/chat/AssistantMessage.tsx`, add a save button in the action row (alongside copy and feedback buttons):

```tsx
<button
  onClick={() => setCreateTemplateOpen(true)}
  className="p-1.5 rounded text-muted-foreground hover:text-foreground hover:bg-muted"
  title="Save prompt as template"
>
  <BookmarkPlus size={14} />
</button>

{/* Modal pre-filled with the user's message that generated this response */}
<CreateTemplateDialog
  open={createTemplateOpen}
  onClose={() => setCreateTemplateOpen(false)}
  initialText={userMessageThatGeneratedThis}
/>
```

The dialog has: Title, Category dropdown, Description (optional), Template text (pre-filled), Shared toggle (admin only).

**End of Day 2 checklist:**
- [ ] `tags TEXT[]` column on documents, GIN index created
- [ ] `PATCH /api/documents/{id}` accepts and normalizes tags
- [ ] `GET /api/organizations/tags` returns org tag list sorted by frequency
- [ ] Tag input component renders on document rows, autocompletes from org tags
- [ ] Document list filter by tag works end-to-end
- [ ] Chat scope selector shows org tags, passes selected tags to hybrid_search
- [ ] `prompt_templates` table created, 5 built-in templates seeded
- [ ] Template popover opens from chat input, shows categories
- [ ] Clicking template fills textarea
- [ ] Use count increments on selection
- [ ] "Save as Template" button on assistant messages opens pre-filled dialog

---

## Day 3 — UX Polish: Mobile Layout, Pinned Conversations, Branching

**Goal:** The product works on any device. Power users can organize and iterate on conversations without losing context.

**Features:** #27 Mobile Responsive Layout, #30 Pinned Conversations, #42 Conversation Branching

---

### #27 — Mobile Responsive Layout

This is a systematic pass across every page. No new components — just Tailwind responsive classes.

**1. Sidebar — collapse to drawer on mobile**

Modify `components/sidebar/Sidebar.tsx`:

```tsx
// Add mobile state
const [mobileOpen, setMobileOpen] = useState(false)

// Mobile overlay
{mobileOpen && (
  <div
    className="fixed inset-0 bg-black/50 z-40 md:hidden"
    onClick={() => setMobileOpen(false)}
  />
)}

// Sidebar — slide in on mobile, static on desktop
<aside className={cn(
  "fixed left-0 top-0 h-full z-50 w-64 bg-sidebar border-r flex flex-col transition-transform duration-200",
  "md:relative md:translate-x-0",  // Desktop: always visible
  mobileOpen ? "translate-x-0" : "-translate-x-full md:translate-x-0"  // Mobile: drawer
)}>
  {/* ... sidebar content ... */}
</aside>
```

**Hamburger button** — add to chat header, visible only on mobile:

```tsx
// In app/chat/[id]/page.tsx header
<button
  className="md:hidden p-2 rounded-md hover:bg-muted mr-2"
  onClick={() => setSidebarOpen(true)}
>
  <Menu size={20} />
</button>
```

Pass `setSidebarOpen` down via context or lift state to layout.

**2. Chat layout — full width on mobile**

```tsx
// app/layout.tsx or chat layout
<div className="flex h-screen overflow-hidden">
  <Sidebar open={sidebarOpen} onClose={() => setSidebarOpen(false)} />
  <main className="flex-1 flex flex-col min-w-0 overflow-hidden">
    {children}
  </main>
</div>
```

**3. Chat input — bottom-anchored on mobile**

The input is likely already at the bottom. Ensure it uses:
```tsx
<div className="sticky bottom-0 bg-background border-t p-3 md:p-4">
  {/* input */}
</div>
```

**4. Touch-friendly tap targets**

Audit all icon buttons — minimum `44px × 44px` on mobile. Apply:
```tsx
// Replace p-1 with p-2.5 on action buttons in mobile view
className="p-1.5 md:p-1 rounded"  // Larger on mobile
```

Specific targets to fix:
- Sidebar conversation items: add `py-2.5 md:py-1.5`
- Copy button: ensure `min-h-[44px] min-w-[44px] md:min-h-auto md:min-w-auto`
- Feedback thumbs: same treatment
- Document table rows: `py-3 md:py-2`

**5. Documents page — single column on mobile**

```tsx
// Document table → card list on mobile
<div className="hidden md:block">
  <DocumentTable documents={documents} />
</div>
<div className="md:hidden space-y-2">
  {documents.map(doc => <DocumentCard key={doc.id} document={doc} />)}
</div>
```

`DocumentCard` for mobile:
```tsx
export function DocumentCard({ document }: { document: Document }) {
  return (
    <div className="border rounded-lg p-3 flex items-center gap-3">
      <FileIcon type={document.file_type} className="shrink-0" />
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium truncate">{document.name}</p>
        <p className="text-xs text-muted-foreground">{formatDate(document.created_at)}</p>
        <div className="flex flex-wrap gap-1 mt-1">
          {document.tags.map(t => <TagBadge key={t} tag={t} />)}
        </div>
      </div>
      <StatusBadge status={document.status} />
    </div>
  )
}
```

**6. Settings page — stack on mobile**

```tsx
// Settings layout: side tabs on desktop, top tabs on mobile
<div className="flex flex-col md:flex-row gap-6">
  <nav className="flex md:flex-col gap-1 overflow-x-auto md:w-48 shrink-0">
    {/* Settings nav tabs */}
  </nav>
  <div className="flex-1 min-w-0">
    {/* Settings content */}
  </div>
</div>
```

---

### #30 — Pinned/Starred Conversations

**Database Migration:**

```sql
ALTER TABLE conversations
  ADD COLUMN IF NOT EXISTS is_pinned BOOLEAN NOT NULL DEFAULT false;

-- Index for fast pinned-first sorting
CREATE INDEX idx_conversations_pinned ON conversations(org_id, is_pinned, updated_at DESC);
```

**Backend — Update `PATCH /api/chat/conversations/{id}`**

The route already exists. Add `is_pinned` to the update body:
```python
class ConversationUpdateBody(BaseModel):
    title: Optional[str]
    is_pinned: Optional[bool]  # ADD THIS

@router.patch("/chat/conversations/{conversation_id}")
async def update_conversation(conversation_id: str, body: ConversationUpdateBody, ...):
    conversation.title = body.title or conversation.title
    if body.is_pinned is not None:
        conversation.is_pinned = body.is_pinned
    await db.commit()
```

Update `GET /api/chat/conversations` to return pinned conversations first:
```python
query = query.order_by(
    Conversation.is_pinned.desc(),  # Pinned first
    Conversation.updated_at.desc()   # Then by recency
)
```

**Frontend — `components/sidebar/ConversationItem.tsx`**

Add star button on hover, and pinned indicator:

```tsx
const [isPinned, setIsPinned] = useState(conversation.is_pinned)

const togglePin = async (e: React.MouseEvent) => {
  e.preventDefault()
  e.stopPropagation()
  const newValue = !isPinned
  setIsPinned(newValue)  // Optimistic update
  await fetch(`/api/chat/conversations/${conversation.id}`, {
    method: 'PATCH',
    body: JSON.stringify({ is_pinned: newValue }),
    headers: { 'Content-Type': 'application/json' }
  })
  queryClient.invalidateQueries(['conversations'])
}

return (
  <div className="group relative flex items-center gap-2 px-2 py-1.5 rounded-lg hover:bg-muted cursor-pointer">
    {/* Pin indicator — visible on all pinned, not just hover */}
    {isPinned && <Pin size={10} className="text-indigo-400 shrink-0" />}

    <span className="flex-1 text-sm truncate">{conversation.title}</span>

    {/* Pin button — visible on hover (or always if pinned) */}
    <button
      onClick={togglePin}
      className={cn(
        "p-1 rounded transition-colors shrink-0",
        isPinned
          ? "text-indigo-500 opacity-100"
          : "text-muted-foreground opacity-0 group-hover:opacity-100"
      )}
      title={isPinned ? "Unpin" : "Pin conversation"}
    >
      <Pin size={12} className={isPinned ? "fill-current" : ""} />
    </button>
  </div>
)
```

**Pinned section header in sidebar:**

In `components/sidebar/ConversationList.tsx`:
```tsx
const pinned = conversations.filter(c => c.is_pinned)
const unpinned = conversations.filter(c => !c.is_pinned)

return (
  <div className="flex-1 overflow-y-auto">
    {pinned.length > 0 && (
      <>
        <p className="px-3 py-1 text-xs font-medium text-muted-foreground uppercase tracking-wider">
          Pinned
        </p>
        {pinned.map(c => <ConversationItem key={c.id} conversation={c} />)}
        <div className="border-t my-1" />
      </>
    )}
    {unpinned.map(c => <ConversationItem key={c.id} conversation={c} />)}
  </div>
)
```

---

### #42 — Conversation Branching

**Database Migration:**

```sql
ALTER TABLE messages
  ADD COLUMN IF NOT EXISTS parent_message_id UUID REFERENCES messages(id),
  ADD COLUMN IF NOT EXISTS branch_index INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS is_active_branch BOOLEAN NOT NULL DEFAULT true;
-- parent_message_id: the assistant message being regenerated
-- branch_index: 0 = original, 1 = first regen, 2 = second, etc.
-- is_active_branch: which branch is currently shown
```

**Backend — `POST /api/chat/messages/{id}/regenerate`**

```python
@router.post("/chat/messages/{message_id}/regenerate")
async def regenerate_message(
    message_id: str,
    body: RegenerateBody,  # { refinement?: str }
    current_user: User = Depends(get_current_user),
    ...
):
    # Get the original assistant message
    original = await db.get(Message, message_id)
    if not original or original.role != 'assistant':
        raise HTTPException(400, "Can only regenerate assistant messages")

    # Get the user message that preceded it
    user_message = await get_preceding_user_message(original)

    # Build the query: original query + optional refinement
    query = user_message.content
    if body.refinement:
        query = f"{query}\n\n[Refinement: {body.refinement}]"

    # Mark all existing branches for this message as inactive
    await db.execute(
        update(Message)
        .where(Message.parent_message_id == message_id)
        .values(is_active_branch=False)
    )

    # Count existing branches for this message
    branch_count = await count_branches(message_id)

    # Create new message with parent reference — stream it
    new_message = Message(
        conversation_id=original.conversation_id,
        role='assistant',
        content='',  # Will be streamed
        parent_message_id=message_id,
        branch_index=branch_count + 1,
        is_active_branch=True
    )
    db.add(new_message)
    await db.commit()

    # Stream the regenerated response (same SSE as normal messages)
    return StreamingResponse(
        execute_task_stream(query=query, message_id=new_message.id, ...),
        media_type="text/event-stream"
    )
```

**Frontend — `components/chat/AssistantMessage.tsx`**

Add regenerate button and branch navigator:

```tsx
const [showRefinementInput, setShowRefinementInput] = useState(false)
const [refinement, setRefinement] = useState('')
const [currentBranch, setCurrentBranch] = useState(message.branch_index)
const totalBranches = message.total_branches || 1  // From API response

const regenerate = async () => {
  setShowRefinementInput(false)
  // Trigger streaming regeneration
  await startRegeneration(message.id, refinement || undefined)
  setRefinement('')
}

return (
  <div className="group">
    {/* Message content */}
    <AssistantMessageContent content={message.content} />

    {/* Action row */}
    <div className="flex items-center gap-1 mt-1.5 opacity-0 group-hover:opacity-100 transition-opacity">
      {/* Branch navigator — only shown if multiple branches exist */}
      {totalBranches > 1 && (
        <div className="flex items-center gap-1 mr-2 text-xs text-muted-foreground">
          <button onClick={() => switchBranch(message.id, currentBranch - 1)}
            disabled={currentBranch === 0}>
            <ChevronLeft size={14} />
          </button>
          <span>{currentBranch + 1}/{totalBranches}</span>
          <button onClick={() => switchBranch(message.id, currentBranch + 1)}
            disabled={currentBranch === totalBranches - 1}>
            <ChevronRight size={14} />
          </button>
        </div>
      )}

      <button onClick={() => setShowRefinementInput(!showRefinementInput)}
        className="flex items-center gap-1 px-2 py-1 rounded text-xs text-muted-foreground hover:bg-muted hover:text-foreground">
        <RefreshCw size={12} /> Regenerate
      </button>

      {/* ... copy, feedback buttons ... */}
    </div>

    {/* Refinement input — shown when regenerate clicked */}
    {showRefinementInput && (
      <div className="mt-2 flex gap-2">
        <input
          value={refinement}
          onChange={e => setRefinement(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && regenerate()}
          placeholder="Optional: make it more formal, focus on X, use different approach..."
          className="flex-1 text-sm bg-muted rounded px-3 py-1.5 outline-none"
          autoFocus
        />
        <button onClick={regenerate}
          className="px-3 py-1.5 bg-primary text-primary-foreground rounded text-sm">
          Go
        </button>
        <button onClick={() => setShowRefinementInput(false)}
          className="px-2 py-1.5 rounded text-muted-foreground hover:bg-muted">
          <X size={14} />
        </button>
      </div>
    )}
  </div>
)
```

**End of Day 3 checklist:**
- [ ] Sidebar collapses to drawer on mobile, hamburger button in header
- [ ] Chat layout full-width on mobile, input sticky at bottom
- [ ] All tap targets at least 44px on mobile
- [ ] Documents page uses card layout on mobile
- [ ] Settings page stacks tabs on mobile
- [ ] `is_pinned` column on conversations
- [ ] Pin toggle on conversation hover, filled icon when pinned
- [ ] Pinned conversations appear in own section at top of sidebar
- [ ] `parent_message_id`, `branch_index`, `is_active_branch` on messages
- [ ] `POST /api/chat/messages/{id}/regenerate` streams new response
- [ ] Regenerate button with optional refinement input on assistant messages
- [ ] Branch navigator (1/2, 2/2) shown when multiple branches exist

---

## Day 4 — Conversation Export, Shareable Links, Intent Classification

**Goal:** Content generated inside Nirnaya IQ can leave it. Every shared link is a growth loop.

**Features:** #25 Conversation Export, #62 Shareable Output Links, #51 Query Intent Classification

---

### #25 — Conversation Export (Markdown + PDF via Print CSS)

**Backend — `GET /api/chat/conversations/{id}/export`**

```python
@router.get("/chat/conversations/{conversation_id}/export")
async def export_conversation(
    conversation_id: str,
    format: str = "markdown",  # "markdown" | "json"
    current_user: User = Depends(get_current_user),
    ...
):
    conversation = await get_conversation_with_messages(conversation_id)
    if conversation.org_id != current_user.org_id:
        raise HTTPException(403)

    if format == "json":
        return conversation  # Raw JSON

    # Build markdown
    lines = [
        f"# Nirnaya IQ Export",
        f"**Conversation:** {conversation.title}",
        f"**Exported:** {datetime.utcnow().strftime('%B %d, %Y at %H:%M UTC')}",
        f"**Organization:** {conversation.org.name}",
        "",
        "---",
        "",
    ]
    for msg in conversation.messages:
        role_label = "**You**" if msg.role == "user" else "**Nirnaya IQ**"
        lines.append(f"{role_label}")
        lines.append("")
        lines.append(msg.content)
        if msg.sources:
            lines.append("")
            sources_text = ", ".join(s.name for s in msg.sources[:3])
            lines.append(f"*Sources: {sources_text}*")
        lines.append("")
        lines.append("---")
        lines.append("")

    markdown_content = "\n".join(lines)
    return Response(
        content=markdown_content,
        media_type="text/markdown",
        headers={
            "Content-Disposition": f'attachment; filename="{slugify(conversation.title)}.md"'
        }
    )
```

**Frontend — `components/chat/ExportButton.tsx`**

Add an export button to the conversation header (top right, alongside other actions):

```tsx
import { Download, Printer, FileText } from 'lucide-react'
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger
} from '@/components/ui/dropdown-menu'

export function ExportButton({ conversationId, title }: {
  conversationId: string
  title: string
}) {
  const exportMarkdown = async () => {
    const response = await fetch(`/api/chat/conversations/${conversationId}/export?format=markdown`)
    const blob = await response.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${title}.md`
    a.click()
    URL.revokeObjectURL(url)
  }

  const exportPDF = () => {
    // Open print-friendly view in new tab → user prints/saves as PDF
    window.open(`/print/conversation/${conversationId}`, '_blank')
  }

  const copyAll = async () => {
    const response = await fetch(`/api/chat/conversations/${conversationId}/export?format=markdown`)
    const text = await response.text()
    await navigator.clipboard.writeText(text)
    // Show "Copied!" toast
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button className="p-2 rounded-md hover:bg-muted text-muted-foreground">
          <Download size={16} />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem onClick={exportMarkdown}>
          <FileText size={14} className="mr-2" /> Download as Markdown
        </DropdownMenuItem>
        <DropdownMenuItem onClick={exportPDF}>
          <Printer size={14} className="mr-2" /> Save as PDF
        </DropdownMenuItem>
        <DropdownMenuItem onClick={copyAll}>
          <Copy size={14} className="mr-2" /> Copy all text
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
```

**Print view — `app/print/conversation/[id]/page.tsx`**

A clean, print-only page:
```tsx
export default async function PrintConversation({ params }: { params: { id: string } }) {
  const conversation = await getConversationServerSide(params.id)

  return (
    <div className="print-page max-w-2xl mx-auto p-8 font-sans">
      <style>{`
        @media print {
          .no-print { display: none !important; }
          body { font-size: 12pt; }
          h1 { font-size: 18pt; }
        }
        @page { margin: 2cm; }
      `}</style>

      <div className="no-print mb-6 flex justify-between items-center">
        <p className="text-sm text-gray-500">Print this page or save as PDF using Ctrl+P / Cmd+P</p>
        <button onClick={() => window.print()} className="px-4 py-2 bg-black text-white rounded text-sm">
          Print / Save PDF
        </button>
      </div>

      <h1 className="text-2xl font-bold mb-1">{conversation.title}</h1>
      <p className="text-sm text-gray-500 mb-8">
        Exported from Nirnaya IQ · {formatDate(new Date())}
      </p>
      <hr className="mb-8" />

      {conversation.messages.map(msg => (
        <div key={msg.id} className="mb-6">
          <p className="text-xs font-bold uppercase tracking-wider text-gray-400 mb-1">
            {msg.role === 'user' ? 'You' : 'Nirnaya IQ'}
          </p>
          <div className="prose prose-sm max-w-none">
            <ReactMarkdown>{msg.content}</ReactMarkdown>
          </div>
          {msg.sources?.length > 0 && (
            <p className="text-xs text-gray-400 mt-2 italic">
              Sources: {msg.sources.map(s => s.name).join(', ')}
            </p>
          )}
          <hr className="mt-6" />
        </div>
      ))}

      <p className="text-xs text-gray-300 text-center mt-8">
        Generated by Nirnaya IQ
      </p>
    </div>
  )
}
```

---

### #62 — Shareable Output Links

**Database Migration:**

```sql
CREATE TABLE shared_outputs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  message_id UUID NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  created_by UUID NOT NULL REFERENCES users(id),
  token VARCHAR(64) NOT NULL UNIQUE,  -- Random token for the public URL
  is_active BOOLEAN NOT NULL DEFAULT true,
  allow_sharing BOOLEAN NOT NULL DEFAULT true,  -- Org-level override
  view_count INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
  -- No expires_at column per decision: never expire
);

CREATE INDEX idx_shared_outputs_token ON shared_outputs(token);
```

Also add an org-level sharing toggle:
```sql
ALTER TABLE organizations
  ADD COLUMN IF NOT EXISTS allow_output_sharing BOOLEAN NOT NULL DEFAULT true;
```

**Backend — Share routes in `app/api/routes/sharing.py`:**

```python
# POST /api/chat/messages/{id}/share — create shareable link
@router.post("/chat/messages/{message_id}/share")
async def share_message(message_id: str, current_user: User = Depends(get_current_user), ...):
    # Check org allows sharing
    org = await db.get(Organization, current_user.org_id)
    if not org.allow_output_sharing:
        raise HTTPException(403, "Sharing is disabled for your organization")

    # Check if already shared
    existing = await db.execute(
        select(SharedOutput).where(
            SharedOutput.message_id == message_id,
            SharedOutput.is_active == True
        )
    )
    if existing.scalar():
        return { "url": build_share_url(existing.scalar().token) }

    token = secrets.token_urlsafe(32)
    shared = SharedOutput(
        message_id=message_id,
        org_id=current_user.org_id,
        created_by=current_user.id,
        token=token
    )
    db.add(shared)
    await db.commit()
    return { "url": build_share_url(token), "token": token }

def build_share_url(token: str) -> str:
    return f"{settings.APP_URL}/share/{token}"

# GET /api/share/{token} — public endpoint, no auth required
@router.get("/share/{token}")
async def get_shared_output(token: str, ...):
    shared = await db.execute(
        select(SharedOutput).where(
            SharedOutput.token == token,
            SharedOutput.is_active == True
        )
    )
    if not shared:
        raise HTTPException(404, "This link doesn't exist or has been removed")

    # Increment view count
    await db.execute(
        update(SharedOutput).where(SharedOutput.token == token)
        .values(view_count=SharedOutput.view_count + 1)
    )

    message = await get_message_with_sources(shared.message_id)
    org = await db.get(Organization, shared.org_id)
    return {
        "content": message.content,
        "sources": message.sources,
        "org_name": org.name,
        "created_at": shared.created_at
    }

# DELETE /api/chat/messages/{id}/share — revoke link
@router.delete("/chat/messages/{message_id}/share")
async def revoke_share(message_id: str, ...):
    await db.execute(
        update(SharedOutput)
        .where(SharedOutput.message_id == message_id, SharedOutput.org_id == current_user.org_id)
        .values(is_active=False)
    )
    await db.commit()
```

**Frontend — Share button on assistant messages:**

```tsx
// In AssistantMessage.tsx action row
const [shareUrl, setShareUrl] = useState<string | null>(null)
const [sharing, setSharing] = useState(false)

const handleShare = async () => {
  setSharing(true)
  const { url } = await fetch(`/api/chat/messages/${message.id}/share`, {
    method: 'POST'
  }).then(r => r.json())
  setShareUrl(url)
  await navigator.clipboard.writeText(url)
  setSharing(false)
  toast.success("Link copied to clipboard!")
}

<button onClick={handleShare} disabled={sharing}
  className="p-1.5 rounded text-muted-foreground hover:bg-muted hover:text-foreground"
  title="Share this output">
  {sharing ? <Loader2 size={14} className="animate-spin" /> : <Share2 size={14} />}
</button>
```

**Public share page — `app/share/[token]/page.tsx`**

```tsx
export default async function SharePage({ params }: { params: { token: string } }) {
  const data = await fetch(`${process.env.API_URL}/api/share/${params.token}`)
    .then(r => r.json())
    .catch(() => null)

  if (!data) return <NotFoundState />

  return (
    <div className="min-h-screen bg-gradient-to-br from-indigo-50 to-white dark:from-gray-900 dark:to-gray-950">
      <div className="max-w-2xl mx-auto px-4 py-12">
        {/* Header */}
        <div className="flex items-center gap-2 mb-8">
          <BrainIcon className="text-indigo-500" />
          <span className="text-sm font-medium text-muted-foreground">
            {data.org_name} · Nirnaya IQ
          </span>
        </div>

        {/* Output */}
        <div className="bg-white dark:bg-gray-900 rounded-2xl border shadow-sm p-6 mb-6">
          <div className="prose prose-sm dark:prose-invert max-w-none">
            <ReactMarkdown>{data.content}</ReactMarkdown>
          </div>
          {data.sources?.length > 0 && (
            <div className="mt-4 pt-4 border-t flex flex-wrap gap-2">
              {data.sources.map((s: any) => (
                <span key={s.id} className="text-xs px-2 py-1 bg-muted rounded-full flex items-center gap-1">
                  <FileText size={10} /> {s.name}
                </span>
              ))}
            </div>
          )}
        </div>

        {/* CTA watermark */}
        <div className="text-center">
          <p className="text-sm text-muted-foreground mb-3">
            This was generated by Nirnaya IQ — an AI knowledge base for teams.
          </p>
          <Link href="https://companybrain.app"
            className="inline-flex items-center gap-2 px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm font-medium hover:bg-indigo-700 transition-colors">
            Create your own Nirnaya IQ →
          </Link>
        </div>
      </div>
    </div>
  )
}
```

**Admin toggle** in `Settings > AI` page:
```tsx
<div className="flex items-center justify-between">
  <div>
    <p className="text-sm font-medium">Allow output sharing</p>
    <p className="text-xs text-muted-foreground">Team members can generate public links for AI outputs</p>
  </div>
  <Switch
    checked={org.allow_output_sharing}
    onCheckedChange={v => updateOrgSettings({ allow_output_sharing: v })}
  />
</div>
```

---

### #51 — Query Intent Classification (Keyword Matching)

**Backend — `app/services/intent.py`**

```python
from enum import Enum
import re

class QueryIntent(str, Enum):
    FACTUAL_QA = "factual_qa"
    TASK_GENERATION = "task_generation"
    ANALYSIS = "analysis"
    SEARCH = "search"

# Keyword signals per intent
INTENT_SIGNALS = {
    QueryIntent.TASK_GENERATION: [
        r'\bwrite\b', r'\bdraft\b', r'\bcreate\b', r'\bgenerate\b', r'\bcompose\b',
        r'\bmake\b.*\b(email|message|doc|template|jd|description|announcement|post)\b',
        r'\bprepare\b', r'\bformat\b', r'\bproduce\b', r'\bcraft\b'
    ],
    QueryIntent.ANALYSIS: [
        r'\banalyze\b', r'\banalyse\b', r'\bcompare\b', r'\bsummarize\b', r'\bsummarise\b',
        r'\bbreakdown\b', r'\bbreak down\b', r'\bevaluate\b', r'\bassess\b', r'\breview\b',
        r'\blist all\b', r'\bgive me a summary\b', r'\boverview\b'
    ],
    QueryIntent.SEARCH: [
        r'\bfind\b', r'\bsearch\b', r'\blook up\b', r'\blocate\b', r'\bshow me\b',
        r'\bwhere (is|are|can)\b', r'\bwhich documents?\b'
    ],
    # FACTUAL_QA is the default (what, who, when, how, is/are/does, why)
    QueryIntent.FACTUAL_QA: [
        r'^(what|who|when|where|how|why|is|are|does|do|can|should|will)\b',
        r'\b(policy|rule|procedure|process|guideline|requirement)\b.*\?',
        r'\?$'
    ]
}

def classify_intent(query: str) -> QueryIntent:
    """Classify query intent using keyword matching. O(n) — no LLM call needed."""
    q = query.lower().strip()

    # Check task generation first (highest priority)
    for pattern in INTENT_SIGNALS[QueryIntent.TASK_GENERATION]:
        if re.search(pattern, q):
            return QueryIntent.TASK_GENERATION

    # Then analysis
    for pattern in INTENT_SIGNALS[QueryIntent.ANALYSIS]:
        if re.search(pattern, q):
            return QueryIntent.ANALYSIS

    # Then search
    for pattern in INTENT_SIGNALS[QueryIntent.SEARCH]:
        if re.search(pattern, q):
            return QueryIntent.SEARCH

    # Default: factual Q&A
    return QueryIntent.FACTUAL_QA
```

**System prompts per intent — `app/services/prompts.py`**

```python
INTENT_SYSTEM_PROMPTS = {
    QueryIntent.FACTUAL_QA: """You are a helpful knowledge base assistant.
Answer the user's question concisely and directly based on the provided context.
Cite specific documents inline using the format [Source: Document Name].
If the context doesn't contain the answer, say so clearly — do not make up information.
Keep responses under 200 words unless the question requires more detail.""",

    QueryIntent.TASK_GENERATION: """You are a professional writing assistant with full knowledge of the organization.
Generate the requested document, email, or content in full — ready to copy and use immediately.
Use the provided knowledge base context to make the output specific and accurate to this organization.
Do not include meta-commentary like "here is the email:" — just output the content directly.
Format with clear structure (headers, bullet points where appropriate).""",

    QueryIntent.ANALYSIS: """You are a senior analyst with access to the organization's knowledge base.
Provide a structured, comprehensive analysis of the requested topic.
Use headers (##) to organize your response into clear sections.
Cite sources inline. Highlight key findings, patterns, and recommendations.
Be thorough — depth is valued over brevity for analysis requests.""",

    QueryIntent.SEARCH: """You are a precise search assistant.
Find and surface the most relevant information from the knowledge base for the user's search.
List results clearly with document names and relevant excerpts.
If multiple documents are relevant, show all of them.
Be factual and concise — let the source material speak.""",
}
```

**Wire into `execute_task()`:**

```python
async def execute_task(query: str, org_id: str, ...):
    # Classify intent — zero cost, instant
    intent = classify_intent(query)

    # Get intent-specific system prompt
    base_prompt = INTENT_SYSTEM_PROMPTS[intent]

    # Add org instructions on top (from Day 9 V2)
    system_prompt = await build_system_prompt(org_id, base_prompt)

    # ... rest of execute_task unchanged ...
```

**SSE event to frontend** (so UI can adapt):
```python
yield f"data: {json.dumps({'type': 'intent', 'intent': intent.value})}\n\n"
```

Frontend can use this to show a subtle intent label ("Writing mode", "Analysis mode") in the chat header during generation.

**End of Day 4 checklist:**
- [ ] `GET /api/chat/conversations/{id}/export?format=markdown` returns valid markdown file
- [ ] Download as Markdown works, filename = conversation title
- [ ] Print view at `/print/conversation/[id]` renders cleanly, print CSS correct
- [ ] `shared_outputs` table created
- [ ] `POST /api/chat/messages/{id}/share` creates link, idempotent
- [ ] `GET /api/share/{token}` is public, no auth, increments view_count
- [ ] Public share page renders with org name, sources, CTA watermark
- [ ] Admin toggle for org-level sharing disable
- [ ] Share button on assistant messages copies URL to clipboard on click
- [ ] `classify_intent()` correctly classifies all four intent types
- [ ] Intent-specific system prompts wired into `execute_task()`
- [ ] SSE `intent` event emitted at start of stream

---

## Day 5 — Redis Caching, Document Recommendations, Query History

**Goal:** The product gets faster and smarter over time. Users rediscover past work easily.

**Features:** #80 Redis Caching Layer, #50 Intelligent Document Recommendations, #91 Question History

---

### #80 — Redis Caching Layer

**`app/services/cache.py`**

```python
import redis.asyncio as redis
import json, hashlib
from app.config import settings

redis_client = redis.from_url(settings.REDIS_URL)

async def cache_get(key: str) -> dict | None:
    value = await redis_client.get(key)
    return json.loads(value) if value else None

async def cache_set(key: str, value: dict, ttl: int):
    await redis_client.setex(key, ttl, json.dumps(value))

async def cache_delete(key: str):
    await redis_client.delete(key)

async def cache_delete_pattern(pattern: str):
    """Delete all keys matching pattern. Use sparingly."""
    keys = await redis_client.keys(pattern)
    if keys:
        await redis_client.delete(*keys)

def make_document_list_key(org_id: str) -> str:
    return f"docs:list:{org_id}"

def make_search_cache_key(org_id: str, query: str, tags: list = None) -> str:
    payload = f"{org_id}:{query}:{sorted(tags or [])}"
    return f"search:{hashlib.md5(payload.encode()).hexdigest()}"
```

**1. Document list cache — 30s TTL**

Wrap `GET /api/documents` endpoint:

```python
@router.get("/documents")
async def list_documents(
    status: Optional[str] = None,
    file_type: Optional[str] = None,
    tags: Optional[List[str]] = Query(None),
    search: Optional[str] = None,
    sort_by: str = "created_at",
    sort_dir: str = "desc",
    current_user: User = Depends(get_current_user),
    ...
):
    # Only cache unfiltered requests (filtered results are too specific)
    use_cache = not any([status, file_type, tags, search])
    cache_key = make_document_list_key(current_user.org_id)

    if use_cache:
        cached = await cache_get(cache_key)
        if cached:
            return cached

    # ... existing query logic ...
    result = [doc.to_dict() for doc in documents]

    if use_cache:
        await cache_set(cache_key, result, ttl=30)

    return result
```

**Cache invalidation** — call `cache_delete(make_document_list_key(org_id))` in:
- `POST /api/documents/upload` — after document created
- `DELETE /api/documents/{id}` — after delete
- `DELETE /api/documents/bulk` — after bulk delete
- `PATCH /api/documents/{id}` — after update (name, tags, status change)
- Inngest `document/process` function — when status changes to 'ready' or 'failed'

**2. Hybrid search cache — 60s TTL**

```python
async def hybrid_search_cached(
    query: str,
    org_id: str,
    tags: Optional[List[str]] = None,
    document_id: Optional[str] = None
) -> List[Chunk]:
    # Don't cache document-scoped searches (too specific, changes too often)
    if document_id:
        return await hybrid_search(query, org_id, document_id=document_id, tags=tags)

    cache_key = make_search_cache_key(org_id, query, tags)
    cached = await cache_get(cache_key)
    if cached:
        return [Chunk(**c) for c in cached]

    results = await hybrid_search(query, org_id, tags=tags)
    await cache_set(cache_key, [c.to_dict() for c in results], ttl=60)
    return results
```

Replace `hybrid_search()` calls in `execute_task()` with `hybrid_search_cached()`.

**Search cache invalidation** — when ANY document in an org is processed (status → 'ready'), invalidate all search caches for that org:
```python
# In Inngest document/process, when status becomes 'ready'
await cache_delete_pattern(f"search:*")
# Note: This is a full search cache flush. Acceptable since document ingestion is infrequent.
# For a more surgical approach: store org_id in the cache key and only flush org-specific keys.
# Use pattern: f"search:{org_id}:*" with a different key structure if needed.
```

**Monitoring** — add cache hit/miss logging to Langfuse:
```python
cache_hit = cached is not None
langfuse.event(name="cache_lookup", metadata={"hit": cache_hit, "key_type": "search"})
```

---

### #50 — Intelligent Document Recommendations

**Backend — `app/services/recommendations.py`**

The AI call happens once during onboarding, result cached in org metadata.

```python
INDUSTRY_TEMPLATES = {
    "saas": ["Employee Handbook", "Refund Policy", "Privacy Policy", "Onboarding Guide", "Pricing Overview", "API Documentation"],
    "ecommerce": ["Return Policy", "Shipping Guide", "Product Catalog", "Customer FAQ", "Brand Guidelines", "Supplier Contracts"],
    "agency": ["Client Onboarding", "Service Agreement", "Brand Voice Guide", "Project Management Process", "Billing & Invoicing FAQ"],
    "healthcare": ["HIPAA Policy", "Patient FAQ", "Staff Handbook", "Emergency Procedures", "Insurance Guide"],
    "default": ["Employee Handbook", "Company Values", "Product Overview", "Common FAQs", "Brand Voice Guidelines", "Pricing Sheet"],
}

def get_document_recommendations(industry: str, company_size: str) -> list[str]:
    """Fast, no LLM — keyword match industry to template list."""
    industry_lower = industry.lower()
    for key in INDUSTRY_TEMPLATES:
        if key in industry_lower:
            return INDUSTRY_TEMPLATES[key]
    return INDUSTRY_TEMPLATES["default"]
```

**Onboarding extension** — extend the onboarding flow to capture industry:

In `POST /api/auth/register` (or a new `POST /api/organizations/setup` step):
```python
class OrgSetupBody(BaseModel):
    industry: str         # Free text: "B2B SaaS", "E-commerce", etc.
    company_size: str     # "1-10", "11-50", "51-200", "200+"

@router.post("/organizations/setup")
async def setup_org(body: OrgSetupBody, current_user: User = Depends(get_current_user), ...):
    recommendations = get_document_recommendations(body.industry, body.company_size)
    await db.execute(
        update(Organization)
        .where(Organization.id == current_user.org_id)
        .values(
            metadata=Organization.metadata.op('||')({
                "industry": body.industry,
                "company_size": body.company_size,
                "recommended_docs": recommendations,
                "uploaded_recommended": []  # Track which ones have been uploaded
            })
        )
    )
    await db.commit()
    return { "recommendations": recommendations }
```

**Frontend — `components/onboarding/DocumentRecommendations.tsx`**

Shown after the org setup step (industry + size collected in a modal on first login):

```tsx
export function DocumentRecommendations() {
  const { data: org } = useQuery(['org'], getOrg)
  const recs = org?.metadata?.recommended_docs || []
  const uploaded = org?.metadata?.uploaded_recommended || []

  if (!recs.length) return null

  const progress = uploaded.length / recs.length

  return (
    <div className="border rounded-xl p-4 bg-card">
      <div className="flex items-start justify-between mb-3">
        <div>
          <p className="text-sm font-semibold">Recommended documents to upload</p>
          <p className="text-xs text-muted-foreground mt-0.5">
            Based on your industry · {uploaded.length}/{recs.length} uploaded
          </p>
        </div>
      </div>

      {/* Progress bar */}
      <div className="h-1.5 bg-muted rounded-full mb-3 overflow-hidden">
        <div className="h-full bg-green-500 rounded-full transition-all"
          style={{ width: `${progress * 100}%` }} />
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5">
        {recs.map((doc: string) => {
          const done = uploaded.includes(doc)
          return (
            <div key={doc} className={cn(
              "flex items-center gap-2 px-3 py-2 rounded-lg border text-sm",
              done ? "bg-green-50 dark:bg-green-950 border-green-200 text-green-700" : "bg-muted/50"
            )}>
              {done
                ? <Check size={14} className="text-green-500 shrink-0" />
                : <div className="w-3.5 h-3.5 rounded border-2 border-muted-foreground shrink-0" />
              }
              <span className={done ? "line-through text-green-600" : ""}>{doc}</span>
            </div>
          )
        })}
      </div>

      <div className="mt-3 text-center">
        <Link href="/documents?upload=true"
          className="text-xs text-indigo-600 hover:underline">
          Upload documents →
        </Link>
      </div>
    </div>
  )
}
```

**Auto-match uploaded documents to recommendations:**

In the document upload handler, after document is created:
```python
# Fuzzy match document name to recommendations list
org = await db.get(Organization, current_user.org_id)
recs = org.metadata.get("recommended_docs", [])
uploaded = org.metadata.get("uploaded_recommended", [])

for rec in recs:
    if rec.lower() in document.name.lower() or document.name.lower() in rec.lower():
        if rec not in uploaded:
            uploaded.append(rec)
            await update_org_metadata(org.id, {"uploaded_recommended": uploaded})
            break
```

**Show recommendations** on the Documents page for orgs with < 5 documents, collapsed for others.

---

### #91 — Question History / Query Logs

**Database Migration:**

```sql
CREATE TABLE query_logs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  conversation_id UUID REFERENCES conversations(id) ON DELETE SET NULL,
  message_id UUID REFERENCES messages(id) ON DELETE SET NULL,
  query_text TEXT NOT NULL,
  intent VARCHAR(50),          -- From classifier
  response_length INTEGER,
  source_count INTEGER,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_query_logs_user ON query_logs(user_id, created_at DESC);
CREATE INDEX idx_query_logs_org ON query_logs(org_id, created_at DESC);
```

**Backend — Log every query**

In `execute_task()`, after message is created:
```python
db.add(QueryLog(
    user_id=user_id,
    org_id=org_id,
    conversation_id=conversation_id,
    message_id=message_id,
    query_text=query[:500],  # Truncate to 500 chars
    intent=intent.value,
    response_length=len(response_text),
    source_count=len(source_chunks)
))
await db.commit()
```

**Backend — `GET /api/me/query-history`:**

```python
@router.get("/me/query-history")
async def get_query_history(
    limit: int = 20,
    offset: int = 0,
    current_user: User = Depends(get_current_user),
    ...
):
    logs = await db.execute(
        select(QueryLog)
        .where(QueryLog.user_id == current_user.id)
        .order_by(QueryLog.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return logs.scalars().all()
```

**Frontend — `app/history/page.tsx`** (new page in sidebar nav):

```tsx
export default function QueryHistoryPage() {
  const { data: history, fetchNextPage } = useInfiniteQuery(
    ['query-history'],
    ({ pageParam = 0 }) =>
      fetch(`/api/me/query-history?limit=20&offset=${pageParam}`).then(r => r.json()),
    { getNextPageParam: (last, all) => last.length === 20 ? all.length * 20 : undefined }
  )

  const allLogs = history?.pages.flat() || []

  return (
    <div className="max-w-2xl mx-auto px-4 py-6">
      <h1 className="text-lg font-semibold mb-4">Recent Queries</h1>
      <div className="space-y-2">
        {allLogs.map((log: any) => (
          <Link
            key={log.id}
            href={log.conversation_id ? `/chat/${log.conversation_id}` : '#'}
            className="block border rounded-lg px-4 py-3 hover:bg-muted transition-colors"
          >
            <div className="flex items-start justify-between gap-3">
              <p className="text-sm flex-1 line-clamp-2">{log.query_text}</p>
              <span className={cn(
                "text-xs px-2 py-0.5 rounded-full shrink-0",
                INTENT_COLORS[log.intent] || "bg-muted text-muted-foreground"
              )}>
                {INTENT_LABELS[log.intent] || log.intent}
              </span>
            </div>
            <div className="flex items-center gap-3 mt-1.5">
              <span className="text-xs text-muted-foreground">{formatRelativeTime(log.created_at)}</span>
              {log.source_count > 0 && (
                <span className="text-xs text-muted-foreground">
                  {log.source_count} source{log.source_count > 1 ? 's' : ''}
                </span>
              )}
            </div>
          </Link>
        ))}
      </div>
      <button onClick={() => fetchNextPage()} className="w-full mt-4 text-sm text-muted-foreground hover:text-foreground py-2">
        Load more
      </button>
    </div>
  )
}

const INTENT_LABELS = {
  factual_qa: 'Q&A',
  task_generation: 'Writing',
  analysis: 'Analysis',
  search: 'Search',
}

const INTENT_COLORS = {
  factual_qa: 'bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300',
  task_generation: 'bg-purple-100 text-purple-700 dark:bg-purple-900 dark:text-purple-300',
  analysis: 'bg-amber-100 text-amber-700 dark:bg-amber-900 dark:text-amber-300',
  search: 'bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300',
}
```

Add "History" to the sidebar nav (below "New Chat", above conversation list).

**End of Day 5 checklist:**
- [ ] Document list caches in Redis with 30s TTL on unfiltered requests
- [ ] Cache invalidated on any document create/update/delete/status-change
- [ ] Search results cache with 60s TTL, keyed by `hash(org_id + query + tags)`
- [ ] Search cache flushed when new document becomes ready
- [ ] Industry + company_size collected during onboarding setup step
- [ ] Recommendations generated from keyword-matched template list
- [ ] Document recommendations widget shows on Documents page for new orgs
- [ ] Auto-match: uploading "Employee Handbook.pdf" checks off that recommendation
- [ ] `query_logs` table populated on every `execute_task()` call
- [ ] `GET /api/me/query-history` returns paginated logs
- [ ] History page in sidebar with intent badges and conversation deep-links
- [ ] Infinite scroll on history page

---

## Day 6 — Competitor Detection + Conversation Archive

**Goal:** The product protects sensitive information and manages itself over time without admin overhead.

**Features:** #102 Competitor Mention Detection, #104 Conversation Archive

---

### #102 — Competitor Mention Detection

**Database Migration:**

```sql
-- Org-level competitor list (admin manages)
ALTER TABLE organizations
  ADD COLUMN IF NOT EXISTS competitor_names TEXT[] NOT NULL DEFAULT '{}';

-- User-level competitor list
ALTER TABLE users
  ADD COLUMN IF NOT EXISTS competitor_names TEXT[] NOT NULL DEFAULT '{}';

-- Flag table for detected mentions
CREATE TABLE competitor_mentions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  message_id UUID NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  competitor_name VARCHAR(255) NOT NULL,
  detected_in VARCHAR(50) NOT NULL,  -- 'retrieved_context' | 'llm_response'
  chunk_id UUID REFERENCES chunks(id),
  flagged_at TIMESTAMP NOT NULL DEFAULT NOW(),
  reviewed_by UUID REFERENCES users(id),
  reviewed_at TIMESTAMP,
  action_taken VARCHAR(50)  -- 'dismissed' | 'removed_from_context' | null
);

CREATE INDEX idx_competitor_mentions_org ON competitor_mentions(org_id, flagged_at DESC);
```

**Backend — `app/services/competitor_detection.py`**

```python
import re

async def get_competitor_list(org_id: str, user_id: str, db: AsyncSession) -> list[str]:
    """Merge org-level + user-level competitor names. Deduplicated, lowercased."""
    org = await db.get(Organization, org_id)
    user = await db.get(User, user_id)
    combined = set(
        (c.lower() for c in (org.competitor_names or [])) |
        (c.lower() for c in (user.competitor_names or []))
    )
    return list(combined)

def detect_competitors(text: str, competitors: list[str]) -> list[str]:
    """Return list of competitor names found in text. Case-insensitive whole-word match."""
    found = []
    for competitor in competitors:
        # Use word boundaries to avoid partial matches (e.g. "Notion" shouldn't match "Notional")
        pattern = r'\b' + re.escape(competitor) + r'\b'
        if re.search(pattern, text, re.IGNORECASE):
            found.append(competitor)
    return found

async def check_and_flag_competitors(
    chunks: list,
    response_text: str,
    org_id: str,
    user_id: str,
    message_id: str,
    db: AsyncSession
) -> dict:
    """
    1. Check retrieved chunks for competitor mentions
    2. Check LLM response for competitor mentions
    3. Log findings to competitor_mentions table
    4. Return { found: bool, competitors: list, in_context: bool, in_response: bool }
    """
    competitors = await get_competitor_list(org_id, user_id, db)
    if not competitors:
        return { "found": False }

    in_context = []
    context_chunk_ids = []
    for chunk in chunks:
        found = detect_competitors(chunk.content, competitors)
        if found:
            in_context.extend(found)
            context_chunk_ids.append(chunk.id)

    in_response = detect_competitors(response_text, competitors)

    if not in_context and not in_response:
        return { "found": False }

    # Log all mentions
    for competitor in set(in_context + in_response):
        db.add(CompetitorMention(
            org_id=org_id,
            message_id=message_id,
            competitor_name=competitor,
            detected_in='retrieved_context' if competitor in in_context else 'llm_response',
        ))
    await db.commit()

    return {
        "found": True,
        "in_context": list(set(in_context)),
        "in_response": list(set(in_response)),
        "all": list(set(in_context + in_response))
    }
```

**Wire into `execute_task()`:**

After the LLM response completes:
```python
competitor_result = await check_and_flag_competitors(
    chunks=source_chunks,
    response_text=full_response,
    org_id=org_id,
    user_id=user_id,
    message_id=message.id,
    db=db
)

# Emit SSE event if competitors detected
if competitor_result["found"]:
    yield f"data: {json.dumps({'type': 'competitor_warning', 'competitors': competitor_result['all']})}\n\n"
```

**Frontend — `components/chat/CompetitorWarning.tsx`**

Shown below the assistant message when competitor mentions are detected:

```tsx
export function CompetitorWarning({ competitors }: { competitors: string[] }) {
  const [dismissed, setDismissed] = useState(false)
  if (dismissed) return null

  return (
    <div className="flex items-start gap-2 mt-2 p-2.5 rounded-lg bg-orange-50 dark:bg-orange-950 border border-orange-200 dark:border-orange-800 text-xs">
      <ShieldAlert size={14} className="text-orange-500 shrink-0 mt-0.5" />
      <div className="flex-1">
        <span className="font-medium text-orange-800 dark:text-orange-200">
          Competitor mention detected:{' '}
        </span>
        <span className="text-orange-700 dark:text-orange-300">
          {competitors.join(', ')} appeared in retrieved context or this response.
        </span>
      </div>
      <button onClick={() => setDismissed(true)}
        className="text-orange-400 hover:text-orange-600 shrink-0">
        <X size={12} />
      </button>
    </div>
  )
}
```

**Settings — Competitor list management**

In `app/settings/ai/page.tsx` (the AI settings tab from V2 Day 9), add two sections:

```tsx
{/* Org-level competitors — admin only */}
{isAdmin && (
  <div className="space-y-2">
    <label className="text-sm font-medium">Organization competitor list</label>
    <p className="text-xs text-muted-foreground">
      Visible to all admins. Detects these names in retrieved context and AI responses.
    </p>
    <TagInputFreeform
      tags={orgCompetitors}
      onUpdate={tags => updateOrgSettings({ competitor_names: tags })}
      placeholder="Add competitor name..."
    />
  </div>
)}

{/* User-level competitors — all users */}
<div className="space-y-2">
  <label className="text-sm font-medium">My competitor watchlist</label>
  <p className="text-xs text-muted-foreground">
    Personal list — not visible to other team members.
  </p>
  <TagInputFreeform
    tags={userCompetitors}
    onUpdate={tags => updateUserSettings({ competitor_names: tags })}
    placeholder="Add competitor name..."
  />
</div>
```

**Admin review page — `app/admin/competitors/page.tsx`**

Simple log of all flagged mentions:
```
Admin > Competitor Mentions

[Filter: Last 30 days ▼] [Competitor: All ▼]

| Competitor | Detected In     | Date       | Message Link | Action |
| Notion     | Context         | 2 days ago | View →       | Dismiss |
| Salesforce | LLM Response    | 5 days ago | View →       | Dismiss |

Total flags: 12 | Reviewed: 8 | Pending: 4
```

---

### #104 — Conversation Archive

**Database Migration:**

```sql
ALTER TABLE conversations
  ADD COLUMN IF NOT EXISTS is_archived BOOLEAN NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS archived_at TIMESTAMP;

-- Archive config stored in org metadata (no new column needed)
-- metadata.archive_after_days: default 45

CREATE INDEX idx_conversations_archived ON conversations(org_id, is_archived, updated_at);
```

**Backend — `app/services/archiver.py`**

```python
async def get_archive_threshold_days(org_id: str, db: AsyncSession) -> int:
    """Returns configured archive threshold. Default 45 days."""
    org = await db.get(Organization, org_id)
    return org.metadata.get("archive_after_days", 45)

async def archive_old_conversations(db: AsyncSession):
    """Called by Inngest cron. Archives all conversations past threshold."""
    # Get all orgs
    orgs = await db.execute(select(Organization))
    for org in orgs.scalars():
        threshold_days = await get_archive_threshold_days(org.id, db)
        cutoff = datetime.utcnow() - timedelta(days=threshold_days)

        await db.execute(
            update(Conversation)
            .where(
                Conversation.org_id == org.id,
                Conversation.is_archived == False,
                Conversation.is_pinned == False,  # Never auto-archive pinned conversations
                Conversation.updated_at < cutoff
            )
            .values(
                is_archived=True,
                archived_at=datetime.utcnow()
            )
        )
    await db.commit()
```

**Inngest cron — daily at 3am UTC:**
```python
@inngest.create_function(
    fn_id="conversations/auto-archive",
    trigger=inngest.TriggerCron(cron="0 3 * * *")
)
async def auto_archive_conversations(ctx, step):
    await step.run("archive", archive_old_conversations)
```

**Update `GET /api/chat/conversations`**

Exclude archived conversations from the default list:
```python
@router.get("/chat/conversations")
async def list_conversations(
    include_archived: bool = False,  # Only true when viewing archive
    ...
):
    query = select(Conversation).where(
        Conversation.org_id == current_user.org_id,
        Conversation.is_archived == (True if include_archived else False)
    )
    ...
```

The existing conversation search endpoint should search BOTH active and archived (for the sidebar search from V2 Day 9):
```python
# In search mode, always include archived
if q:
    query = query.where(Conversation.org_id == current_user.org_id)  # No archive filter
```

**Manual archive/restore routes:**

```python
# POST /api/chat/conversations/{id}/archive
@router.post("/chat/conversations/{conversation_id}/archive")
async def archive_conversation(conversation_id: str, ...):
    conversation.is_archived = True
    conversation.archived_at = datetime.utcnow()
    await db.commit()

# POST /api/chat/conversations/{id}/restore
@router.post("/chat/conversations/{conversation_id}/restore")
async def restore_conversation(conversation_id: str, ...):
    conversation.is_archived = False
    conversation.archived_at = None
    await db.commit()
```

**Frontend — Archive section in sidebar**

In `components/sidebar/ConversationList.tsx`, add an archive link at the bottom:

```tsx
{/* At bottom of conversation list */}
<div className="border-t pt-1 mt-1">
  <Link
    href="/archive"
    className="flex items-center gap-2 px-3 py-1.5 rounded text-xs text-muted-foreground hover:text-foreground hover:bg-muted"
  >
    <Archive size={13} /> Archived conversations
  </Link>
</div>
```

Right-click context menu on conversation items (or "..." overflow menu):
```tsx
<DropdownMenu>
  <DropdownMenuTrigger asChild>
    <button className="opacity-0 group-hover:opacity-100 p-1 rounded hover:bg-muted">
      <MoreHorizontal size={12} />
    </button>
  </DropdownMenuTrigger>
  <DropdownMenuContent align="end">
    <DropdownMenuItem onClick={handleRename}>Rename</DropdownMenuItem>
    <DropdownMenuItem onClick={handlePin}>
      {isPinned ? 'Unpin' : 'Pin'}
    </DropdownMenuItem>
    <DropdownMenuSeparator />
    <DropdownMenuItem onClick={handleArchive} className="text-muted-foreground">
      Archive
    </DropdownMenuItem>
    <DropdownMenuItem onClick={handleDelete} className="text-destructive">
      Delete
    </DropdownMenuItem>
  </DropdownMenuContent>
</DropdownMenu>
```

**`app/archive/page.tsx`** — Archive view:

```tsx
export default function ArchivePage() {
  const { data: archived } = useQuery(['conversations', 'archived'], () =>
    fetch('/api/chat/conversations?include_archived=true').then(r => r.json())
  )

  return (
    <div className="max-w-2xl mx-auto px-4 py-6">
      <div className="flex items-center gap-2 mb-4">
        <Archive size={18} />
        <h1 className="text-lg font-semibold">Archived Conversations</h1>
      </div>
      <p className="text-sm text-muted-foreground mb-4">
        Conversations archived after {archiveThreshold} days of inactivity.
        Pinned conversations are never archived automatically.
      </p>
      <div className="space-y-1">
        {(archived || []).map((conv: any) => (
          <div key={conv.id} className="flex items-center gap-2 px-3 py-2 border rounded-lg">
            <Link href={`/chat/${conv.id}`} className="flex-1 text-sm truncate hover:underline">
              {conv.title}
            </Link>
            <span className="text-xs text-muted-foreground">
              archived {formatRelativeTime(conv.archived_at)}
            </span>
            <button
              onClick={() => restoreConversation(conv.id)}
              className="text-xs text-indigo-600 hover:underline px-2"
            >
              Restore
            </button>
          </div>
        ))}
        {archived?.length === 0 && (
          <p className="text-sm text-muted-foreground text-center py-8">
            No archived conversations yet.
          </p>
        )}
      </div>
    </div>
  )
}
```

**Settings — Configure archive threshold**

In `Settings > AI` tab, add:
```tsx
<div className="flex items-center justify-between">
  <div>
    <p className="text-sm font-medium">Auto-archive conversations after</p>
    <p className="text-xs text-muted-foreground">Archived conversations remain searchable</p>
  </div>
  <Select
    value={String(archiveDays)}
    onValueChange={v => updateOrgSettings({ archive_after_days: parseInt(v) })}
  >
    <SelectTrigger className="w-36">
      <SelectValue />
    </SelectTrigger>
    <SelectContent>
      <SelectItem value="30">30 days</SelectItem>
      <SelectItem value="45">45 days</SelectItem>
      <SelectItem value="60">60 days</SelectItem>
      <SelectItem value="90">90 days</SelectItem>
      <SelectItem value="180">180 days</SelectItem>
      <SelectItem value="never">Never</SelectItem>
    </SelectContent>
  </Select>
</div>
```

**End of Day 6 checklist:**
- [ ] `competitor_names TEXT[]` on both organizations and users tables
- [ ] `competitor_mentions` table created
- [ ] `detect_competitors()` uses whole-word regex matching
- [ ] Competitor check runs after every `execute_task()` call
- [ ] SSE `competitor_warning` event emitted when matches found
- [ ] Inline warning banner below assistant message, dismissible
- [ ] Org-level competitor list in Settings > AI (admin only)
- [ ] User-level competitor watchlist in Settings > AI (all users)
- [ ] Admin competitor mentions review page with dismiss actions
- [ ] `is_archived` and `archived_at` columns on conversations
- [ ] Daily Inngest cron archives conversations past 45-day threshold
- [ ] Pinned conversations never auto-archived
- [ ] `include_archived=false` default on conversation list endpoint
- [ ] Search includes archived conversations
- [ ] Archive/restore from conversation "..." menu
- [ ] Archive page at `/archive` with restore button
- [ ] Archive threshold configurable in settings (30/45/60/90/180/never)

---

## Database Migration Sequence

Run `alembic revision --autogenerate` at the start of each day, before any code:

```
Day 1: organizations.metadata (IF NOT EXISTS), no new tables
Day 2: documents.tags TEXT[], CREATE TABLE prompt_templates
Day 3: conversations.is_pinned, messages.parent_message_id + branch_index + is_active_branch
Day 4: CREATE TABLE shared_outputs, organizations.allow_output_sharing
Day 5: CREATE TABLE query_logs, organizations.competitor_names (prep for Day 6)
Day 6: users.competitor_names, CREATE TABLE competitor_mentions,
        conversations.is_archived + archived_at
```

---

## Environment Variables Checklist

No new environment variables are required for V3 — all infrastructure (Redis, Postgres, Inngest) is already wired from V2. Verify these are set:

```bash
REDIS_URL=redis://...            # For caching layer (Day 5)
APP_URL=https://yourapp.com      # For shareable link generation (Day 4)
INNGEST_EVENT_KEY=...            # For cron jobs (Days 1, 5, 6)
```

---

## Feature Completion Matrix

| # | Feature | Day | Priority | Status |
|---|---|---|---|---|
| #4 | No Documents Warning | 1 | P0 | ✓ |
| #5 | Guided Onboarding Checklist | 1 | P0 | ✓ |
| #19 | Document Tags + Collections | 2 | P1 | ✓ |
| #20 | Prompt Template Library | 2 | P1 | ✓ |
| #27 | Mobile Responsive Layout | 3 | P2 | ✓ |
| #30 | Pinned Conversations | 3 | P2 | ✓ |
| #42 | Conversation Branching | 3 | P2 | ✓ |
| #25 | Conversation Export | 4 | P2 | ✓ |
| #62 | Shareable Output Links | 4 | P2 | ✓ |
| #51 | Query Intent Classification | 4 | P2 | ✓ |
| #80 | Redis Caching Layer | 5 | P2 | ✓ |
| #50 | Document Recommendations | 5 | P2 | ✓ |
| #91 | Question History / Query Logs | 5 | P2 | ✓ |
| #102 | Competitor Mention Detection | 6 | P2 | ✓ |
| #104 | Conversation Archive | 6 | P2 | ✓ |
| #37 | Real-Time Collaboration | — | P3 | Deferred |

**Total: 15 features across 6 days.**