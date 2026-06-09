# Company Brain — V2 Feature Roadmap (15 Days)

## Confirmed Architecture Decisions

| Layer | Choice |
|---|---|
| Frontend | Next.js (existing) |
| Backend | FastAPI (existing) |
| Background Jobs | Inngest (existing) |
| Email | Resend + React Email |
| Observability | Langfuse Cloud (free tier) |
| Drive Sync | Google Drive API v3, polling every 5 min |
| Quota Counter | Redis (reads) + Postgres (persistence) |
| API Keys | Org-level + User-level scoping |
| Slack Response | Streaming via Slack socket mode |
| Ingestion Retry | Chunk-level only |
| Knowledge Gaps | SSE event + inline chat UI warning |
| Integrations | Google Drive, Notion, Zapier, Email forwarding |
| Sprint Style | 15 days sequential, no buffer |

---

## Priority Index

- **P0** — Ship immediately, blocking core value: #1, #2, #10, #16
- **P1** — This sprint, high impact: #6, #8, #9, #11, #13, #14, #15, #21, #23, #26, #28, #31, #41, #65
- **P2** — Later days: #38, #47, #49, #53, #66, #67, #76, #83–#100

---

## Day 1 — P0 Foundations: Markdown, Copy Button, Feedback UI

**Goal:** Ship the three highest-leverage P0 features that are blocking polish and data collection. Every user interaction from today onward generates feedback signal.

**Features:** #10 Markdown Rendering, #2 Copy Button, #1 Feedback Thumbs

---

### #10 — Markdown Rendering in Chat

**Frontend — `components/chat/AssistantMessage.tsx`**

Install dependencies:
```bash
npm install react-markdown remark-gfm rehype-highlight
```

Replace the current plain `<p>` text renderer for assistant messages with:
```tsx
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

export function AssistantMessage({ content }: { content: string }) {
  return (
    <div className="prose prose-sm dark:prose-invert max-w-none">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>
        {content}
      </ReactMarkdown>
    </div>
  )
}
```

Add `@tailwindcss/typography` plugin to `tailwind.config.ts`:
```ts
plugins: [require('@tailwindcss/typography')]
```

Install:
```bash
npm install @tailwindcss/typography
```

Test: Send a message that returns a bulleted list, a bold heading, and a code block. Verify all render correctly.

---

### #2 — Copy-to-Clipboard Button

**Frontend — `components/chat/AssistantMessage.tsx`**

Add a copy button overlay that appears on hover above every assistant message bubble:
```tsx
import { useState } from 'react'
import { Copy, Check } from 'lucide-react'
import { useToast } from '@/hooks/use-toast'

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)

  const handleCopy = async () => {
    await navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <button
      onClick={handleCopy}
      className="absolute top-2 right-2 p-1.5 rounded-md bg-muted hover:bg-muted/80 opacity-0 group-hover:opacity-100 transition-opacity"
    >
      {copied ? <Check size={14} className="text-green-500" /> : <Copy size={14} />}
    </button>
  )
}
```

Wrap the `AssistantMessage` container div with `relative group` class so the copy button appears on hover. Show a "Copied!" toast via shadcn's `useToast`.

---

### #1 — Feedback Thumbs Up/Down

**Backend — `app/api/routes/messages.py`**

The `feedback` column already exists on the `messages` table. Add the PATCH route:
```python
@router.patch("/chat/messages/{message_id}/feedback")
async def update_message_feedback(
    message_id: str,
    body: FeedbackBody,  # { feedback: Literal['positive', 'negative'] }
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    message = await db.get(Message, message_id)
    if not message or message.conversation.org_id != current_user.org_id:
        raise HTTPException(404)
    message.feedback = body.feedback
    await db.commit()
    return {"status": "ok"}
```

Add Pydantic model:
```python
class FeedbackBody(BaseModel):
    feedback: Literal['positive', 'negative']
```

**Frontend — `components/chat/AssistantMessage.tsx`**

Below each assistant message, render two icon buttons:
```tsx
import { ThumbsUp, ThumbsDown } from 'lucide-react'

function FeedbackButtons({ messageId, currentFeedback }: {
  messageId: string
  currentFeedback: 'positive' | 'negative' | null
}) {
  const [feedback, setFeedback] = useState(currentFeedback)

  const submitFeedback = async (value: 'positive' | 'negative') => {
    setFeedback(value)
    await fetch(`/api/chat/messages/${messageId}/feedback`, {
      method: 'PATCH',
      body: JSON.stringify({ feedback: value }),
      headers: { 'Content-Type': 'application/json' }
    })
  }

  return (
    <div className="flex gap-1 mt-2">
      <button onClick={() => submitFeedback('positive')}
        className={cn('p-1 rounded', feedback === 'positive' ? 'text-green-500' : 'text-muted-foreground')}>
        <ThumbsUp size={14} />
      </button>
      <button onClick={() => submitFeedback('negative')}
        className={cn('p-1 rounded', feedback === 'negative' ? 'text-red-500' : 'text-muted-foreground')}>
        <ThumbsDown size={14} />
      </button>
    </div>
  )
}
```

**Admin view:** Add a `GET /api/admin/feedback-stats` route that returns:
```json
{ "positive": 142, "negative": 23, "unrated": 891 }
```
Surface this on the admin dashboard as a simple stat card.

**End of Day 1 checklist:**
- [ ] Markdown renders correctly for headers, bullets, bold, code blocks
- [ ] Copy button appears on hover, copies raw markdown text, shows toast
- [ ] Thumbs up/down persist to DB on click, highlight on re-render
- [ ] Admin feedback stats endpoint returns correct counts

---

## Day 2 — P0: Team Invitation Flow (#16)

**Goal:** This is the single biggest conversion blocker. An admin who can't invite teammates cannot buy a team plan. Ship the entire invite flow end-to-end.

**Feature:** #16 Team Member Invitation Flow

---

### Database Migration

Create `invitations` table:
```sql
CREATE TABLE invitations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  email VARCHAR(255) NOT NULL,
  role VARCHAR(50) NOT NULL DEFAULT 'member',
  token VARCHAR(255) NOT NULL UNIQUE,
  invited_by UUID NOT NULL REFERENCES users(id),
  expires_at TIMESTAMP NOT NULL DEFAULT (NOW() + INTERVAL '7 days'),
  accepted_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_invitations_token ON invitations(token);
CREATE INDEX idx_invitations_org_id ON invitations(org_id);
```

Add Alembic migration:
```bash
alembic revision --autogenerate -m "add_invitations_table"
alembic upgrade head
```

---

### Backend

**`app/models/invitation.py`** — SQLAlchemy model mirroring the table above.

**`app/api/routes/organizations.py`** — Add three routes:

```python
# POST /api/organizations/invite
@router.post("/organizations/invite")
async def invite_member(body: InviteBody, ...):
    # 1. Check caller is admin of org
    # 2. Check plan user limit not exceeded
    # 3. Check email not already a member or pending invite
    # 4. Generate secure token: secrets.token_urlsafe(32)
    # 5. Insert into invitations table
    # 6. Send invite email via Resend (see Day 3)
    # 7. Return { "invited": true }

# GET /api/organizations/invitations
@router.get("/organizations/invitations")
async def list_invitations(...):
    # Returns all pending (accepted_at IS NULL, expires_at > NOW()) invites for org
    # Used in Settings > Team tab

# DELETE /api/organizations/invitations/{invitation_id}
@router.delete("/organizations/invitations/{invitation_id}")
async def revoke_invitation(...):
    # Hard delete. Admin only.
```

**`app/api/routes/auth.py`** — Add accept flow:
```python
# GET /api/auth/accept-invite?token=xxx
@router.get("/auth/accept-invite")
async def accept_invite(token: str, db: AsyncSession = Depends(get_db)):
    invite = await db.execute(
        select(Invitation).where(
            Invitation.token == token,
            Invitation.accepted_at.is_(None),
            Invitation.expires_at > datetime.utcnow()
        )
    )
    if not invite:
        raise HTTPException(400, "Invalid or expired invite link")
    return { "org_id": invite.org_id, "email": invite.email, "role": invite.role }
    # Frontend uses this to pre-populate signup form
```

On signup completion, set `invitations.accepted_at = NOW()` and assign `users.org_id` from the invite.

---

### Frontend

**`app/settings/team/page.tsx`** — New settings tab:

Layout:
```
Settings > Team

[Current Members]
| Name          | Email              | Role   | Actions |
| Aniket        | a@company.com      | Admin  | —       |
| Riya Sharma   | r@company.com      | Member | Remove  |

[Pending Invites]
| Email              | Sent       | Expires    | Actions |
| test@company.com   | 2 days ago | in 5 days  | Revoke  |

[Invite Member]
Email: [________________] Role: [Member ▼] [Send Invite]
```

Implement with:
- `useQuery` hook to fetch `/api/organizations/members` and `/api/organizations/invitations`
- Inline form (no `<form>` tag — use `onClick` handler on button)
- On success: show toast "Invite sent to test@company.com", refetch invitations list

**`app/auth/accept-invite/page.tsx`** — Accept invite page:
- On mount, call `GET /api/auth/accept-invite?token=TOKEN`
- If valid: show signup form pre-filled with email, org name displayed ("You're joining Acme Corp")
- If invalid/expired: show error state with "Request a new invite" CTA

**End of Day 2 checklist:**
- [ ] Admin can send invite from Settings > Team
- [ ] Pending invites listed with revoke option
- [ ] Invite link works and pre-populates signup
- [ ] Expired/invalid tokens show graceful error
- [ ] Plan user limit enforced on invite send

---

## Day 3 — Transactional Email System (#26)

**Goal:** Wire up Resend for all lifecycle emails. This unblocks Day 2's invite email and all future notification flows.

**Feature:** #26 Transactional Email System

---

### Setup

Install in FastAPI:
```bash
pip install resend
```

Add to `.env`:
```
RESEND_API_KEY=re_xxxxxxxxxxxx
EMAIL_FROM=noreply@companybrain.app
```

Create `email_events` table (prevents duplicate sends):
```sql
CREATE TABLE email_events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES users(id),
  event_type VARCHAR(100) NOT NULL,
  metadata JSONB,
  sent_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_email_events_user_event ON email_events(user_id, event_type);
```

---

### Email Service — `app/services/email.py`

```python
import resend
from app.config import settings

resend.api_key = settings.RESEND_API_KEY

async def send_email(to: str, subject: str, html: str):
    resend.Emails.send({
        "from": settings.EMAIL_FROM,
        "to": to,
        "subject": subject,
        "html": html
    })

async def send_if_not_sent(db, user_id: str, event_type: str, to: str, subject: str, html: str):
    """Idempotent send — checks email_events before sending."""
    existing = await db.execute(
        select(EmailEvent).where(
            EmailEvent.user_id == user_id,
            EmailEvent.event_type == event_type
        )
    )
    if existing.scalar():
        return  # Already sent
    await send_email(to, subject, html)
    db.add(EmailEvent(user_id=user_id, event_type=event_type))
    await db.commit()
```

---

### Email Templates — `app/email_templates/`

Create plain HTML strings (or use Jinja2 templates) for each trigger. Keep them simple, on-brand:

**`invite.html`**
```html
<h2>You've been invited to join {{ org_name }} on Company Brain</h2>
<p>{{ inviter_name }} has invited you to collaborate.</p>
<a href="{{ accept_url }}" style="...">Accept Invitation</a>
<p>This link expires in 7 days.</p>
```

**`welcome.html`** — Sent on signup completion.
```html
<h2>Welcome to Company Brain, {{ first_name }}</h2>
<p>Your knowledge base is ready. Upload your first document to get started.</p>
<a href="{{ app_url }}/documents">Upload Documents →</a>
```

**`document_ready.html`** — Sent when Inngest marks document status = 'ready'.
```html
<h2>Your document is ready</h2>
<p><strong>{{ doc_name }}</strong> has been processed and is now searchable.</p>
```

**`quota_warning.html`** — Sent at 80% usage.
```html
<h2>You've used 80% of your monthly queries</h2>
<p>{{ used }} of {{ limit }} queries used. Upgrade to avoid interruption.</p>
<a href="{{ billing_url }}">Upgrade Plan →</a>
```

**`quota_exceeded.html`** — Sent at 100%.
```html
<h2>You've reached your query limit</h2>
<p>Upgrade your plan to continue using Company Brain.</p>
```

**`weekly_digest.html`** — Sent every Monday (Inngest cron).
```html
<h2>Your Company Brain weekly summary</h2>
<p>{{ query_count }} queries this week | {{ doc_count }} documents in your knowledge base</p>
<p>Top document cited: {{ top_doc }}</p>
```

---

### Wire Up Triggers

1. **Signup** → call `send_email(welcome)` in `POST /api/auth/register` after user creation
2. **Invite** → call `send_email(invite)` in `POST /api/organizations/invite` (Day 2)
3. **Document ready** → call `send_email(document_ready)` in Inngest `document.process` function on status = 'ready'
4. **Quota warning** → checked on every query in `execute_task()`: if `used/limit >= 0.8` and no warning sent this month, fire email
5. **Weekly digest** → new Inngest cron function `email/weekly-digest`, runs every Monday 9am UTC

```python
# In inngest_functions.py
@inngest.create_function(
    fn_id="email/weekly-digest",
    trigger=inngest.TriggerCron(cron="0 9 * * MON")
)
async def weekly_digest(ctx, step):
    orgs = await step.run("fetch-orgs", get_all_active_orgs)
    for org in orgs:
        stats = await step.run(f"stats-{org.id}", lambda: get_weekly_stats(org.id))
        await step.run(f"send-{org.id}", lambda: send_digest_email(org, stats))
```

**End of Day 3 checklist:**
- [ ] `email_events` table exists, idempotent sends working
- [ ] Invite email sends and links work end-to-end
- [ ] Welcome email fires on new signup
- [ ] Document ready email fires from Inngest worker
- [ ] Quota warning fires at 80% (once per month per user)
- [ ] Weekly digest cron registered in Inngest

---

## Day 4 — UI Polish: Skeletons, Dark Mode, Loading States (#13, #14)

**Goal:** Close the visible polish gap. Dark mode and skeletons are the two things that make a product feel unfinished to technical users.

**Features:** #13 Dark Mode, #14 Loading Skeletons

---

### #13 — Dark Mode

`next-themes` is already in the stack. Wire it up:

**`app/layout.tsx`** — Wrap with ThemeProvider:
```tsx
import { ThemeProvider } from 'next-themes'

export default function RootLayout({ children }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body>
        <ThemeProvider attribute="class" defaultTheme="system" enableSystem>
          {children}
        </ThemeProvider>
      </body>
    </html>
  )
}
```

**`components/ThemeToggle.tsx`** — Toggle button component:
```tsx
import { Moon, Sun } from 'lucide-react'
import { useTheme } from 'next-themes'

export function ThemeToggle() {
  const { theme, setTheme } = useTheme()
  return (
    <button onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
      className="p-2 rounded-md hover:bg-muted">
      {theme === 'dark' ? <Sun size={16} /> : <Moon size={16} />}
    </button>
  )
}
```

Place `<ThemeToggle />` in the sidebar footer next to user avatar.

**CSS variables audit:** Open `app/globals.css`. Ensure every color uses CSS vars (shadcn does this by default). Audit any hardcoded `text-gray-900` or `bg-white` classes in custom components — replace with `text-foreground` and `bg-background`.

Spot-check pages: Chat, Documents, Settings. Fix any dark mode contrast issues.

---

### #14 — Loading Skeletons

**`components/ui/skeleton.tsx`** — Already exists in shadcn. If not:
```tsx
export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("animate-pulse rounded-md bg-muted", className)} />
}
```

**Three skeleton implementations:**

**1. Conversation List Skeleton** — `components/sidebar/ConversationListSkeleton.tsx`
```tsx
export function ConversationListSkeleton() {
  return (
    <div className="space-y-1 p-2">
      {Array.from({ length: 6 }).map((_, i) => (
        <div key={i} className="flex items-center gap-2 p-2">
          <Skeleton className="h-4 w-4 rounded" />
          <Skeleton className="h-4 flex-1" />
        </div>
      ))}
    </div>
  )
}
```

Show while `useQuery('conversations')` is loading.

**2. Document Table Skeleton** — `components/documents/DocumentTableSkeleton.tsx`
```tsx
export function DocumentTableSkeleton() {
  return (
    <div className="space-y-2">
      {Array.from({ length: 8 }).map((_, i) => (
        <div key={i} className="flex items-center gap-4 p-3 border rounded-lg">
          <Skeleton className="h-8 w-8 rounded" />
          <Skeleton className="h-4 w-48" />
          <Skeleton className="h-4 w-16 ml-auto" />
          <Skeleton className="h-6 w-20 rounded-full" />
        </div>
      ))}
    </div>
  )
}
```

**3. Chat Messages Skeleton** — `components/chat/ChatSkeleton.tsx`
```tsx
export function ChatMessageSkeleton() {
  return (
    <div className="space-y-4 p-4">
      {[80, 60, 90, 50].map((w, i) => (
        <div key={i} className={cn("flex gap-3", i % 2 === 0 ? "justify-end" : "")}>
          {i % 2 !== 0 && <Skeleton className="h-8 w-8 rounded-full shrink-0" />}
          <Skeleton className={`h-12 rounded-2xl`} style={{ width: `${w}%` }} />
        </div>
      ))}
    </div>
  )
}
```

Show while conversation is loading on route change.

**Where to add skeletons:** Replace any `if (loading) return null` or `if (loading) return <Spinner />` with the appropriate skeleton component. Target files: `app/chat/[id]/page.tsx`, `app/documents/page.tsx`, `components/sidebar/ConversationList.tsx`.

**End of Day 4 checklist:**
- [ ] Dark mode toggle in sidebar, persists across sessions
- [ ] All custom components render correctly in dark mode (no hardcoded colors)
- [ ] Conversation list shows skeleton while loading
- [ ] Document table shows skeleton while loading
- [ ] Chat messages show skeleton on conversation switch

---

## Day 5 — Document UX: Bulk Upload, Retry, Progress, Rename (#8, #15, #11, #6)

**Goal:** Fix the document and conversation management UX friction points that cause user frustration during daily use.

**Features:** #8 Bulk Upload, #15 Retry Button, #11 Progress Bar, #6 Conversation Rename

---

### #8 — Multiple File Upload

**Frontend — `components/documents/UploadDropzone.tsx`**

Change react-dropzone config:
```tsx
const { getRootProps, getInputProps } = useDropzone({
  multiple: true,  // Was: false
  accept: {
    'application/pdf': ['.pdf'],
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': ['.docx'],
    'text/plain': ['.txt'],
    'text/csv': ['.csv'],
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': ['.xlsx'],
    'application/vnd.openxmlformats-officedocument.presentationml.presentation': ['.pptx'],
  },
  onDrop: handleMultipleDrop
})
```

**`handleMultipleDrop` function:**
```tsx
const handleMultipleDrop = async (files: File[]) => {
  // Upload files sequentially (not parallel) to avoid hammering the server
  for (const file of files) {
    const formData = new FormData()
    formData.append('file', file)
    await uploadDocument(formData)  // Existing upload function
  }
}
```

**Bulk progress UI** — Show a queue panel when multiple files are uploading:
```tsx
{uploadQueue.length > 0 && (
  <div className="fixed bottom-4 right-4 w-80 bg-card border rounded-lg shadow-lg p-4">
    <p className="text-sm font-medium mb-2">Uploading {uploadQueue.length} files</p>
    {uploadQueue.map(item => (
      <div key={item.name} className="flex items-center gap-2 py-1">
        <FileIcon size={14} />
        <span className="text-xs truncate flex-1">{item.name}</span>
        <span className="text-xs text-muted-foreground">{item.status}</span>
      </div>
    ))}
  </div>
)}
```

---

### #15 — Retry Failed Documents

**Frontend — `components/documents/DocumentRow.tsx`**

In the document table row, add a "Retry" button that only renders when `document.status === 'failed'`:
```tsx
{document.status === 'failed' && (
  <Button
    variant="outline"
    size="sm"
    onClick={() => retryDocument(document.id)}
  >
    <RefreshCw size={12} className="mr-1" /> Retry
  </Button>
)}
```

**`retryDocument` function:**
```tsx
const retryDocument = async (docId: string) => {
  await fetch(`/api/documents/${docId}/reprocess`, { method: 'POST' })
  // Optimistically update status to 'processing' in local state
  queryClient.setQueryData(['documents'], (old) =>
    old.map(d => d.id === docId ? { ...d, status: 'processing' } : d)
  )
}
```

The backend route `POST /documents/{id}/reprocess` already exists per the feature notes — no backend work needed.

---

### #11 — Document Processing Progress Bar

**Frontend — `components/documents/DocumentRow.tsx`**

When `document.status === 'processing'`, replace the status badge with an animated progress indicator:
```tsx
{document.status === 'processing' ? (
  <div className="flex items-center gap-2 min-w-32">
    <div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden">
      <div className="h-full bg-primary rounded-full animate-[progress_2s_ease-in-out_infinite]" />
    </div>
    <span className="text-xs text-muted-foreground whitespace-nowrap">~30s</span>
  </div>
) : (
  <StatusBadge status={document.status} />
)}
```

Add the CSS animation to `globals.css`:
```css
@keyframes progress {
  0% { width: 10%; margin-left: 0; }
  50% { width: 60%; margin-left: 20%; }
  100% { width: 10%; margin-left: 90%; }
}
```

Poll document status every 3 seconds while any document is in 'processing' state:
```tsx
useEffect(() => {
  const hasProcessing = documents.some(d => d.status === 'processing')
  if (!hasProcessing) return
  const interval = setInterval(() => queryClient.invalidateQueries(['documents']), 3000)
  return () => clearInterval(interval)
}, [documents])
```

---

### #6 — Conversation Rename

**Frontend — `components/sidebar/ConversationItem.tsx`**

Add inline edit on double-click:
```tsx
const [editing, setEditing] = useState(false)
const [title, setTitle] = useState(conversation.title)
const inputRef = useRef<HTMLInputElement>(null)

const handleDoubleClick = () => {
  setEditing(true)
  setTimeout(() => inputRef.current?.focus(), 0)
}

const handleSave = async () => {
  setEditing(false)
  if (title === conversation.title) return
  await fetch(`/api/chat/conversations/${conversation.id}`, {
    method: 'PATCH',
    body: JSON.stringify({ title }),
    headers: { 'Content-Type': 'application/json' }
  })
}

return editing ? (
  <input
    ref={inputRef}
    value={title}
    onChange={e => setTitle(e.target.value)}
    onBlur={handleSave}
    onKeyDown={e => e.key === 'Enter' && handleSave()}
    className="text-sm bg-transparent border-b border-primary outline-none w-full"
  />
) : (
  <span onDoubleClick={handleDoubleClick} className="text-sm truncate cursor-pointer">
    {title}
  </span>
)
```

The backend PATCH route already exists. No backend work needed.

**End of Day 5 checklist:**
- [ ] Can drop 10+ files at once, all upload sequentially with queue panel
- [ ] Failed documents have "Retry" button that resets status to processing
- [ ] Processing documents show animated progress bar with "~30s" estimate
- [ ] Double-click conversation title → inline edit → Enter or blur saves

---

## Day 6 — Langfuse Observability (#41)

**Goal:** Instrument every LLM call so you have full visibility into cost, latency, quality, and failure rate from today onward. This is the foundation for product quality improvements.

**Feature:** #41 Langfuse LLM Observability

---

### Setup

Sign up at cloud.langfuse.com. Create a project "company-brain-prod".

Add to `.env`:
```
LANGFUSE_SECRET_KEY=sk-lf-xxxx
LANGFUSE_PUBLIC_KEY=pk-lf-xxxx
LANGFUSE_HOST=https://cloud.langfuse.com
```

Install:
```bash
pip install langfuse
```

---

### Instrumentation — `app/services/llm.py`

Wrap your existing `execute_task()` function with Langfuse tracing:

```python
from langfuse import Langfuse
from app.config import settings

langfuse = Langfuse(
    secret_key=settings.LANGFUSE_SECRET_KEY,
    public_key=settings.LANGFUSE_PUBLIC_KEY,
    host=settings.LANGFUSE_HOST
)

async def execute_task(query: str, context_chunks: list, conversation_id: str, user_id: str, org_id: str):
    trace = langfuse.trace(
        name="execute_task",
        user_id=user_id,
        metadata={
            "org_id": org_id,
            "conversation_id": conversation_id,
            "chunk_count": len(context_chunks)
        }
    )

    # Instrument the vector search step
    search_span = trace.span(name="vector_search")
    # ... existing search logic ...
    search_span.end(output={"chunks_found": len(context_chunks)})

    # Instrument the LLM call
    generation = trace.generation(
        name="llm_completion",
        model="gpt-4o",  # or whatever model you use
        input={"messages": messages},  # the prompt array
    )
    response = await call_llm(messages)  # existing LLM call
    generation.end(
        output=response.choices[0].message.content,
        usage={
            "input": response.usage.prompt_tokens,
            "output": response.usage.completion_tokens,
        }
    )

    trace.update(output={"response_length": len(response_text)})
    return response_text
```

---

### Feedback Forwarding

When a user clicks thumbs up/down (Day 1), also send the score to Langfuse:
```python
# In PATCH /chat/messages/{id}/feedback
langfuse.score(
    trace_id=message.langfuse_trace_id,  # Store this when message is created
    name="user_feedback",
    value=1 if feedback == 'positive' else 0,
    comment=f"User rated {feedback}"
)
```

Add `langfuse_trace_id VARCHAR(255)` column to `messages` table. Store it when message is created.

---

### What You'll See in Langfuse Dashboard

After 50+ queries:
- **Cost per query** — total token spend broken down by org and user
- **Latency histogram** — p50/p95/p99 response times
- **Failure rate** — traces with errors highlighted
- **Feedback scores** — which conversations have low user satisfaction
- **Search quality** — how many chunks are found per query (low = knowledge gap)

**End of Day 6 checklist:**
- [ ] `langfuse_trace_id` column on messages table, migration run
- [ ] Every `execute_task()` call creates a Langfuse trace
- [ ] Vector search and LLM call are separate spans within the trace
- [ ] Token usage logged on every generation
- [ ] Feedback scores forwarded to Langfuse from thumbs up/down UI
- [ ] Langfuse dashboard shows live traces after a few test queries

---

## Day 7 — Advanced Document Filters + Bulk Operations (#65, #66)

**Goal:** The documents page becomes genuinely manageable for orgs with 50–200 documents.

**Features:** #65 Advanced Document Filters, #66 Bulk Document Operations

---

### #65 — Advanced Document Filters

**Backend — `GET /api/documents`**

Update the documents list endpoint to accept query params:
```python
@router.get("/documents")
async def list_documents(
    status: Optional[str] = None,       # pending|processing|ready|failed
    file_type: Optional[str] = None,    # pdf|docx|txt|csv|xlsx|pptx
    tag: Optional[str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    search: Optional[str] = None,       # filename search
    sort_by: Optional[str] = "created_at",
    sort_dir: Optional[str] = "desc",
    ...
):
    query = select(Document).where(Document.org_id == current_user.org_id)

    if status:
        query = query.where(Document.status == status)
    if file_type:
        query = query.where(Document.file_type == file_type)
    if tag:
        query = query.where(Document.tags.contains([tag]))
    if date_from:
        query = query.where(Document.created_at >= date_from)
    if date_to:
        query = query.where(Document.created_at <= date_to)
    if search:
        query = query.where(Document.name.ilike(f"%{search}%"))

    sort_col = getattr(Document, sort_by, Document.created_at)
    query = query.order_by(sort_col.desc() if sort_dir == "desc" else sort_col.asc())
    ...
```

**Frontend — `components/documents/DocumentFilters.tsx`**

Filter bar above the document table:
```tsx
<div className="flex items-center gap-2 mb-4 flex-wrap">
  <Input
    placeholder="Search documents..."
    value={filters.search}
    onChange={e => setFilters(f => ({ ...f, search: e.target.value }))}
    className="w-48"
  />
  <Select value={filters.status} onValueChange={v => setFilters(f => ({ ...f, status: v }))}>
    <SelectTrigger className="w-36"><SelectValue placeholder="All statuses" /></SelectTrigger>
    <SelectContent>
      <SelectItem value="">All</SelectItem>
      <SelectItem value="ready">Ready</SelectItem>
      <SelectItem value="processing">Processing</SelectItem>
      <SelectItem value="failed">Failed</SelectItem>
    </SelectContent>
  </Select>
  <Select value={filters.file_type} onValueChange={...}>
    {/* PDF, DOCX, TXT, CSV, XLSX, PPTX */}
  </Select>
  <DateRangePicker value={filters.dateRange} onChange={...} />
  {hasActiveFilters && (
    <Button variant="ghost" size="sm" onClick={clearFilters}>
      Clear filters <X size={12} className="ml-1" />
    </Button>
  )}
</div>
```

Use `debounce(300ms)` on the search input before firing the API call.

---

### #66 — Bulk Document Operations

**Frontend — `components/documents/DocumentTable.tsx`**

Add a checkbox column to the document table:
```tsx
// State
const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
const allSelected = selectedIds.size === documents.length

// Header checkbox (select all)
<Checkbox
  checked={allSelected}
  onCheckedChange={checked => {
    setSelectedIds(checked ? new Set(documents.map(d => d.id)) : new Set())
  }}
/>

// Row checkbox
<Checkbox
  checked={selectedIds.has(doc.id)}
  onCheckedChange={checked => {
    setSelectedIds(prev => {
      const next = new Set(prev)
      checked ? next.add(doc.id) : next.delete(doc.id)
      return next
    })
  }}
/>
```

**Bulk action bar** — appears when 1+ documents are selected:
```tsx
{selectedIds.size > 0 && (
  <div className="flex items-center gap-2 p-2 bg-muted rounded-lg mb-2">
    <span className="text-sm">{selectedIds.size} selected</span>
    <Button variant="destructive" size="sm" onClick={bulkDelete}>
      <Trash2 size={14} className="mr-1" /> Delete
    </Button>
    <Button variant="outline" size="sm" onClick={() => setTagDialogOpen(true)}>
      <Tag size={14} className="mr-1" /> Add Tag
    </Button>
    <Button variant="ghost" size="sm" onClick={() => setSelectedIds(new Set())}>
      Clear
    </Button>
  </div>
)}
```

**Backend — `DELETE /api/documents/bulk`**
```python
@router.delete("/documents/bulk")
async def bulk_delete_documents(body: BulkDeleteBody, ...):
    # body.document_ids: List[str]
    # Verify all docs belong to current user's org before deleting
    await db.execute(
        delete(Document).where(
            Document.id.in_(body.document_ids),
            Document.org_id == current_user.org_id
        )
    )
    await db.commit()
    return { "deleted": len(body.document_ids) }
```

**Backend — `PATCH /api/documents/bulk/tags`**
```python
@router.patch("/documents/bulk/tags")
async def bulk_add_tags(body: BulkTagBody, ...):
    # body.document_ids, body.tags
    # Appends tags to existing tags array, no duplicates
```

Bulk delete confirmation: use shadcn `AlertDialog` — "Delete 12 documents? This cannot be undone."

**End of Day 7 checklist:**
- [ ] Filter bar: status, file type, date range, filename search all work
- [ ] Sort by name, date, size works
- [ ] Clear filters button resets all filters
- [ ] Checkbox column in document table, select all works
- [ ] Bulk delete with confirmation dialog
- [ ] Bulk add tag dialog

---

## Day 8 — Usage Quota Meter + Knowledge Gap Detection (#28, #23)

**Goal:** Users always know where they stand on quota, and the system proactively tells them when their knowledge base has gaps.

**Features:** #28 Usage Quota Meter, #23 Knowledge Gap Detection

---

### #28 — Usage Quota Meter

**Database:** Add to `organizations` table:
```sql
ALTER TABLE organizations ADD COLUMN monthly_query_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE organizations ADD COLUMN quota_reset_date DATE NOT NULL DEFAULT CURRENT_DATE;
```

**Backend — `app/services/quota.py`**
```python
import redis.asyncio as redis
from app.config import settings

redis_client = redis.from_url(settings.REDIS_URL)

async def increment_usage(org_id: str) -> tuple[int, int]:
    """Returns (used, limit). Increments Redis counter, syncs to Postgres periodically."""
    key = f"quota:{org_id}:{get_current_month()}"  # e.g. "quota:abc123:2024-02"
    used = await redis_client.incr(key)
    await redis_client.expire(key, 60 * 60 * 24 * 35)  # 35 days TTL

    # Sync to Postgres every 10 increments (reduce DB writes)
    if used % 10 == 0:
        await sync_quota_to_db(org_id, used)

    limit = await get_org_plan_limit(org_id)
    return used, limit

async def get_usage(org_id: str) -> dict:
    key = f"quota:{org_id}:{get_current_month()}"
    used = int(await redis_client.get(key) or 0)
    limit = await get_org_plan_limit(org_id)
    reset_date = get_first_of_next_month()
    return { "used": used, "limit": limit, "reset_date": reset_date }
```

**Backend — `GET /api/usage/me`**
```python
@router.get("/usage/me")
async def get_my_usage(current_user: User = Depends(get_current_user)):
    return await get_usage(current_user.org_id)
```

**Frontend — `components/sidebar/QuotaMeter.tsx`**

In the sidebar footer, below the user avatar:
```tsx
export function QuotaMeter() {
  const { data } = useQuery('usage', () => fetch('/api/usage/me').then(r => r.json()))
  if (!data) return null

  const pct = (data.used / data.limit) * 100
  const isWarning = pct >= 80
  const isExceeded = pct >= 100

  return (
    <div className="px-3 py-2 border-t">
      <div className="flex justify-between text-xs text-muted-foreground mb-1">
        <span>{data.used.toLocaleString()} / {data.limit.toLocaleString()} queries</span>
        <span>resets {formatDate(data.reset_date)}</span>
      </div>
      <div className="h-1.5 bg-muted rounded-full overflow-hidden">
        <div
          className={cn("h-full rounded-full transition-all",
            isExceeded ? "bg-red-500" : isWarning ? "bg-amber-500" : "bg-primary"
          )}
          style={{ width: `${Math.min(pct, 100)}%` }}
        />
      </div>
      {isWarning && !isExceeded && (
        <p className="text-xs text-amber-600 mt-1">Upgrade to get more queries →</p>
      )}
    </div>
  )
}
```

**Quota exceeded modal:** In `execute_task()`, if `used >= limit`, return HTTP 402. On the frontend, catch 402 response and show a shadcn Dialog with upgrade CTA instead of the error toast.

---

### #23 — Knowledge Gap Detection

**Backend — `app/services/llm.py`**

In `execute_task()`, after the vector search step, evaluate chunk quality:
```python
def detect_knowledge_gap(chunks: list, query: str) -> dict | None:
    """Returns gap info if search results are weak."""
    if len(chunks) == 0:
        return {
            "topic": extract_topic(query),  # simple noun phrase extraction
            "suggestion": f"Upload a document about '{extract_topic(query)}'"
        }
    avg_score = sum(c.score for c in chunks) / len(chunks)
    if avg_score < 0.35:  # Below similarity threshold
        return {
            "topic": extract_topic(query),
            "suggestion": f"Your knowledge base has limited information on this topic. Consider uploading relevant documents."
        }
    return None
```

**SSE Event:** In the SSE streaming handler, emit the gap event before the first text chunk:
```python
if gap := detect_knowledge_gap(chunks, query):
    yield f"data: {json.dumps({'type': 'knowledge_gap', 'gap': gap})}\n\n"
```

**Frontend — `hooks/useChat.ts`**

Handle the `knowledge_gap` SSE event type:
```tsx
if (event.type === 'knowledge_gap') {
  setKnowledgeGap(event.gap)
  return  // Don't add to message content
}
```

**`components/chat/KnowledgeGapWarning.tsx`**

Render below the chat input, above the message being typed:
```tsx
{knowledgeGap && (
  <div className="flex items-start gap-2 p-3 bg-amber-50 dark:bg-amber-950 border border-amber-200 rounded-lg mb-2 text-sm">
    <AlertTriangle size={16} className="text-amber-500 shrink-0 mt-0.5" />
    <div>
      <p className="font-medium text-amber-800 dark:text-amber-200">Limited context found</p>
      <p className="text-amber-700 dark:text-amber-300">{knowledgeGap.suggestion}</p>
      <button
        onClick={() => router.push('/documents?upload=true')}
        className="text-amber-600 underline text-xs mt-1"
      >
        Upload a document →
      </button>
    </div>
    <button onClick={() => setKnowledgeGap(null)} className="ml-auto text-amber-400">
      <X size={14} />
    </button>
  </div>
)}
```

Clear the gap warning when the user starts a new message.

**End of Day 8 checklist:**
- [ ] `GET /api/usage/me` returns correct used/limit/reset_date
- [ ] Sidebar quota meter renders, updates every minute via polling
- [ ] Amber warning state at 80%, red at 100%
- [ ] 402 response triggers upgrade modal (not error toast)
- [ ] Knowledge gap fires SSE event when avg chunk score < 0.35 or 0 chunks
- [ ] Inline gap warning renders in chat with upload link

---

## Day 9 — Conversation Search + Custom Org Instructions (#21, #67)

**Goal:** Users can find past conversations instantly. Admins can give the AI permanent context about their organization.

**Features:** #21 Conversation Search, #67 Custom Organization Instructions

---

### #21 — Conversation Search

**Database:** Add full-text search index:
```sql
-- Add tsvector column for FTS on message content
ALTER TABLE messages ADD COLUMN content_tsv TSVECTOR
  GENERATED ALWAYS AS (to_tsvector('english', content)) STORED;

CREATE INDEX idx_messages_content_tsv ON messages USING GIN(content_tsv);
CREATE INDEX idx_conversations_title ON conversations USING GIN(to_tsvector('english', title));
```

**Backend — Update `GET /api/chat/conversations`**
```python
@router.get("/chat/conversations")
async def list_conversations(q: Optional[str] = None, ...):
    query = select(Conversation).where(Conversation.org_id == current_user.org_id)

    if q:
        # Search in title OR message content
        query = query.where(
            or_(
                Conversation.title.ilike(f"%{q}%"),
                Conversation.id.in_(
                    select(Message.conversation_id).where(
                        Message.content_tsv.op('@@')(func.plainto_tsquery('english', q))
                    )
                )
            )
        )

    query = query.order_by(Conversation.updated_at.desc()).limit(50)
    return await db.execute(query)
```

**Frontend — `components/sidebar/ConversationSearch.tsx`**
```tsx
export function ConversationSearch() {
  const [query, setQuery] = useState('')
  const debouncedQuery = useDebounce(query, 300)

  return (
    <div className="px-2 py-1">
      <div className="relative">
        <Search size={14} className="absolute left-2.5 top-2.5 text-muted-foreground" />
        <Input
          placeholder="Search conversations..."
          value={query}
          onChange={e => setQuery(e.target.value)}
          className="pl-8 h-8 text-sm"
        />
        {query && (
          <button onClick={() => setQuery('')} className="absolute right-2.5 top-2.5">
            <X size={12} className="text-muted-foreground" />
          </button>
        )}
      </div>
    </div>
  )
}
```

Place this above the conversation list in the sidebar. Pass `debouncedQuery` to the conversations query.

---

### #67 — Custom Organization Instructions

**Database:**
```sql
ALTER TABLE organizations ADD COLUMN ai_instructions TEXT;
-- Stores the custom instructions, max ~500 chars enforced at API level
```

**Backend — `PATCH /api/organizations/settings`**
```python
@router.patch("/organizations/settings")
async def update_org_settings(body: OrgSettingsBody, ...):
    # body.ai_instructions: Optional[str] (max 500 chars)
    if body.ai_instructions and len(body.ai_instructions) > 500:
        raise HTTPException(400, "AI instructions must be 500 characters or less")
    org.ai_instructions = body.ai_instructions
    await db.commit()
```

**Wire into system prompt** — In `app/services/llm.py`, prepend org instructions to every system prompt:
```python
async def build_system_prompt(org_id: str, base_prompt: str) -> str:
    org = await get_org(org_id)
    if org.ai_instructions:
        return f"Organization Context:\n{org.ai_instructions}\n\n---\n\n{base_prompt}"
    return base_prompt
```

**Frontend — `app/settings/ai/page.tsx`** — New settings tab "AI":
```tsx
<div className="space-y-4">
  <div>
    <label className="text-sm font-medium">AI Instructions</label>
    <p className="text-xs text-muted-foreground mb-2">
      This context is prepended to every AI response. Use it to describe your company, 
      set tone guidelines, or specify things the AI should never mention.
    </p>
    <Textarea
      value={instructions}
      onChange={e => setInstructions(e.target.value)}
      placeholder="We're a B2B SaaS for accounting firms. Our tone is formal but approachable. Never mention competitor pricing."
      maxLength={500}
      rows={5}
    />
    <p className="text-xs text-muted-foreground mt-1 text-right">
      {instructions.length}/500 characters
    </p>
  </div>
  <Button onClick={save}>Save Instructions</Button>
</div>
```

Add "AI" to the settings sidebar nav between "Team" and "Billing".

**End of Day 9 checklist:**
- [ ] FTS index on messages and conversations created
- [ ] Search bar in sidebar searches title + message content
- [ ] Debounced 300ms, clears with X button
- [ ] `ai_instructions` column on organizations table
- [ ] Instructions prepended to every system prompt
- [ ] Settings > AI tab with textarea, character counter, save

---

## Day 10 — Answer Confidence + Document Q&A Mode + Citation Deep-Link (#90, #100, #9)

**Goal:** Make responses more trustworthy (confidence scores), more precise (document-scoped Q&A), and more navigable (PDF deep-links).

**Features:** #90 Confidence Indicators, #100 Document Q&A Mode, #9 Citation Deep-Link

---

### #90 — Answer Confidence Indicators

The cosine similarity scores already come back from your vector search. Surface them:

**Backend — SSE response:** Add a `confidence` event before the text stream:
```python
avg_score = sum(c.score for c in chunks) / len(chunks) if chunks else 0
confidence_level = "high" if avg_score > 0.75 else "medium" if avg_score > 0.45 else "low"
confidence_score = round(avg_score * 10, 1)  # 0–10 scale

yield f"data: {json.dumps({'type': 'confidence', 'level': confidence_level, 'score': confidence_score})}\n\n"
```

**Frontend — `components/chat/ConfidenceBadge.tsx`**
```tsx
const colors = {
  high: "bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300",
  medium: "bg-amber-100 text-amber-700 dark:bg-amber-900 dark:text-amber-300",
  low: "bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300"
}

export function ConfidenceBadge({ level, score }: { level: string, score: number }) {
  return (
    <span className={cn("text-xs px-2 py-0.5 rounded-full font-medium", colors[level])}>
      {level === 'high' ? '●' : level === 'medium' ? '◑' : '○'} {level} confidence ({score}/10)
    </span>
  )
}
```

Render `<ConfidenceBadge />` above the assistant message text, below the avatar.

---

### #100 — Document Q&A Direct Mode

**Frontend — `components/documents/DocumentRow.tsx`**

Add a "Ask questions" button to each ready document row:
```tsx
<Button
  variant="ghost"
  size="sm"
  onClick={() => router.push(`/chat/new?document_id=${doc.id}`)}
>
  <MessageSquare size={14} className="mr-1" /> Ask
</Button>
```

**`app/chat/new/page.tsx`** — Read `document_id` from URL params:
```tsx
const searchParams = useSearchParams()
const documentId = searchParams.get('document_id')
```

If `documentId` is present, show a banner in the chat header:
```tsx
{scopedDocument && (
  <div className="flex items-center gap-2 px-4 py-2 bg-blue-50 dark:bg-blue-950 border-b text-sm">
    <FileText size={14} className="text-blue-500" />
    <span>Asking questions about: <strong>{scopedDocument.name}</strong></span>
    <button onClick={clearScope} className="ml-auto text-xs text-muted-foreground">
      Search all documents ×
    </button>
  </div>
)}
```

**Backend — `execute_task()`**

Accept optional `document_id` param. If present, filter vector search to only that document:
```python
async def execute_task(query: str, ..., document_id: Optional[str] = None):
    filter_conditions = {"org_id": org_id}
    if document_id:
        filter_conditions["document_id"] = document_id
    chunks = await vector_search(query, filter=filter_conditions)
```

---

### #9 — Citation Deep-Link to PDF

**Backend — `GET /api/documents/{id}/signed-url`**
```python
@router.get("/documents/{document_id}/signed-url")
async def get_signed_url(document_id: str, ...):
    # Generate a time-limited (15 min) signed URL from your storage provider (S3/GCS/Supabase)
    signed_url = await storage.generate_signed_url(
        key=document.storage_key,
        expires_in=900  # 15 minutes
    )
    return { "url": signed_url }
```

**Frontend — `components/chat/CitationCard.tsx`**

Update citation cards to show page number and be clickable:
```tsx
export function CitationCard({ citation }: { citation: Citation }) {
  const openDoc = async () => {
    const { url } = await fetch(`/api/documents/${citation.document_id}/signed-url`).then(r => r.json())
    const pdfUrl = citation.page_number ? `${url}#page=${citation.page_number}` : url
    window.open(pdfUrl, '_blank')
  }

  return (
    <button
      onClick={openDoc}
      className="flex items-center gap-2 px-3 py-1.5 bg-muted rounded-lg text-xs hover:bg-muted/80 transition-colors"
    >
      <FileText size={12} />
      <span className="truncate max-w-32">{citation.document_name}</span>
      {citation.page_number && (
        <span className="text-muted-foreground shrink-0">p.{citation.page_number}</span>
      )}
      <ExternalLink size={10} className="text-muted-foreground shrink-0" />
    </button>
  )
}
```

Ensure `page_number` is stored in the chunks table and returned in citation metadata. If not already present: `ALTER TABLE chunks ADD COLUMN page_number INTEGER`.

**End of Day 10 checklist:**
- [ ] Confidence badge renders above assistant messages, colored by level
- [ ] "Ask" button on document rows navigates to scoped chat
- [ ] Scoped chat shows blue banner with document name, "Search all" to clear
- [ ] Vector search filters by `document_id` when scope is active
- [ ] Citation cards show page number, clicking opens PDF at correct page
- [ ] Signed URLs expire after 15 minutes

---

## Day 11 — Custom File Types + Chunk-Level Retry (#88, #76)

**Goal:** Expand ingestion to cover the file types that exist in every real company's knowledge base. Add resilient retry so partial failures don't waste everything.

**Features:** #88 Custom File Type Support, #76 Proper Error Recovery in Ingestion

---

### #88 — Custom File Type Support

Install Python dependencies:
```bash
pip install openpyxl python-pptx beautifulsoup4 lxml
```

**`app/services/ingestion/extractors.py`** — Add extractors for each new type:

```python
import openpyxl
from pptx import Presentation
from bs4 import BeautifulSoup

def extract_xlsx(file_bytes: bytes) -> str:
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True)
    text_parts = []
    for sheet in wb.worksheets:
        text_parts.append(f"Sheet: {sheet.title}")
        for row in sheet.iter_rows(values_only=True):
            row_text = " | ".join(str(cell) for cell in row if cell is not None)
            if row_text.strip():
                text_parts.append(row_text)
    return "\n".join(text_parts)

def extract_pptx(file_bytes: bytes) -> str:
    prs = Presentation(io.BytesIO(file_bytes))
    text_parts = []
    for i, slide in enumerate(prs.slides, 1):
        text_parts.append(f"Slide {i}:")
        for shape in slide.shapes:
            if hasattr(shape, "text") and shape.text.strip():
                text_parts.append(shape.text.strip())
    return "\n".join(text_parts)

def extract_html(file_bytes: bytes) -> str:
    soup = BeautifulSoup(file_bytes, 'lxml')
    for tag in soup(['script', 'style', 'nav', 'footer']):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)

def extract_csv(file_bytes: bytes) -> str:
    import csv, io
    reader = csv.DictReader(io.StringIO(file_bytes.decode('utf-8', errors='ignore')))
    lines = [", ".join(f"{k}: {v}" for k, v in row.items()) for row in reader]
    return "\n".join(lines)
```

**Update `get_extractor(file_type)`** in `app/services/ingestion/router.py`:
```python
EXTRACTORS = {
    'pdf': extract_pdf,
    'docx': extract_docx,
    'txt': extract_txt,
    'xlsx': extract_xlsx,
    'pptx': extract_pptx,
    'html': extract_html,
    'csv': extract_csv,
}
```

Update file type validation in `POST /api/documents/upload` to accept the new types.

---

### #76 — Chunk-Level Error Recovery

**Database:**
```sql
ALTER TABLE chunks ADD COLUMN embedding_status VARCHAR(50) NOT NULL DEFAULT 'pending';
-- Values: pending | embedded | failed
ALTER TABLE chunks ADD COLUMN embedding_error TEXT;
ALTER TABLE chunks ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0;
```

**Update Inngest document processing function:**

```python
# In inngest_functions.py — document.process function
@inngest.create_function(fn_id="document/process", trigger=inngest.TriggerEvent("document/process"))
async def process_document(ctx, step):
    doc_id = ctx.event.data["document_id"]

    # Step 1: Extract text and create chunks
    chunks = await step.run("extract-and-chunk", lambda: extract_and_create_chunks(doc_id))

    # Step 2: Embed each chunk INDIVIDUALLY with per-chunk error handling
    failed_chunks = []
    for chunk in chunks:
        try:
            await step.run(
                f"embed-chunk-{chunk.id}",
                lambda: embed_single_chunk(chunk.id)
            )
        except Exception as e:
            failed_chunks.append(chunk.id)
            await mark_chunk_failed(chunk.id, str(e))

    # Step 3: Determine document status
    total = len(chunks)
    failed = len(failed_chunks)

    if failed == 0:
        await update_document_status(doc_id, 'ready')
    elif failed == total:
        await update_document_status(doc_id, 'failed')
    else:
        # NEW: partial ready state
        await update_document_status(doc_id, 'partial', metadata={
            "embedded": total - failed,
            "failed": failed,
            "total": total
        })
```

**`embed_single_chunk(chunk_id)` function:**
```python
async def embed_single_chunk(chunk_id: str):
    chunk = await db.get(Chunk, chunk_id)
    try:
        embedding = await get_embedding(chunk.content)
        chunk.embedding = embedding
        chunk.embedding_status = 'embedded'
        chunk.retry_count += 1
        await db.commit()
    except Exception as e:
        chunk.embedding_status = 'failed'
        chunk.embedding_error = str(e)
        await db.commit()
        raise  # Re-raise so Inngest step marks it failed
```

**Document status "partial":** Show as a new amber badge in the UI:
```tsx
// In StatusBadge component
case 'partial':
  return <Badge variant="outline" className="text-amber-600 border-amber-300">
    ⚠ Partial ({doc.metadata.embedded}/{doc.metadata.total} chunks)
  </Badge>
```

**Retry failed chunks:** The existing "Retry" button (Day 5) now triggers `POST /documents/{id}/reprocess`. Update the reprocess endpoint to only re-embed `embedding_status = 'failed'` chunks, not extract/chunk the whole document again:
```python
@router.post("/documents/{document_id}/reprocess")
async def reprocess_document(document_id: str, ...):
    doc = await db.get(Document, document_id)
    failed_chunks = await db.execute(
        select(Chunk).where(
            Chunk.document_id == document_id,
            Chunk.embedding_status == 'failed'
        )
    )
    if failed_chunks:
        # Only re-embed failed chunks
        await inngest.send("document/retry-chunks", {
            "document_id": document_id,
            "chunk_ids": [c.id for c in failed_chunks]
        })
    else:
        # Full reprocess if no chunk tracking data
        await inngest.send("document/process", {"document_id": document_id})
```

**End of Day 11 checklist:**
- [ ] XLSX, PPTX, HTML, CSV files upload and extract text correctly
- [ ] Extracted text is chunked and embedded same as PDF/DOCX
- [ ] `embedding_status` tracked per chunk
- [ ] Documents with some failed chunks show "Partial" badge
- [ ] Retry button only re-embeds failed chunks, not full reprocess

---

## Day 12 — Context Window Intelligence + Learning from Usage (#53, #49)

**Goal:** The product gets smarter automatically. Long conversations stay coherent. High-value documents get surfaced.

**Features:** #53 Context Window Intelligence, #49 Learning from Usage

---

### #53 — Context Window Intelligence

**Database:**
```sql
ALTER TABLE conversations ADD COLUMN summary TEXT;
ALTER TABLE conversations ADD COLUMN summary_turn_count INTEGER NOT NULL DEFAULT 0;
```

**Backend — `app/services/llm.py`**

Before building the context for `execute_task()`, check if summarization is needed:
```python
async def get_conversation_context(conversation_id: str, messages: list) -> list:
    """Returns messages to include in context, summarizing if too long."""
    if len(messages) <= 8:
        return messages  # No summarization needed

    conversation = await get_conversation(conversation_id)

    # Check if we need a fresh summary
    if len(messages) - conversation.summary_turn_count >= 8:
        summary = await summarize_early_messages(messages[:-6])  # Summarize all but last 6
        await save_summary(conversation_id, summary, len(messages))
        conversation.summary = summary

    # Build context: summary + last 6 messages
    context = []
    if conversation.summary:
        context.append({
            "role": "system",
            "content": f"Earlier conversation summary:\n{conversation.summary}"
        })
    context.extend(messages[-6:])  # Always keep last 6 turns fresh
    return context

async def summarize_early_messages(messages: list) -> str:
    """Calls LLM to summarize early conversation turns."""
    prompt = f"""Summarize this conversation in 3 concise bullet points for context continuity. 
    Focus on: what the user asked about, key information provided, any decisions made.
    
    Conversation:
    {format_messages_for_summary(messages)}"""

    response = await call_llm([{"role": "user", "content": prompt}], max_tokens=200)
    return response
```

This is fully transparent to the user — no UI change needed.

---

### #49 — Learning from Usage

**Database:**
```sql
-- Track which chunks appear in responses
CREATE TABLE chunk_citations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  chunk_id UUID NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
  message_id UUID NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  cited_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_chunk_citations_chunk ON chunk_citations(chunk_id);
CREATE INDEX idx_chunk_citations_org ON chunk_citations(org_id);

-- Aggregate stats per document
ALTER TABLE documents ADD COLUMN citation_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE documents ADD COLUMN last_cited_at TIMESTAMP;
```

**Backend — Record citations in `execute_task()`:**
```python
# After LLM call, record which chunks were used as sources
for chunk in source_chunks:
    db.add(ChunkCitation(
        chunk_id=chunk.id,
        message_id=message.id,
        conversation_id=conversation_id,
        org_id=org_id
    ))
    # Increment document citation count
    await db.execute(
        update(Document)
        .where(Document.id == chunk.document_id)
        .values(
            citation_count=Document.citation_count + 1,
            last_cited_at=datetime.utcnow()
        )
    )
await db.commit()
```

**Retrieval boost** — In vector search, apply a multiplier to chunks from high-performing documents:
```python
async def vector_search_with_boost(query: str, org_id: str, document_id: Optional[str] = None):
    base_results = await vector_search(query, org_id, document_id)

    # Fetch citation counts for result documents
    doc_ids = [r.document_id for r in base_results]
    citation_counts = await get_citation_counts(doc_ids, org_id)
    max_citations = max(citation_counts.values(), default=1)

    # Boost score: up to 15% boost for most-cited documents
    for result in base_results:
        count = citation_counts.get(result.document_id, 0)
        boost = (count / max_citations) * 0.15
        result.score = result.score * (1 + boost)

    return sorted(base_results, key=lambda r: r.score, reverse=True)
```

**`GET /api/admin/knowledge-intelligence`** endpoint:
```python
@router.get("/admin/knowledge-intelligence")
async def get_knowledge_intelligence(current_user: User = Depends(get_admin_user)):
    org_id = current_user.org_id

    # Most valuable documents
    top_docs = await db.execute(
        select(Document)
        .where(Document.org_id == org_id, Document.status == 'ready')
        .order_by(Document.citation_count.desc())
        .limit(5)
    )

    # Never-cited documents (potential dead weight)
    unused_docs = await db.execute(
        select(Document)
        .where(Document.org_id == org_id, Document.citation_count == 0, Document.status == 'ready')
        .order_by(Document.created_at.asc())
        .limit(10)
    )

    total_docs = await get_total_doc_count(org_id)
    total_citations = await get_total_citation_count(org_id)

    return {
        "top_documents": top_docs,
        "unused_documents": unused_docs,
        "total_documents": total_docs,
        "total_citations": total_citations
    }
```

**Frontend — `app/admin/knowledge/page.tsx`**

Simple two-panel view:
```
Knowledge Intelligence

[Most Cited Documents]               [Never Cited Documents]
1. Employee Handbook — 423 cites     • Old Pricing Sheet (Jan 2022)
2. Refund Policy — 287 cites         • Q1 2023 Meeting Notes
3. Onboarding Guide — 156 cites      • Draft Marketing Plan
                                     [These may not be useful or indexed correctly]
```

**End of Day 12 checklist:**
- [ ] Conversations with 9+ turns auto-summarize early messages
- [ ] Summary stored in DB, refreshed every 8 new turns
- [ ] `chunk_citations` table populated on every execute_task call
- [ ] Citation counts update on `documents` table
- [ ] High-citation documents get up to 15% score boost in retrieval
- [ ] Admin Knowledge Intelligence page shows top and unused docs

---

## Day 13 — Knowledge Refresh Notifications + Automated Webhooks (#38, #85, #99)

**Goal:** The knowledge base stays accurate over time (not just at upload). External systems can trigger document ingestion automatically.

**Features:** #38 Automated Knowledge Refresh, #85 Zapier Integration, #99 Custom Webhooks

---

### #38 — Automated Knowledge Refresh

**Database:**
```sql
ALTER TABLE documents ADD COLUMN review_frequency_days INTEGER;
-- NULL = never auto-review. Common values: 90 (quarterly), 180 (biannual), 365 (annual)
ALTER TABLE documents ADD COLUMN review_due_at TIMESTAMP;
ALTER TABLE documents ADD COLUMN last_reviewed_at TIMESTAMP;
```

**Backend — `PATCH /api/documents/{id}`**

Add `review_frequency_days` to the document update endpoint. When set, auto-calculate `review_due_at`:
```python
if body.review_frequency_days:
    doc.review_frequency_days = body.review_frequency_days
    doc.review_due_at = datetime.utcnow() + timedelta(days=body.review_frequency_days)
```

**Inngest cron — Weekly review check:**
```python
@inngest.create_function(
    fn_id="documents/review-check",
    trigger=inngest.TriggerCron(cron="0 8 * * MON")  # Every Monday 8am UTC
)
async def check_document_reviews(ctx, step):
    due_docs = await step.run("fetch-due-docs", lambda: get_documents_due_for_review())
    # Group by org
    by_org = group_by_org(due_docs)
    for org_id, docs in by_org.items():
        admin = await step.run(f"admin-{org_id}", lambda: get_org_admin(org_id))
        await step.run(f"email-{org_id}", lambda: send_review_reminder_email(admin, docs))
```

**Review reminder email** (add to Day 3 email templates):
```
Subject: 3 documents in your knowledge base are due for review

Employee Handbook — last updated 91 days ago [Review →]
Refund Policy — last updated 95 days ago [Review →]
Pricing Sheet — last updated 187 days ago [Review →]
```

**In-app banner** — In `components/chat/ChatInterface.tsx`, check if any source document in the response has `review_due_at < NOW()`:
```tsx
{overdueSourceDocs.length > 0 && (
  <div className="text-xs text-amber-600 flex items-center gap-1 mt-1">
    <Clock size={12} />
    This answer is based on a document that may be outdated.{' '}
    <Link href={`/documents/${overdueSourceDocs[0].id}`}>Review →</Link>
  </div>
)}
```

**"Mark as Reviewed" button** in document detail view → sets `last_reviewed_at = NOW()`, recalculates `review_due_at`.

---

### #85/#99 — Zapier Integration + Custom Webhooks

These share the same outbound webhook infrastructure.

**Database:**
```sql
CREATE TABLE webhooks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  name VARCHAR(255) NOT NULL,
  url TEXT NOT NULL,
  events TEXT[] NOT NULL,  -- ['document.processed', 'query.completed', 'document.failed']
  secret VARCHAR(255),     -- For HMAC signature verification
  is_active BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  last_triggered_at TIMESTAMP,
  last_status INTEGER       -- HTTP status of last delivery
);
```

**`app/services/webhooks.py`**
```python
import hmac, hashlib, httpx

async def trigger_webhook(org_id: str, event: str, payload: dict):
    """Fire all matching webhooks for this org and event."""
    webhooks = await get_webhooks_for_event(org_id, event)
    for webhook in webhooks:
        await deliver_webhook(webhook, event, payload)

async def deliver_webhook(webhook, event: str, payload: dict):
    body = json.dumps({"event": event, "data": payload, "timestamp": datetime.utcnow().isoformat()})
    headers = {
        "Content-Type": "application/json",
        "X-CompanyBrain-Event": event,
    }
    if webhook.secret:
        sig = hmac.new(webhook.secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        headers["X-CompanyBrain-Signature"] = f"sha256={sig}"

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(webhook.url, content=body, headers=headers)
        await update_webhook_last_status(webhook.id, resp.status_code)
```

**Wire up webhook triggers:**

1. In Inngest `document/process`, on status = 'ready': `await trigger_webhook(org_id, 'document.processed', {"document_id": ..., "name": ...})`
2. In `execute_task()`, after response: `await trigger_webhook(org_id, 'query.completed', {"query": ..., "response_length": ...})`
3. On document failure: `await trigger_webhook(org_id, 'document.failed', {"document_id": ..., "error": ...})`

**Zapier-specific:** The webhook endpoint is the same. Users paste their Zapier webhook URL into the webhook config. Zapier receives the `document.processed` event and can trigger "Add row to Google Sheet", "Send Slack message", etc.

**Frontend — `app/settings/webhooks/page.tsx`**
```
Settings > Webhooks

[Add Webhook]
Name: [__________]
URL: [https://hooks.zapier.com/...]
Events: [✓] document.processed  [✓] document.failed  [ ] query.completed
Secret (optional): [__________]

[Active Webhooks]
| Name          | URL              | Events | Last Status | Actions |
| Zapier Sync   | hooks.zapier...  | 2      | 200 ✓       | Edit Delete |
```

**End of Day 13 checklist:**
- [ ] `review_frequency_days` and `review_due_at` on documents
- [ ] Admin can set review frequency per document in document settings
- [ ] Monday cron sends review reminder email listing overdue docs
- [ ] In-chat banner appears when source document is overdue
- [ ] "Mark as Reviewed" button resets timer
- [ ] `webhooks` table created, CRUD endpoints working
- [ ] Webhooks fire on `document.processed`, `document.failed`, `query.completed`
- [ ] HMAC signature in header for verification
- [ ] Webhooks settings page with last delivery status

---

## Day 14 — Google Drive + Notion + Email Forwarding Integration (#86, #87, #83)

**Goal:** The knowledge base feeds itself. Documents from Drive, Notion, and email threads are automatically ingested.

**Features:** #86 Google Drive Sync, #87 Notion Integration, #83 Email Forward-to-Brain

---

### #86 — Google Drive Sync

**Database:**
```sql
CREATE TABLE drive_integrations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  access_token TEXT NOT NULL,
  refresh_token TEXT NOT NULL,
  folder_ids TEXT[] NOT NULL DEFAULT '{}',  -- Which folders to watch
  last_synced_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
```

Install:
```bash
pip install google-auth google-auth-oauthlib google-api-python-client
```

**OAuth flow** — `app/api/routes/integrations/drive.py`:
```python
from google_auth_oauthlib.flow import Flow

SCOPES = ['https://www.googleapis.com/auth/drive.readonly']

@router.get("/integrations/drive/connect")
async def drive_connect(current_user: User = Depends(get_current_user)):
    flow = Flow.from_client_config(GOOGLE_CLIENT_CONFIG, scopes=SCOPES)
    flow.redirect_uri = settings.DRIVE_REDIRECT_URI
    auth_url, state = flow.authorization_url(access_type='offline', prompt='consent')
    return { "auth_url": auth_url }

@router.get("/integrations/drive/callback")
async def drive_callback(code: str, state: str, ...):
    flow = Flow.from_client_config(GOOGLE_CLIENT_CONFIG, scopes=SCOPES, state=state)
    flow.redirect_uri = settings.DRIVE_REDIRECT_URI
    flow.fetch_token(code=code)
    creds = flow.credentials
    # Store access_token + refresh_token encrypted in drive_integrations
    db.add(DriveIntegration(org_id=current_user.org_id, access_token=..., refresh_token=...))
    await db.commit()
    return RedirectResponse("/settings/integrations?connected=drive")
```

**Polling Inngest cron — runs every 5 minutes:**
```python
@inngest.create_function(
    fn_id="drive/poll-sync",
    trigger=inngest.TriggerCron(cron="*/5 * * * *")
)
async def poll_drive_sync(ctx, step):
    integrations = await step.run("fetch-integrations", get_all_drive_integrations)
    for integration in integrations:
        await step.run(f"sync-{integration.org_id}", lambda: sync_drive_for_org(integration))

async def sync_drive_for_org(integration: DriveIntegration):
    service = build_drive_service(integration.access_token, integration.refresh_token)
    for folder_id in integration.folder_ids:
        # Query files modified since last_synced_at
        query = f"'{folder_id}' in parents and modifiedTime > '{integration.last_synced_at.isoformat()}'"
        results = service.files().list(q=query, fields="files(id, name, mimeType, modifiedTime)").execute()
        for file in results.get('files', []):
            if file['mimeType'] in SUPPORTED_MIME_TYPES:
                await ingest_drive_file(file, integration.org_id, service)
    await update_last_synced(integration.id)
```

Supported MIME types: `application/pdf`, `application/vnd.google-apps.document` (export as DOCX), `text/plain`, `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`.

**Frontend — `app/settings/integrations/page.tsx`**

Integration card with folder selector:
```tsx
<div className="border rounded-lg p-4">
  <div className="flex items-center gap-3 mb-3">
    <GoogleDriveIcon />
    <div>
      <p className="font-medium">Google Drive</p>
      <p className="text-xs text-muted-foreground">Auto-sync documents from specific folders</p>
    </div>
    {connected ? <Badge>Connected</Badge> : (
      <Button size="sm" onClick={connectDrive}>Connect</Button>
    )}
  </div>
  {connected && (
    <div>
      <p className="text-xs mb-2">Syncing folders:</p>
      {syncedFolders.map(f => <FolderTag key={f.id} folder={f} onRemove={removeFolder} />)}
      <Button size="sm" variant="outline" onClick={addFolder}>+ Add folder</Button>
      <p className="text-xs text-muted-foreground mt-2">Last synced: {lastSynced}</p>
    </div>
  )}
</div>
```

---

### #87 — Notion Integration

**Database:**
```sql
CREATE TABLE notion_integrations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  access_token TEXT NOT NULL,
  workspace_id VARCHAR(255),
  workspace_name VARCHAR(255),
  synced_page_ids TEXT[] NOT NULL DEFAULT '{}',
  last_synced_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
```

Install:
```bash
pip install notion-client
```

**OAuth:** Notion uses OAuth 2.0. Set up a Notion integration at notion.so/my-integrations. Store the `access_token` in `notion_integrations`.

**Polling cron — every 10 minutes:**
```python
@inngest.create_function(
    fn_id="notion/poll-sync",
    trigger=inngest.TriggerCron(cron="*/10 * * * *")
)
async def poll_notion_sync(ctx, step):
    integrations = await step.run("fetch", get_all_notion_integrations)
    for integration in integrations:
        await step.run(f"sync-{integration.org_id}", lambda: sync_notion_for_org(integration))

async def sync_notion_for_org(integration: NotionIntegration):
    client = Client(auth=integration.access_token)
    for page_id in integration.synced_page_ids:
        page = client.pages.retrieve(page_id=page_id)
        last_edited = page['last_edited_time']
        if last_edited > integration.last_synced_at.isoformat():
            # Page was updated — re-ingest
            content = extract_notion_page_content(client, page_id)
            await upsert_document_from_notion(page_id, content, integration.org_id)
```

**Notion page content extraction:**
```python
def extract_notion_page_content(client, page_id: str) -> str:
    blocks = client.blocks.children.list(block_id=page_id)
    text_parts = []
    for block in blocks['results']:
        block_type = block['type']
        if block_type in ['paragraph', 'heading_1', 'heading_2', 'heading_3', 'bulleted_list_item', 'numbered_list_item', 'quote', 'callout']:
            rich_text = block[block_type].get('rich_text', [])
            text = "".join(rt['plain_text'] for rt in rich_text)
            if text:
                text_parts.append(text)
    return "\n".join(text_parts)
```

**Frontend:** Same integrations page card as Drive. "Select pages" opens a modal where the user can browse and select Notion pages by title.

---

### #83 — Email Forward-to-Brain

**Setup:** Create a catch-all email address using your domain and Resend's inbound email feature (or use Mailgun/Postmark inbound parsing).

```
brain@yourdomain.companybrain.app
```

Each org gets a unique email address stored in the organizations table:
```sql
ALTER TABLE organizations ADD COLUMN inbound_email VARCHAR(255) UNIQUE;
-- e.g. brain-abc123@inbound.companybrain.app
```

**Inbound webhook handler — `POST /api/webhooks/email-inbound`:**
```python
@router.post("/webhooks/email-inbound")
async def handle_inbound_email(request: Request):
    # Resend/Mailgun sends parsed email as JSON
    data = await request.json()
    to_address = data['to']
    subject = data.get('subject', 'Email Thread')
    body_text = data.get('text', '') or strip_html(data.get('html', ''))
    from_address = data['from']

    # Find org by inbound email address
    org = await get_org_by_inbound_email(to_address)
    if not org:
        return {"status": "ignored"}

    # Combine subject + body as document content
    content = f"Subject: {subject}\nFrom: {from_address}\n\n{body_text}"

    # Create document record and trigger ingestion
    doc = Document(
        name=f"Email: {subject[:80]}",
        content=content,
        file_type='email',
        source='email_forward',
        org_id=org.id
    )
    db.add(doc)
    await db.commit()
    await inngest.send("document/process-text", {"document_id": doc.id})

    return {"status": "ingested"}
```

**Frontend — `app/settings/integrations/page.tsx`**

Email forwarding card:
```tsx
<div className="border rounded-lg p-4">
  <p className="font-medium">Email Forward-to-Brain</p>
  <p className="text-xs text-muted-foreground mb-3">Forward any email thread to your brain inbox</p>
  <div className="flex items-center gap-2 bg-muted rounded p-2">
    <code className="text-sm flex-1">{org.inbound_email}</code>
    <Button size="sm" variant="ghost" onClick={() => copyToClipboard(org.inbound_email)}>
      <Copy size={14} />
    </Button>
  </div>
  <p className="text-xs text-muted-foreground mt-2">
    Forward emails here to automatically add them to your knowledge base.
  </p>
</div>
```

**End of Day 14 checklist:**
- [ ] Google Drive OAuth connects, stores tokens
- [ ] Drive polling cron runs every 5 min, ingests new/updated files
- [ ] Folder selection UI in integrations settings
- [ ] Notion OAuth connects, page selection modal works
- [ ] Notion polling cron runs every 10 min, detects page edits
- [ ] Each org has a unique inbound email address
- [ ] Forwarded emails are parsed, stored as documents, and ingested
- [ ] Email integration shown in settings with copy button

---

## Day 15 — Developer API + Slack Bot (#47, #31)

**Goal:** The two highest-leverage distribution features. API access turns Company Brain into a platform. Slack makes it viral inside every organization.

**Features:** #47 Developer API, #31 Slack Bot Integration

---

### #47 — Developer API with API Keys

**Database:**
```sql
CREATE TABLE api_keys (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  created_by UUID NOT NULL REFERENCES users(id),
  scope VARCHAR(50) NOT NULL DEFAULT 'org',  -- 'org' | 'user'
  name VARCHAR(255) NOT NULL,
  key_hash VARCHAR(255) NOT NULL UNIQUE,  -- bcrypt hash of the actual key
  key_prefix VARCHAR(10) NOT NULL,        -- First 8 chars for display e.g. "cb_live_a"
  last_used_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  revoked_at TIMESTAMP
);
```

**Key generation:**
```python
import secrets, hashlib

def generate_api_key() -> tuple[str, str, str]:
    """Returns (full_key, key_hash, key_prefix)"""
    raw = secrets.token_urlsafe(32)
    full_key = f"cb_live_{raw}"  # Prefix for easy identification
    key_hash = hashlib.sha256(full_key.encode()).hexdigest()
    key_prefix = full_key[:12]  # Show "cb_live_xxxx" in UI
    return full_key, key_hash, key_prefix
```

**Backend — API key management routes:**
```python
# POST /api/settings/api-keys — generate new key
@router.post("/settings/api-keys")
async def create_api_key(body: CreateKeyBody, ...):
    full_key, key_hash, key_prefix = generate_api_key()
    db.add(ApiKey(
        org_id=current_user.org_id,
        created_by=current_user.id,
        scope=body.scope,  # 'org' or 'user'
        name=body.name,
        key_hash=key_hash,
        key_prefix=key_prefix
    ))
    await db.commit()
    # IMPORTANT: Return full_key ONCE. Never store or return again.
    return { "key": full_key, "prefix": key_prefix }

# GET /api/settings/api-keys — list keys (prefix only, never full key)
# DELETE /api/settings/api-keys/{id} — revoke
```

**Public API — `app/api/routes/public_api.py`**

Authentication middleware:
```python
async def get_api_key_context(authorization: str = Header(...)):
    if not authorization.startswith("Bearer cb_live_"):
        raise HTTPException(401, "Invalid API key format")
    key_hash = hashlib.sha256(authorization[7:].encode()).hexdigest()
    api_key = await db.execute(
        select(ApiKey).where(ApiKey.key_hash == key_hash, ApiKey.revoked_at.is_(None))
    )
    if not api_key:
        raise HTTPException(401, "Invalid or revoked API key")
    await update_last_used(api_key.id)
    return api_key
```

**`POST /v1/query`** — The main public endpoint:
```python
@router.post("/v1/query")
async def public_query(
    body: PublicQueryBody,  # { message: str, collection?: str }
    api_key: ApiKey = Depends(get_api_key_context)
):
    """
    Query your Company Brain knowledge base programmatically.
    Rate limited by plan.
    """
    result = await execute_task(
        query=body.message,
        org_id=api_key.org_id,
        document_id=None,
        stream=False  # Public API returns full response, no streaming
    )
    return {
        "output": result.text,
        "sources": [
            { "document_name": s.name, "page": s.page_number }
            for s in result.sources
        ],
        "confidence": result.confidence
    }
```

**Frontend — `app/settings/api/page.tsx`**

API settings page:
```
Settings > API

[Generate API Key]
Name: [______________]
Scope: [Org-level ▼]  [Generate]

[Your API Keys]
| Name          | Scope | Created    | Last Used  | Actions |
| Production    | Org   | 3 days ago | 1 hour ago | Revoke  |
| Testing       | User  | today      | Never      | Revoke  |

[Documentation]
Quick start:
curl -X POST https://api.companybrain.app/v1/query \
  -H "Authorization: Bearer cb_live_..." \
  -H "Content-Type: application/json" \
  -d '{"message": "What is our refund policy?"}'
```

Show the full API key in a modal immediately after generation with a "⚠ Copy now — you won't see this again" warning.

---

### #31 — Slack Bot Integration (Streaming)

**Setup:** Create a Slack app at api.slack.com/apps. Enable Socket Mode (for streaming), add `chat:write` and `commands` scopes.

Install:
```bash
pip install slack-bolt
```

**Database:**
```sql
CREATE TABLE slack_integrations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  slack_team_id VARCHAR(255) NOT NULL UNIQUE,
  slack_team_name VARCHAR(255),
  bot_token TEXT NOT NULL,
  installed_by UUID REFERENCES users(id),
  installed_at TIMESTAMP NOT NULL DEFAULT NOW()
);
```

**Slack OAuth flow — `app/api/routes/integrations/slack.py`:**
```python
@router.get("/integrations/slack/connect")
async def slack_connect(current_user: User = Depends(get_current_user)):
    state = encode_jwt({"user_id": current_user.id, "org_id": current_user.org_id})
    auth_url = (
        f"https://slack.com/oauth/v2/authorize"
        f"?client_id={settings.SLACK_CLIENT_ID}"
        f"&scope=chat:write,commands"
        f"&redirect_uri={settings.SLACK_REDIRECT_URI}"
        f"&state={state}"
    )
    return { "auth_url": auth_url }

@router.get("/integrations/slack/callback")
async def slack_callback(code: str, state: str, ...):
    # Exchange code for bot_token via Slack OAuth API
    response = await exchange_slack_code(code)
    db.add(SlackIntegration(
        org_id=org_id,
        slack_team_id=response['team']['id'],
        slack_team_name=response['team']['name'],
        bot_token=response['access_token']
    ))
    await db.commit()
    return RedirectResponse("/settings/integrations?connected=slack")
```

**Slash command handler — `/brain`:**

Slack requires a response within 3 seconds. Use the deferred response pattern:
```python
from slack_bolt.async_app import AsyncApp

slack_app = AsyncApp(token=settings.SLACK_BOT_TOKEN, signing_secret=settings.SLACK_SIGNING_SECRET)

@slack_app.command("/brain")
async def handle_brain_command(ack, body, client):
    await ack()  # Acknowledge within 3 seconds

    query = body['text']
    channel_id = body['channel_id']
    team_id = body['team_id']

    # Find org by team_id
    integration = await get_slack_integration_by_team(team_id)
    if not integration:
        await client.chat_postMessage(channel=channel_id, text="Company Brain is not connected.")
        return

    # Post "thinking" placeholder message
    placeholder = await client.chat_postMessage(
        channel=channel_id,
        text="🧠 Thinking...",
        blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": "🧠 _Thinking..._"}}]
    )

    # Stream response via execute_task, update Slack message as chunks arrive
    full_response = ""
    sources = []
    async for chunk in execute_task_stream(query=query, org_id=integration.org_id):
        if chunk.type == 'text':
            full_response += chunk.content
            # Update message every ~200 chars to simulate streaming
            if len(full_response) % 200 < 20:
                await client.chat_update(
                    channel=channel_id,
                    ts=placeholder['ts'],
                    text=full_response,
                    blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": full_response + " ▌"}}]
                )
        elif chunk.type == 'sources':
            sources = chunk.sources

    # Final update with full response + citations block
    citation_text = " | ".join(f"📄 {s.name}" for s in sources[:3])
    await client.chat_update(
        channel=channel_id,
        ts=placeholder['ts'],
        text=full_response,
        blocks=[
            {"type": "section", "text": {"type": "mrkdwn", "text": full_response}},
            {"type": "divider"},
            {"type": "context", "elements": [
                {"type": "mrkdwn", "text": f"*Sources:* {citation_text}"}
            ]}
        ]
    )
```

**Mount Slack handler in FastAPI:**
```python
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler
slack_handler = AsyncSlackRequestHandler(slack_app)

@app.post("/slack/events")
async def slack_events(req: Request):
    return await slack_handler.handle(req)
```

**Frontend — `app/settings/integrations/page.tsx`**

Slack integration card:
```tsx
<div className="border rounded-lg p-4">
  <div className="flex items-center gap-3 mb-3">
    <SlackIcon />
    <div>
      <p className="font-medium">Slack</p>
      <p className="text-xs text-muted-foreground">Use /brain from any Slack channel</p>
    </div>
    {connected ? (
      <Badge>Connected to {workspace.name}</Badge>
    ) : (
      <Button size="sm" onClick={connectSlack}>Add to Slack</Button>
    )}
  </div>
  {connected && (
    <div className="bg-muted rounded p-2 text-xs font-mono">
      /brain What is our refund policy?
    </div>
  )}
</div>
```
