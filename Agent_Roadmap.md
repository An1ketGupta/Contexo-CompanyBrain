# Company Brain — 15-Day Agentic Implementation Roadmap
> Generated June 2026. Based on full codebase analysis + confirmed architectural decisions.

---

## CONFIRMED ARCHITECTURAL DECISIONS

| Decision | Choice |
|---|---|
| Google re-auth (Gmail Send) | Banner on first Gmail Send attempt — contextual, non-disruptive |
| Database migrations | Supabase migrations folder (`supabase/migrations/`) |
| Notion page output | Clean text dump — fast build, good enough for V1 |
| Confidence thresholds | Org-admin configurable — no hardcoded defaults |
| Slack interactivity | Needs to be enabled — Day 1 prerequisite |
| Frontend component library | Shadcn/UI |
| Agent orchestration | Inngest multi-step functions only — no LangGraph/CrewAI |
| Public API auth | API key auth (existing mechanism in `public_api.py`) |
| 15-day scope | Phase 1 (execution gap) + Phase 2 (autoflows) + Phase 3 start (enterprise) |
| Job infrastructure | Inngest only — no BullMQ/Celery |
| Notification email | Resend — sole email delivery provider |
| Out of scope | Zapier connector, Greenhouse/Lever API, LinkedIn, No-Code Builder |

---

## PHASE 1 — CLOSE THE EXECUTION GAP (Days 1–7)

---

### DAY 1 — Foundation: Gmail OAuth Extension + Slack Interactivity Setup

**Goal:** Lay the infrastructure groundwork that every execution feature depends on. Nothing user-visible ships today, but without this day nothing else works.

---

#### PART A — Gmail OAuth Scope Extension

**File: `apps/api/app/services/integrations/google_oauth.py`**

Locate your existing Google OAuth flow used for Drive. The current scope array looks something like:
```python
SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "openid",
    "email",
    "profile"
]
```

Add `gmail.send` to the scopes array:
```python
SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "openid",
    "email",
    "profile"
]
```

**Important:** This scope change only takes effect on NEW OAuth connections. Existing users who already connected Google will NOT have this scope — they need to re-authorize. This is handled via the re-auth banner (Day 2). Do not force re-auth here.

**File: `apps/api/app/services/integrations/gmail.py`** (NEW FILE)

Create this file mirroring the structure of `drive.py`. This is the Gmail adapter:

```python
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
import base64
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


class GmailAdapter:
    def __init__(self, credentials: Credentials):
        self.service = build("gmail", "v1", credentials=credentials)

    def send_email(
        self,
        to: str,
        subject: str,
        body: str,
        cc: str = None,
        reply_to: str = None
    ) -> dict:
        """
        Send an email via Gmail API.
        Returns: { message_id, thread_id, sent_at }
        """
        message = MIMEMultipart("alternative")
        message["to"] = to
        message["subject"] = subject
        if cc:
            message["cc"] = cc
        if reply_to:
            message["reply-to"] = reply_to

        # Plain text part
        text_part = MIMEText(body, "plain")
        message.attach(text_part)

        # HTML part (convert markdown line breaks to <br>)
        html_body = body.replace("\n", "<br>")
        html_part = MIMEText(f"<html><body>{html_body}</body></html>", "html")
        message.attach(html_part)

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        result = self.service.users().messages().send(
            userId="me",
            body={"raw": raw}
        ).execute()

        return {
            "message_id": result["id"],
            "thread_id": result.get("threadId"),
            "sent_at": None  # Gmail API doesn't return sent_at directly
        }

    def list_contacts(self, query: str, max_results: int = 10) -> list:
        """
        Search contacts via Gmail API for recipient autocomplete.
        """
        # Uses People API — see Day 2 for full implementation
        pass

    def check_send_scope(self, credentials: Credentials) -> bool:
        """
        Check if the current token has gmail.send scope.
        Returns False if user needs to re-authorize.
        """
        scopes = getattr(credentials, "scopes", []) or []
        return "https://www.googleapis.com/auth/gmail.send" in scopes
```

**File: `apps/api/app/db/migrations/`** — create migration file `20260601_001_gmail_integrations.sql`:

```sql
-- Gmail integration state per org
CREATE TABLE gmail_integrations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    email_address TEXT NOT NULL,
    has_send_scope BOOLEAN DEFAULT FALSE,
    token_data JSONB, -- encrypted refresh token storage
    connected_at TIMESTAMPTZ DEFAULT now(),
    last_used_at TIMESTAMPTZ,
    UNIQUE(org_id, user_id)
);

CREATE INDEX idx_gmail_integrations_org ON gmail_integrations(org_id);

-- Extend messages table with delivery tracking
ALTER TABLE messages 
ADD COLUMN IF NOT EXISTS delivery_status JSONB DEFAULT NULL;
-- delivery_status shape: { channel: "gmail"|"slack"|"notion", status: "sent"|"pending"|"failed", delivered_at, recipient, metadata }
```

Run this in Supabase dashboard SQL editor and save the file to `supabase/migrations/`.

---

#### PART B — Slack Interactivity Setup

**In your Slack App Dashboard (api.slack.com/apps):**

1. Go to your app → **Interactivity & Shortcuts**
2. Toggle **Interactivity** ON
3. Set Request URL to: `https://your-api-domain.com/api/slack/interactions`
4. Save changes
5. Go to **OAuth & Permissions** → add these Bot Token Scopes if not already present:
   - `chat:write`
   - `chat:write.public`
   - `channels:read`
   - `groups:read` (for private channels)
   - `im:write` (for DMs)
   - `users:read` (for user lookup)

**File: `apps/api/app/routers/slack_router.py`**

Add the interactions endpoint (Slack sends POST to this when a Block Kit button is clicked):

```python
@router.post("/interactions")
async def handle_slack_interaction(request: Request):
    """
    Handles all Block Kit interactions: button clicks, dropdown selections.
    Slack sends payload as application/x-www-form-urlencoded with a 'payload' key.
    """
    form_data = await request.form()
    payload = json.loads(form_data.get("payload", "{}"))
    
    action_id = payload.get("actions", [{}])[0].get("action_id", "")
    
    # Route to appropriate handler based on action_id
    if action_id.startswith("send_email_"):
        return await handle_email_send_action(payload)
    elif action_id.startswith("post_slack_"):
        return await handle_slack_post_action(payload)
    elif action_id.startswith("approve_"):
        return await handle_approval_action(payload)
    
    return {"ok": True}
```

**File: `apps/api/app/services/slack/block_kit.py`** (NEW FILE)

```python
def build_email_draft_blocks(
    draft_text: str,
    message_id: str,
    confidence_score: float,
    confidence_label: str
) -> list:
    """
    Builds Slack Block Kit blocks for an email draft approval card.
    Used when /brain draft email is called from Slack.
    """
    confidence_color = "good" if confidence_score >= 8.0 else "warning" if confidence_score >= 5.0 else "danger"
    
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Email Draft Ready*\nConfidence: {confidence_score}/10"
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"```{draft_text[:2000]}```"  # Slack has 3000 char limit per block
            }
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Send Email"},
                    "style": "primary",
                    "action_id": f"send_email_{message_id}",
                    "value": message_id
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Edit First"},
                    "action_id": f"edit_draft_{message_id}",
                    "value": message_id
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Discard"},
                    "style": "danger",
                    "action_id": f"discard_draft_{message_id}",
                    "value": message_id
                }
            ]
        }
    ]
```

**End of Day 1 checklist:**
- [ ] `gmail.py` adapter created
- [ ] `gmail_integrations` table migration saved and run
- [ ] `delivery_status` column added to `messages`
- [ ] Slack interactivity URL configured in dashboard
- [ ] Slack scopes updated
- [ ] `slack_router.py` interactions endpoint added
- [ ] `block_kit.py` builder created

---

### DAY 2 — Gmail Send Agent: Backend + Re-auth Banner

**Goal:** Users can click "Send via Gmail" on any email draft. Gmail-connected users see a send confirmation. Users without `gmail.send` scope see a re-auth banner.

---

#### PART A — Gmail Send Endpoint

**File: `apps/api/app/routers/integrations/gmail_router.py`** (NEW FILE)

```python
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from app.services.integrations.gmail import GmailAdapter
from app.services.auth import get_current_user, get_org_credentials
from app.db.supabase import supabase_client
from app.inngest.client import inngest
import uuid
from datetime import datetime, timezone

router = APIRouter(prefix="/integrations/gmail", tags=["gmail"])


class SendEmailRequest(BaseModel):
    message_id: str          # The Company Brain message ID (for delivery_status tracking)
    to: str                  # Recipient email address
    subject: str             # Email subject
    body: str                # Email body (the AI-generated content)
    cc: str = None
    reply_to: str = None


class SendEmailResponse(BaseModel):
    success: bool
    gmail_message_id: str = None
    sent_at: str = None
    error: str = None


@router.post("/send", response_model=SendEmailResponse)
async def send_email(
    request: SendEmailRequest,
    current_user=Depends(get_current_user)
):
    """
    Send an email via Gmail on behalf of the authenticated user.
    Checks for gmail.send scope before attempting.
    """
    # 1. Get user's Google credentials from your existing credential store
    credentials = await get_org_credentials(current_user.org_id, current_user.id, "google")
    
    if not credentials:
        raise HTTPException(status_code=400, detail="Google account not connected")
    
    # 2. Check for gmail.send scope
    adapter = GmailAdapter(credentials)
    if not adapter.check_send_scope(credentials):
        raise HTTPException(
            status_code=403,
            detail="gmail_send_scope_missing"  # Frontend uses this code to show re-auth banner
        )
    
    # 3. Queue the send via Inngest (async, with retry)
    job_id = str(uuid.uuid4())
    await inngest.send({
        "name": "gmail/send-email",
        "data": {
            "job_id": job_id,
            "message_id": request.message_id,
            "org_id": current_user.org_id,
            "user_id": current_user.id,
            "to": request.to,
            "subject": request.subject,
            "body": request.body,
            "cc": request.cc,
            "reply_to": request.reply_to
        }
    })
    
    # 4. Optimistically mark as pending in delivery_status
    await supabase_client.table("messages").update({
        "delivery_status": {
            "channel": "gmail",
            "status": "pending",
            "recipient": request.to,
            "job_id": job_id,
            "queued_at": datetime.now(timezone.utc).isoformat()
        }
    }).eq("id", request.message_id).execute()
    
    return SendEmailResponse(success=True)


@router.get("/scope-check")
async def check_gmail_scope(current_user=Depends(get_current_user)):
    """
    Frontend calls this to determine whether to show re-auth banner.
    """
    credentials = await get_org_credentials(current_user.org_id, current_user.id, "google")
    if not credentials:
        return {"connected": False, "has_send_scope": False}
    
    adapter = GmailAdapter(credentials)
    has_scope = adapter.check_send_scope(credentials)
    return {"connected": True, "has_send_scope": has_scope}
```

**File: `apps/api/app/inngest/functions/gmail_send.py`** (NEW FILE)

```python
from app.inngest.client import inngest
from app.services.integrations.gmail import GmailAdapter
from app.services.auth import get_org_credentials_by_ids
from app.db.supabase import supabase_client
from datetime import datetime, timezone


@inngest.create_function(
    fn_id="gmail-send-email",
    trigger=inngest.TriggerEvent("gmail/send-email"),
    retries=3,
    retry_after_error=True
)
async def send_email_function(ctx, step):
    data = ctx.event["data"]

    # Step 1: Retrieve credentials
    credentials = await step.run(
        "retrieve-credentials",
        lambda: get_org_credentials_by_ids(data["org_id"], data["user_id"], "google")
    )

    # Step 2: Send the email
    result = await step.run(
        "send-via-gmail",
        lambda: GmailAdapter(credentials).send_email(
            to=data["to"],
            subject=data["subject"],
            body=data["body"],
            cc=data.get("cc"),
            reply_to=data.get("reply_to")
        )
    )

    # Step 3: Update delivery_status in messages table
    await step.run(
        "update-delivery-status",
        lambda: supabase_client.table("messages").update({
            "delivery_status": {
                "channel": "gmail",
                "status": "sent",
                "recipient": data["to"],
                "gmail_message_id": result["message_id"],
                "delivered_at": datetime.now(timezone.utc).isoformat()
            }
        }).eq("id", data["message_id"]).execute()
    )

    # Step 4: Update gmail_integrations.last_used_at
    await step.run(
        "update-last-used",
        lambda: supabase_client.table("gmail_integrations").update({
            "last_used_at": datetime.now(timezone.utc).isoformat()
        }).eq("org_id", data["org_id"]).eq("user_id", data["user_id"]).execute()
    )

    return {"sent": True, "gmail_message_id": result["message_id"]}
```

---

#### PART B — Frontend: Send Button + Re-auth Banner

**File: `apps/web/components/chat/MessageActions.tsx`**

Add a "Send via Gmail" button that appears on messages where intent === `task_generation` and the output looks like an email (heuristic: subject line detected, or user explicitly said "email"):

```tsx
import { useState, useEffect } from "react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Send, AlertCircle } from "lucide-react"
import { GmailSendDialog } from "./GmailSendDialog"

interface MessageActionsProps {
  messageId: string
  intent: string
  outputText: string
  confidenceScore: number
  deliveryStatus?: DeliveryStatus
}

export function MessageActions({ 
  messageId, intent, outputText, confidenceScore, deliveryStatus 
}: MessageActionsProps) {
  const [gmailScope, setGmailScope] = useState<{ connected: boolean; has_send_scope: boolean } | null>(null)
  const [showSendDialog, setShowSendDialog] = useState(false)
  const [showReauthBanner, setShowReauthBanner] = useState(false)

  // Only show for email-type task_generation outputs
  const isEmailOutput = intent === "task_generation" && detectEmailOutput(outputText)

  useEffect(() => {
    if (isEmailOutput) {
      fetch("/api/integrations/gmail/scope-check")
        .then(r => r.json())
        .then(setGmailScope)
    }
  }, [isEmailOutput])

  if (!isEmailOutput) return null

  // Already sent
  if (deliveryStatus?.status === "sent") {
    return (
      <div className="flex items-center gap-2 mt-2 text-sm text-muted-foreground">
        <Send className="h-3 w-3" />
        Sent to {deliveryStatus.recipient} · {formatTime(deliveryStatus.delivered_at)}
      </div>
    )
  }

  const handleSendClick = () => {
    if (!gmailScope?.connected) {
      // Show connect Google prompt
      window.location.href = "/settings/integrations"
      return
    }
    if (!gmailScope?.has_send_scope) {
      setShowReauthBanner(true)
      return
    }
    setShowSendDialog(true)
  }

  return (
    <div className="mt-3 space-y-2">
      {showReauthBanner && (
        <Alert className="border-amber-200 bg-amber-50">
          <AlertCircle className="h-4 w-4 text-amber-600" />
          <AlertDescription className="text-amber-800">
            To send emails, reconnect your Google account with send permission.{" "}
            <a href="/api/auth/google/reauthorize" className="underline font-medium">
              Reconnect Google
            </a>
          </AlertDescription>
        </Alert>
      )}

      <div className="flex items-center gap-2">
        <Button
          size="sm"
          variant="outline"
          onClick={handleSendClick}
          className="gap-2"
        >
          <Send className="h-3 w-3" />
          Send via Gmail
        </Button>
        
        {confidenceScore && (
          <ConfidenceBadge score={confidenceScore} />
        )}
      </div>

      {showSendDialog && (
        <GmailSendDialog
          messageId={messageId}
          defaultBody={outputText}
          onClose={() => setShowSendDialog(false)}
          onSent={() => {
            setShowSendDialog(false)
          }}
        />
      )}
    </div>
  )
}
```

**File: `apps/web/components/chat/GmailSendDialog.tsx`** (NEW FILE)

A Shadcn Dialog that lets the user fill in To, Subject, and optionally CC before sending:

```tsx
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { useState } from "react"
import { Loader2 } from "lucide-react"

export function GmailSendDialog({ messageId, defaultBody, onClose, onSent }) {
  const [to, setTo] = useState("")
  const [subject, setSubject] = useState(extractSubject(defaultBody))
  const [body, setBody] = useState(stripSubjectLine(defaultBody))
  const [cc, setCc] = useState("")
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState("")

  const handleSend = async () => {
    if (!to || !subject) {
      setError("Recipient and subject are required")
      return
    }
    setLoading(true)
    setError("")
    
    try {
      const res = await fetch("/api/integrations/gmail/send", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message_id: messageId, to, subject, body, cc: cc || null })
      })
      
      if (!res.ok) throw new Error(await res.text())
      onSent()
    } catch (e) {
      setError("Failed to send. Please try again.")
    } finally {
      setLoading(false)
    }
  }

  return (
    <Dialog open onOpenChange={onClose}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Send Email via Gmail</DialogTitle>
        </DialogHeader>
        
        <div className="space-y-4">
          <div className="space-y-1">
            <Label>To</Label>
            <Input placeholder="recipient@company.com" value={to} onChange={e => setTo(e.target.value)} />
          </div>
          <div className="space-y-1">
            <Label>CC (optional)</Label>
            <Input placeholder="cc@company.com" value={cc} onChange={e => setCc(e.target.value)} />
          </div>
          <div className="space-y-1">
            <Label>Subject</Label>
            <Input value={subject} onChange={e => setSubject(e.target.value)} />
          </div>
          <div className="space-y-1">
            <Label>Body</Label>
            <Textarea rows={10} value={body} onChange={e => setBody(e.target.value)} />
          </div>
          {error && <p className="text-sm text-destructive">{error}</p>}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button onClick={handleSend} disabled={loading}>
            {loading ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : null}
            Send
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
```

**File: `apps/web/components/chat/ConfidenceBadge.tsx`** (NEW FILE)

```tsx
import { Badge } from "@/components/ui/badge"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { ShieldCheck, ShieldAlert, ShieldX } from "lucide-react"

export function ConfidenceBadge({ score }: { score: number }) {
  const orgThresholds = useOrgConfig() // hook to fetch org_config thresholds
  
  const high = orgThresholds?.min_confidence_high ?? 8.0
  const medium = orgThresholds?.min_confidence_medium ?? 5.0
  
  const isHigh = score >= high
  const isMedium = score >= medium && score < high
  const isLow = score < medium

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Badge variant={isHigh ? "default" : isMedium ? "secondary" : "destructive"} className="gap-1 cursor-help">
          {isHigh ? <ShieldCheck className="h-3 w-3" /> : isLow ? <ShieldX className="h-3 w-3" /> : <ShieldAlert className="h-3 w-3" />}
          {score.toFixed(1)}/10
        </Badge>
      </TooltipTrigger>
      <TooltipContent>
        {isHigh ? "High confidence — safe to send" : isMedium ? "Review before sending" : "Low confidence — add more documents first"}
      </TooltipContent>
    </Tooltip>
  )
}
```

**End of Day 2 checklist:**
- [ ] `gmail_router.py` created with `/send` and `/scope-check` endpoints
- [ ] Router registered in `main.py` / `app.py`
- [ ] `gmail_send.py` Inngest function created and registered
- [ ] `MessageActions.tsx` updated with Send button
- [ ] `GmailSendDialog.tsx` created
- [ ] `ConfidenceBadge.tsx` created
- [ ] Re-auth banner implemented and tested manually

---

### DAY 3 — Slack Post Agent + Org Config for Confidence Thresholds

**Goal:** Users can post AI-generated content directly to any Slack channel. Org admins can configure confidence thresholds from the settings page.

---

#### PART A — Slack Post Endpoint

**File: `apps/api/app/routers/integrations/slack_post_router.py`** (NEW FILE)

```python
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from app.services.integrations.slack import SlackAdapter
from app.db.supabase import supabase_client
from app.services.auth import get_current_user
from app.inngest.client import inngest

router = APIRouter(prefix="/integrations/slack", tags=["slack"])


class PostSlackRequest(BaseModel):
    message_id: str
    channel_id: str
    channel_name: str
    text: str
    thread_ts: str = None  # Optional: reply in thread


@router.post("/post")
async def post_to_slack(req: PostSlackRequest, current_user=Depends(get_current_user)):
    await inngest.send({
        "name": "slack/post-message",
        "data": {
            "org_id": current_user.org_id,
            "message_id": req.message_id,
            "channel_id": req.channel_id,
            "channel_name": req.channel_name,
            "text": req.text,
            "thread_ts": req.thread_ts
        }
    })
    return {"queued": True}


@router.get("/channels")
async def list_channels(current_user=Depends(get_current_user)):
    """
    Returns list of Slack channels for the channel picker dropdown.
    Fetches from Slack API and caches in org config for 1 hour.
    """
    adapter = SlackAdapter(org_id=current_user.org_id)
    channels = await adapter.list_channels()
    return {"channels": channels}
```

**File: `apps/api/app/services/integrations/slack.py`**

Extend your existing `slack.py` with `post_message` and `list_channels`:

```python
async def post_message(self, channel_id: str, text: str, thread_ts: str = None) -> dict:
    """Post a message to a Slack channel."""
    payload = {
        "channel": channel_id,
        "text": text,
        "mrkdwn": True
    }
    if thread_ts:
        payload["thread_ts"] = thread_ts
    
    response = await self.client.chat_postMessage(**payload)
    return {
        "ts": response["ts"],
        "channel": response["channel"],
        "message": response["message"]
    }

async def list_channels(self) -> list:
    """Returns all public + private channels the bot has access to."""
    response = await self.client.conversations_list(
        types="public_channel,private_channel",
        limit=200,
        exclude_archived=True
    )
    return [
        {"id": c["id"], "name": c["name"], "is_private": c["is_private"]}
        for c in response["channels"]
    ]
```

**File: `apps/api/app/inngest/functions/slack_post.py`** (NEW FILE)

```python
@inngest.create_function(
    fn_id="slack-post-message",
    trigger=inngest.TriggerEvent("slack/post-message"),
    retries=3
)
async def slack_post_function(ctx, step):
    data = ctx.event["data"]
    
    result = await step.run(
        "post-to-channel",
        lambda: SlackAdapter(org_id=data["org_id"]).post_message(
            channel_id=data["channel_id"],
            text=data["text"],
            thread_ts=data.get("thread_ts")
        )
    )
    
    await step.run(
        "update-delivery-status",
        lambda: supabase_client.table("messages").update({
            "delivery_status": {
                "channel": "slack",
                "status": "sent",
                "channel_name": data["channel_name"],
                "slack_ts": result["ts"],
                "delivered_at": datetime.now(timezone.utc).isoformat()
            }
        }).eq("id", data["message_id"]).execute()
    )
```

---

#### PART B — Org Config: Confidence Thresholds

**Migration file: `20260601_002_org_config_confidence.sql`**

```sql
ALTER TABLE org_config 
ADD COLUMN IF NOT EXISTS min_confidence_high FLOAT DEFAULT NULL,
ADD COLUMN IF NOT EXISTS min_confidence_medium FLOAT DEFAULT NULL,
ADD COLUMN IF NOT EXISTS min_confidence_block FLOAT DEFAULT NULL;
-- NULL means org admin hasn't configured thresholds yet — UI shows "Not configured"
```

**File: `apps/api/app/routers/admin/config_router.py`**

Add endpoint to save confidence thresholds:

```python
class ConfidenceThresholdsRequest(BaseModel):
    min_confidence_high: float    # e.g. 8.0 — show green, safe to auto-execute
    min_confidence_medium: float  # e.g. 5.0 — show amber, warn before executing
    min_confidence_block: float   # e.g. 3.0 — show red, disable execution

@router.put("/config/confidence-thresholds")
async def update_confidence_thresholds(
    req: ConfidenceThresholdsRequest,
    current_user=Depends(get_current_admin_user)
):
    await supabase_client.table("org_config").update({
        "min_confidence_high": req.min_confidence_high,
        "min_confidence_medium": req.min_confidence_medium,
        "min_confidence_block": req.min_confidence_block
    }).eq("org_id", current_user.org_id).execute()
    return {"updated": True}
```

**Frontend: `apps/web/app/settings/confidence/page.tsx`** (NEW PAGE)

A simple Shadcn settings page with three sliders (0–10) for high/medium/block thresholds. Saves on blur. Shows a live preview of what the badge looks like at each threshold.

**Frontend: Slack Channel Picker in MessageActions**

Add a "Post to Slack" button alongside Gmail Send. Clicking it opens a Shadcn Popover with a searchable channel list (fetched from `/integrations/slack/channels`). Select channel → confirm → posts.

**End of Day 3 checklist:**
- [ ] `slack_post_router.py` created and registered
- [ ] `post_message` and `list_channels` added to `slack.py`
- [ ] `slack_post.py` Inngest function created and registered
- [ ] `org_config` migration for confidence thresholds run
- [ ] Confidence threshold settings page created
- [ ] Slack channel picker added to `MessageActions.tsx`
- [ ] "Post to Slack" delivery status shown after posting

---

### DAY 4 — Notion Page Creator + Google Docs Export

**Goal:** Users can push AI-generated content to Notion or Google Docs with one click.

---

#### PART A — Notion Write

**File: `apps/api/app/services/integrations/notion.py`**

Extend existing `notion.py` (read-only) with `create_page`:

```python
async def create_page(
    self,
    parent_page_id: str,
    title: str,
    content: str,
    org_id: str
) -> dict:
    """
    Creates a Notion page with clean text dump.
    Content is split by newlines into paragraph blocks.
    """
    # Convert plain text to Notion paragraph blocks
    blocks = self._text_to_blocks(content)
    
    payload = {
        "parent": {"page_id": parent_page_id},
        "properties": {
            "title": {
                "title": [{"type": "text", "text": {"content": title}}]
            }
        },
        "children": blocks
    }
    
    response = await self.client.post("/pages", json=payload)
    return {
        "page_id": response["id"],
        "url": response["url"],
        "title": title
    }

def _text_to_blocks(self, text: str) -> list:
    """
    Converts plain text to Notion paragraph blocks.
    Clean text dump — each paragraph becomes one block.
    Max 2000 chars per block (Notion limit).
    """
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    blocks = []
    
    for para in paragraphs:
        # Split oversized paragraphs
        chunks = [para[i:i+2000] for i in range(0, len(para), 2000)]
        for chunk in chunks:
            blocks.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": chunk}}]
                }
            })
    
    return blocks

async def list_pages(self, org_id: str) -> list:
    """
    Returns accessible pages for the parent page picker.
    """
    response = await self.client.post("/search", json={
        "filter": {"property": "object", "value": "page"},
        "page_size": 50
    })
    return [
        {"id": p["id"], "title": self._get_page_title(p)}
        for p in response.get("results", [])
    ]
```

**File: `apps/api/app/routers/integrations/notion_router.py`** (NEW FILE)

```python
@router.post("/notion/create-page")
async def create_notion_page(req: CreateNotionPageRequest, current_user=Depends(get_current_user)):
    await inngest.send({
        "name": "notion/create-page",
        "data": {
            "org_id": current_user.org_id,
            "message_id": req.message_id,
            "parent_page_id": req.parent_page_id,
            "title": req.title,
            "content": req.content
        }
    })
    return {"queued": True}

@router.get("/notion/pages")
async def list_notion_pages(current_user=Depends(get_current_user)):
    adapter = NotionAdapter(org_id=current_user.org_id)
    pages = await adapter.list_pages(current_user.org_id)
    return {"pages": pages}
```

---

#### PART B — Google Docs Export

**File: `apps/api/app/services/integrations/drive.py`**

Extend existing `drive.py` with `create_document`:

```python
async def create_document(
    self,
    title: str,
    content: str,
    org_id: str,
    share_with_email: str = None
) -> dict:
    """
    Creates a Google Doc with the given content.
    Uses Docs API to insert content after creation.
    """
    from googleapiclient.discovery import build
    
    docs_service = build("docs", "v1", credentials=self.credentials)
    drive_service = build("drive", "v3", credentials=self.credentials)
    
    # Create empty doc
    doc = docs_service.documents().create(body={"title": title}).execute()
    doc_id = doc["documentId"]
    
    # Insert content via batchUpdate
    requests = [{
        "insertText": {
            "location": {"index": 1},
            "text": content
        }
    }]
    docs_service.documents().batchUpdate(
        documentId=doc_id,
        body={"requests": requests}
    ).execute()
    
    # Optionally share with user
    if share_with_email:
        drive_service.permissions().create(
            fileId=doc_id,
            body={"type": "user", "role": "writer", "emailAddress": share_with_email}
        ).execute()
    
    return {
        "doc_id": doc_id,
        "url": f"https://docs.google.com/document/d/{doc_id}/edit",
        "title": title
    }
```

**Frontend: Export Buttons in MessageActions**

Add "Create in Notion" and "Export to Google Doc" buttons. Each opens a simple dialog:
- Notion: page title input + parent page picker (searchable dropdown from `/integrations/notion/pages`)
- Google Docs: just title input + "Create & Share with me" checkbox

**End of Day 4 checklist:**
- [ ] `create_page` and `_text_to_blocks` added to `notion.py`
- [ ] `notion_router.py` created with create-page and list-pages endpoints
- [ ] Notion Inngest function created
- [ ] `create_document` added to `drive.py`
- [ ] Google Docs endpoint added to drive router
- [ ] Notion page picker dialog built (Shadcn)
- [ ] Google Docs export dialog built (Shadcn)
- [ ] Both delivery statuses shown in message bubble after creation

---

### DAY 5 — Knowledge Gap Alert System

**Goal:** Every zero-result search is captured. Admins get notified when the same topic hits 3+ gaps. AI auto-drafts a stub document for admin review.

---

#### PART A — Capture the Signal

**Migration: `20260601_003_knowledge_gaps.sql`**

```sql
CREATE TABLE knowledge_gaps (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    topic TEXT NOT NULL,           -- normalized topic extracted from the query
    query TEXT NOT NULL,           -- original user query
    user_id UUID REFERENCES users(id),
    conversation_id UUID REFERENCES conversations(id),
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_knowledge_gaps_org ON knowledge_gaps(org_id);
CREATE INDEX idx_knowledge_gaps_topic ON knowledge_gaps(org_id, topic);
CREATE INDEX idx_knowledge_gaps_created ON knowledge_gaps(created_at);
```

**File: `apps/api/app/services/llm/task_chain.py`**

Find the `KnowledgeGapEvent` emission (around line 458-462). Currently it just emits to the SSE stream. Add persistence:

```python
# Existing code (approximately):
if all_search_results_empty:
    yield KnowledgeGapEvent(topic=extracted_topic)
    # NEW: persist to database via Inngest
    await inngest_client.send({
        "name": "knowledge/gap-detected",
        "data": {
            "org_id": org_id,
            "topic": extracted_topic,
            "query": user_query,
            "user_id": user_id,
            "conversation_id": conversation_id
        }
    })
```

**File: `apps/api/app/inngest/functions/knowledge_gap.py`** (NEW FILE)

```python
@inngest.create_function(
    fn_id="knowledge-gap-detected",
    trigger=inngest.TriggerEvent("knowledge/gap-detected"),
    retries=2
)
async def knowledge_gap_function(ctx, step):
    data = ctx.event["data"]
    
    # Step 1: Persist the gap
    await step.run("persist-gap", lambda: supabase_client.table("knowledge_gaps").insert({
        "org_id": data["org_id"],
        "topic": data["topic"],
        "query": data["query"],
        "user_id": data.get("user_id"),
        "conversation_id": data.get("conversation_id")
    }).execute())
    
    # Step 2: Count gaps for this topic in the last 7 days
    count_result = await step.run("count-topic-gaps", lambda: 
        supabase_client.table("knowledge_gaps")
        .select("id", count="exact")
        .eq("org_id", data["org_id"])
        .eq("topic", data["topic"])
        .gte("created_at", (datetime.now() - timedelta(days=7)).isoformat())
        .execute()
    )
    
    gap_count = count_result.count
    
    # Step 3: If threshold hit (3+), trigger admin notification + auto-draft
    if gap_count >= 3:
        await step.run("trigger-admin-alert", lambda: inngest_client.send({
            "name": "knowledge/gap-threshold-hit",
            "data": {
                "org_id": data["org_id"],
                "topic": data["topic"],
                "count": gap_count
            }
        }))


@inngest.create_function(
    fn_id="knowledge-gap-threshold",
    trigger=inngest.TriggerEvent("knowledge/gap-threshold-hit"),
    retries=2,
    # Debounce: only fire once per topic per 24 hours
    debounce=inngest.Debounce(period="24h", key="data.org_id + '-' + data.topic")
)
async def knowledge_gap_threshold_function(ctx, step):
    data = ctx.event["data"]
    
    # Step 1: Check if we already alerted about this topic this week
    existing = await step.run("check-existing-alert", lambda:
        supabase_client.table("knowledge_gaps")
        .select("id")
        .eq("org_id", data["org_id"])
        .eq("topic", data["topic"])
        .execute()
    )
    
    # Step 2: Auto-draft stub document using LLM
    stub_content = await step.run("draft-stub-document", lambda:
        generate_stub_document(
            topic=data["topic"],
            org_id=data["org_id"],
            occurrence_count=data["count"]
        )
    )
    
    # Step 3: Save stub to a drafts table (not ingested yet — needs admin approval)
    await step.run("save-draft", lambda:
        supabase_client.table("document_drafts").insert({
            "org_id": data["org_id"],
            "title": f"[STUB] {data['topic']}",
            "content": stub_content,
            "source": "knowledge_gap_autoflow",
            "gap_topic": data["topic"],
            "gap_count": data["count"],
            "status": "pending_review"
        }).execute()
    )
    
    # Step 4: Send admin notification email via Resend
    await step.run("notify-admin", lambda:
        send_admin_gap_alert(
            org_id=data["org_id"],
            topic=data["topic"],
            count=data["count"],
            stub_preview=stub_content[:500]
        )
    )
```

**Migration: `20260601_004_document_drafts.sql`**

```sql
CREATE TABLE document_drafts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    source TEXT NOT NULL,  -- 'knowledge_gap_autoflow' | 'admin_manual' | 'version_diff'
    gap_topic TEXT,
    gap_count INT,
    status TEXT DEFAULT 'pending_review',  -- pending_review | approved | rejected
    reviewed_by UUID REFERENCES users(id),
    reviewed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now()
);
```

**Frontend: Admin Knowledge Gaps Panel**

New section in admin dashboard: `/admin/knowledge-gaps`

Shows table of: Topic | Times Asked | Last Asked | AI Draft Available | Actions (Review Draft / Dismiss).

Clicking "Review Draft" opens a dialog with the stub content, with "Approve & Upload" and "Edit then Upload" buttons.

**End of Day 5 checklist:**
- [ ] `knowledge_gaps` table migration run
- [ ] `document_drafts` table migration run
- [ ] `task_chain.py` updated to fire `knowledge/gap-detected` event
- [ ] `knowledge_gap.py` Inngest functions created (both)
- [ ] `generate_stub_document()` function written (calls `execute_task` internally)
- [ ] Admin gap alert email template created in Resend
- [ ] Admin knowledge gaps panel page created
- [ ] Draft review dialog with approve/reject actions built

---

### DAY 6 — Human-in-the-Loop Approval Workflow

**Goal:** Users can submit any AI output for manager approval before execution. Approvers get notified and can approve/reject from Company Brain or directly from their email.

---

**Migration: `20260601_005_approvals.sql`**

```sql
CREATE TABLE approvals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    message_id UUID NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    requested_by UUID NOT NULL REFERENCES users(id),
    approver_id UUID NOT NULL REFERENCES users(id),
    status TEXT DEFAULT 'pending',  -- pending | approved | rejected
    note TEXT,
    execution_action JSONB,  -- what to execute after approval: { channel, params }
    created_at TIMESTAMPTZ DEFAULT now(),
    resolved_at TIMESTAMPTZ,
    reminder_sent_at TIMESTAMPTZ
);

CREATE INDEX idx_approvals_approver ON approvals(approver_id, status);
CREATE INDEX idx_approvals_org ON approvals(org_id, status);
```

**File: `apps/api/app/routers/approvals.py`** (NEW FILE)

```python
@router.post("/approvals")
async def request_approval(req: ApprovalRequest, current_user=Depends(get_current_user)):
    approval_id = str(uuid.uuid4())
    
    # Create approval record
    await supabase_client.table("approvals").insert({
        "id": approval_id,
        "org_id": current_user.org_id,
        "message_id": req.message_id,
        "requested_by": current_user.id,
        "approver_id": req.approver_id,
        "execution_action": req.execution_action,  # e.g. { channel: "gmail", to: "...", subject: "..." }
        "status": "pending"
    }).execute()
    
    # Trigger notification
    await inngest.send({
        "name": "approval/requested",
        "data": {
            "approval_id": approval_id,
            "org_id": current_user.org_id,
            "requester_name": current_user.name,
            "approver_id": req.approver_id,
            "message_id": req.message_id,
            "preview": req.preview_text[:300]
        }
    })
    
    return {"approval_id": approval_id}


@router.post("/approvals/{approval_id}/resolve")
async def resolve_approval(
    approval_id: str,
    req: ResolveApprovalRequest,  # { action: "approved"|"rejected", note: str }
    current_user=Depends(get_current_user)
):
    approval = await get_approval(approval_id)
    
    if approval["approver_id"] != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to resolve this approval")
    
    await supabase_client.table("approvals").update({
        "status": req.action,
        "note": req.note,
        "resolved_at": datetime.now(timezone.utc).isoformat()
    }).eq("id", approval_id).execute()
    
    if req.action == "approved":
        # Execute the queued action
        await inngest.send({
            "name": "approval/execute-action",
            "data": {"approval": approval, "execution_action": approval["execution_action"]}
        })
    
    # Notify requester
    await inngest.send({
        "name": "approval/resolved",
        "data": {"approval_id": approval_id, "action": req.action, "note": req.note}
    })
    
    return {"resolved": True}
```

**Inngest: Approval notification + 24h reminder**

```python
@inngest.create_function(
    fn_id="approval-requested",
    trigger=inngest.TriggerEvent("approval/requested")
)
async def approval_requested_function(ctx, step):
    data = ctx.event["data"]
    
    # Send email to approver
    await step.run("notify-approver", lambda: send_approval_request_email(data))
    
    # Also send Slack DM if Slack connected
    await step.run("slack-notify", lambda: send_approval_slack_dm(data))
    
    # Schedule 24h reminder if not resolved
    await step.sleep("wait-24h", "24h")
    
    approval = await supabase_client.table("approvals").select("*").eq("id", data["approval_id"]).single().execute()
    
    if approval.data["status"] == "pending":
        await send_approval_reminder_email(data)
        await supabase_client.table("approvals").update({
            "reminder_sent_at": datetime.now(timezone.utc).isoformat()
        }).eq("id", data["approval_id"]).execute()
```

**Frontend: Approval UI**

- "Submit for Approval" button added next to "Send via Gmail" in MessageActions (only shown on Business plan orgs)
- Clicking opens a dialog: select approver from org members dropdown + optional note
- Approver sees pending approvals badge on their dashboard nav
- `/approvals` page shows pending items with approve/reject buttons and the full AI output + sources

**End of Day 6 checklist:**
- [ ] `approvals` migration run
- [ ] `approvals.py` router created (request + resolve endpoints)
- [ ] Approval notification Inngest function with 24h reminder
- [ ] Execute-on-approval Inngest handler
- [ ] "Submit for Approval" button in MessageActions (Business plan gate)
- [ ] Approvals dashboard page
- [ ] Approval request + resolution email templates in Resend
- [ ] Slack DM notification for approvals

---

### DAY 7 — Agent Run Infrastructure + Audit Trail

**Goal:** Build the foundational `agent_runs` table and logging infrastructure that every autonomous agent in Phase 2 depends on. Ship the audit trail admin view.

---

**Migration: `20260601_006_agent_runs.sql`**

```sql
CREATE TABLE agent_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    agent_type TEXT NOT NULL,  -- 'onboarding' | 'policy_propagation' | 'support_response' | 'knowledge_gap' | 'weekly_digest' | 'recruiting'
    triggered_by TEXT NOT NULL,  -- 'user' | 'webhook' | 'cron' | 'api'
    triggered_by_user_id UUID REFERENCES users(id),
    status TEXT DEFAULT 'running',  -- running | completed | failed | cancelled | pending_approval
    input JSONB NOT NULL DEFAULT '{}',
    steps JSONB DEFAULT '[]',  -- [{step_name, status, result, started_at, completed_at, error}]
    output JSONB DEFAULT '{}',
    error TEXT,
    llm_tokens_used INT DEFAULT 0,
    confidence_scores JSONB DEFAULT '[]',
    created_at TIMESTAMPTZ DEFAULT now(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ
);

CREATE INDEX idx_agent_runs_org ON agent_runs(org_id, created_at DESC);
CREATE INDEX idx_agent_runs_type ON agent_runs(org_id, agent_type);
CREATE INDEX idx_agent_runs_status ON agent_runs(org_id, status);
```

**File: `apps/api/app/services/agents/base_agent.py`** (NEW FILE)

This is the base class all agents inherit from. Handles step logging, status updates, and error handling:

```python
import uuid
from datetime import datetime, timezone
from app.db.supabase import supabase_client
from app.inngest.client import inngest


class BaseAgent:
    def __init__(self, org_id: str, agent_type: str, input_data: dict, triggered_by: str = "user", triggered_by_user_id: str = None):
        self.org_id = org_id
        self.agent_type = agent_type
        self.input_data = input_data
        self.triggered_by = triggered_by
        self.triggered_by_user_id = triggered_by_user_id
        self.run_id = str(uuid.uuid4())
        self.steps = []
        self.tokens_used = 0
        self.confidence_scores = []
    
    async def create_run_record(self) -> str:
        await supabase_client.table("agent_runs").insert({
            "id": self.run_id,
            "org_id": self.org_id,
            "agent_type": self.agent_type,
            "triggered_by": self.triggered_by,
            "triggered_by_user_id": self.triggered_by_user_id,
            "status": "running",
            "input": self.input_data,
            "started_at": datetime.now(timezone.utc).isoformat()
        }).execute()
        return self.run_id
    
    async def log_step(self, step_name: str, status: str, result: dict = None, error: str = None):
        step = {
            "step_name": step_name,
            "status": status,  # started | completed | failed
            "result": result,
            "error": error,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        self.steps.append(step)
        
        # Update steps in DB
        await supabase_client.table("agent_runs").update({
            "steps": self.steps
        }).eq("id", self.run_id).execute()
    
    async def complete(self, output: dict):
        await supabase_client.table("agent_runs").update({
            "status": "completed",
            "output": output,
            "steps": self.steps,
            "llm_tokens_used": self.tokens_used,
            "confidence_scores": self.confidence_scores,
            "completed_at": datetime.now(timezone.utc).isoformat()
        }).eq("id", self.run_id).execute()
    
    async def fail(self, error: str):
        await supabase_client.table("agent_runs").update({
            "status": "failed",
            "error": error,
            "steps": self.steps,
            "completed_at": datetime.now(timezone.utc).isoformat()
        }).eq("id", self.run_id).execute()
```

**File: `apps/api/app/routers/admin/agent_runs_router.py`** (NEW FILE)

```python
@router.get("/agent-runs")
async def list_agent_runs(
    agent_type: str = None,
    status: str = None,
    limit: int = 50,
    offset: int = 0,
    current_user=Depends(get_current_admin_user)
):
    query = supabase_client.table("agent_runs").select("*").eq("org_id", current_user.org_id)
    if agent_type:
        query = query.eq("agent_type", agent_type)
    if status:
        query = query.eq("status", status)
    result = await query.order("created_at", desc=True).range(offset, offset + limit - 1).execute()
    return {"runs": result.data, "total": len(result.data)}


@router.get("/agent-runs/{run_id}")
async def get_agent_run(run_id: str, current_user=Depends(get_current_admin_user)):
    result = await supabase_client.table("agent_runs").select("*").eq("id", run_id).eq("org_id", current_user.org_id).single().execute()
    return result.data
```

**Frontend: `/admin/agent-runs` page**

Table view of all agent runs with columns: Agent Type, Triggered By, Status (badge), Steps (count), Tokens Used, Started At, Duration. Clicking a row expands the step-by-step execution log with timestamps and outputs for each step. This is the full audit trail.

**End of Day 7 — Phase 1 Complete checklist:**
- [ ] `agent_runs` migration run
- [ ] `base_agent.py` created with full step logging
- [ ] `agent_runs_router.py` created
- [ ] Admin audit trail page at `/admin/agent-runs`
- [ ] Step detail expandable view in audit trail
- [ ] Verify all Day 1–7 features work end-to-end

---

## PHASE 2 — AUTOFLOWS THAT SELL THEMSELVES (Days 8–12)

---

### DAY 8 — New Employee Onboarding Autoflow

**Goal:** When a new user joins an org, the onboarding agent fires automatically — welcome email, Notion page, Slack DM to manager, all within 5 minutes.

---

**Migration: `20260601_007_onboarding.sql`**

```sql
ALTER TABLE users 
ADD COLUMN IF NOT EXISTS onboarding_status TEXT DEFAULT 'not_started',
ADD COLUMN IF NOT EXISTS onboarding_plan_conversation_id UUID REFERENCES conversations(id),
ADD COLUMN IF NOT EXISTS onboarding_agent_run_id UUID REFERENCES agent_runs(id);

CREATE TABLE onboarding_plans (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL,
    user_id UUID NOT NULL REFERENCES users(id),
    agent_run_id UUID REFERENCES agent_runs(id),
    plan_content TEXT,
    notion_page_id TEXT,
    notion_page_url TEXT,
    welcome_email_sent_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now()
);
```

**File: `apps/api/app/services/agents/onboarding_agent.py`** (NEW FILE)

```python
from app.services.agents.base_agent import BaseAgent
from app.services.llm.task_chain import execute_task
from app.services.integrations.gmail import GmailAdapter
from app.services.integrations.notion import NotionAdapter
from app.services.integrations.slack import SlackAdapter


class OnboardingAgent(BaseAgent):
    def __init__(self, org_id: str, new_hire_data: dict):
        super().__init__(
            org_id=org_id,
            agent_type="onboarding",
            input_data=new_hire_data,
            triggered_by="webhook"
        )
        self.new_hire = new_hire_data
    
    async def run(self):
        await self.create_run_record()
        
        try:
            # Step 1: Generate personalized onboarding plan
            await self.log_step("generate_plan", "started")
            plan = await execute_task(
                org_id=self.org_id,
                query=f"""
                    Create a comprehensive 90-day onboarding plan for {self.new_hire['name']}, 
                    who is joining as {self.new_hire['role']} starting {self.new_hire['start_date']}.
                    Include: Day 1 checklist, Week 1 goals, 30-day milestones, 60-day milestones, 
                    90-day milestones. Reference our company handbook and any role-specific documents.
                    Format clearly with sections and bullet points.
                """,
                intent="task_generation"
            )
            await self.log_step("generate_plan", "completed", {"plan_length": len(plan.text)})
            self.confidence_scores.append(plan.confidence_score)
            
            # Step 2: Create Notion onboarding page
            await self.log_step("create_notion_page", "started")
            notion = NotionAdapter(org_id=self.org_id)
            notion_result = await notion.create_page(
                parent_page_id=await self._get_onboarding_parent_page_id(),
                title=f"Onboarding — {self.new_hire['name']} ({self.new_hire['role']})",
                content=plan.text,
                org_id=self.org_id
            )
            await self.log_step("create_notion_page", "completed", notion_result)
            
            # Step 3: Send welcome email via Gmail
            await self.log_step("send_welcome_email", "started")
            welcome_email = await execute_task(
                org_id=self.org_id,
                query=f"""
                    Write a warm, personal welcome email to {self.new_hire['name']} 
                    who is joining as {self.new_hire['role']} on {self.new_hire['start_date']}.
                    Include: what to expect on Day 1, who to reach out to, link to their 
                    Notion onboarding page at {notion_result['url']}.
                    Keep it under 200 words. Friendly and excited tone.
                """,
                intent="task_generation"
            )
            gmail = GmailAdapter(credentials=await self._get_gmail_credentials())
            send_result = await gmail.send_email(
                to=self.new_hire['email'],
                subject=f"Welcome to the team, {self.new_hire['name'].split()[0]}! 🎉",
                body=welcome_email.text
            )
            await self.log_step("send_welcome_email", "completed", send_result)
            
            # Step 4: Slack DM to manager
            await self.log_step("notify_manager", "started")
            slack = SlackAdapter(org_id=self.org_id)
            manager_message = f"""
Hi! Just wanted to let you know that *{self.new_hire['name']}'s* onboarding plan is ready.

📋 <{notion_result['url']}|View Onboarding Plan in Notion>

The plan covers Day 1, Week 1, and 30/60/90-day milestones for their *{self.new_hire['role']}* role.
A welcome email has been sent to {self.new_hire['email']}.
            """
            await slack.send_dm(
                user_email=self.new_hire['manager_email'],
                text=manager_message
            )
            await self.log_step("notify_manager", "completed")
            
            # Step 5: Save to onboarding_plans
            await supabase_client.table("onboarding_plans").insert({
                "org_id": self.org_id,
                "user_id": self.new_hire['user_id'],
                "agent_run_id": self.run_id,
                "plan_content": plan.text,
                "notion_page_id": notion_result["page_id"],
                "notion_page_url": notion_result["url"],
                "welcome_email_sent_at": datetime.now(timezone.utc).isoformat()
            }).execute()
            
            await self.complete({
                "notion_url": notion_result["url"],
                "welcome_email_sent": True,
                "manager_notified": True
            })
            
        except Exception as e:
            await self.fail(str(e))
            raise
```

**Inngest Trigger:**

```python
@inngest.create_function(
    fn_id="onboard-new-member",
    trigger=inngest.TriggerEvent("org/member-joined")
)
async def onboard_new_member(ctx, step):
    data = ctx.event["data"]
    agent = OnboardingAgent(org_id=data["org_id"], new_hire_data=data)
    await agent.run()
```

**Hook into existing user invite acceptance:** In your `invitations` table handler (wherever `accepted_at` is set), fire `org/member-joined` after the user row is created.

**End of Day 8 checklist:**
- [ ] `onboarding` migration run
- [ ] `OnboardingAgent` class created with all 5 steps
- [ ] Inngest trigger registered on `org/member-joined`
- [ ] Invite acceptance flow fires the event
- [ ] Agent run logged to `agent_runs` with step detail
- [ ] Test with a real org invite flow end-to-end

---

### DAY 9 — Policy Change Propagation Autoflow

**Goal:** When a document tagged "policy" is uploaded, AI diffs it against the previous version, posts a change summary to Slack, and creates acknowledgement tasks for all employees.

---

**Migration: `20260601_008_policy.sql`**

```sql
CREATE TABLE document_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL,
    document_id UUID NOT NULL REFERENCES documents(id),
    version_number INT NOT NULL,
    content_hash TEXT NOT NULL,
    chunk_summary TEXT,  -- AI-generated summary of this version's content
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(document_id, version_number)
);

CREATE TABLE document_diffs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL,
    document_id UUID NOT NULL REFERENCES documents(id),
    from_version INT NOT NULL,
    to_version INT NOT NULL,
    diff_summary TEXT NOT NULL,  -- AI-generated "what changed" in 3-5 bullets
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE acknowledgements (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL,
    document_id UUID NOT NULL REFERENCES documents(id),
    document_version_id UUID REFERENCES document_versions(id),
    user_id UUID NOT NULL REFERENCES users(id),
    status TEXT DEFAULT 'pending',  -- pending | acknowledged | overdue
    acknowledged_at TIMESTAMPTZ,
    reminder_count INT DEFAULT 0,
    last_reminder_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(document_id, user_id, document_version_id)
);

ALTER TABLE documents
ADD COLUMN IF NOT EXISTS requires_acknowledgement BOOLEAN DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS current_version_id UUID REFERENCES document_versions(id);
```

**File: `apps/api/app/services/agents/policy_propagation_agent.py`** (NEW FILE)

```python
class PolicyPropagationAgent(BaseAgent):
    async def run(self, document_id: str):
        await self.create_run_record()
        
        try:
            # Step 1: Get document content and previous version
            await self.log_step("fetch_document", "started")
            doc = await get_document(document_id, self.org_id)
            prev_version = await get_previous_version(document_id, self.org_id)
            await self.log_step("fetch_document", "completed", {"title": doc.title, "has_previous": prev_version is not None})
            
            # Step 2: If previous version exists, generate diff summary
            diff_summary = None
            if prev_version:
                await self.log_step("generate_diff", "started")
                diff_result = await execute_task(
                    org_id=self.org_id,
                    query=f"""
                        Compare these two versions of our {doc.title} and list what changed.
                        Previous version summary: {prev_version.chunk_summary}
                        New version: {doc.content[:3000]}
                        
                        Output exactly 3-5 bullet points describing key changes.
                        Be specific: mention section names, numbers, dates that changed.
                        Start each bullet with an action word: "Changed", "Added", "Removed", "Updated".
                    """,
                    intent="analysis"
                )
                diff_summary = diff_result.text
                
                # Save diff
                await supabase_client.table("document_diffs").insert({
                    "org_id": self.org_id,
                    "document_id": document_id,
                    "from_version": prev_version.version_number,
                    "to_version": prev_version.version_number + 1,
                    "diff_summary": diff_summary
                }).execute()
                await self.log_step("generate_diff", "completed", {"diff_length": len(diff_summary)})
            
            # Step 3: Post to Slack
            await self.log_step("post_slack", "started")
            slack_channel = await self._detect_relevant_channel(doc.tags)
            slack_text = self._build_policy_slack_message(doc.title, diff_summary, prev_version is not None)
            slack = SlackAdapter(org_id=self.org_id)
            await slack.post_message(channel_id=slack_channel, text=slack_text)
            await self.log_step("post_slack", "completed", {"channel": slack_channel})
            
            # Step 4: Create acknowledgement tasks for all org members
            await self.log_step("create_acknowledgements", "started")
            all_users = await get_org_users(self.org_id)
            new_version = await save_document_version(document_id, doc)
            
            ack_records = [
                {
                    "org_id": self.org_id,
                    "document_id": document_id,
                    "document_version_id": new_version.id,
                    "user_id": user.id,
                    "status": "pending"
                }
                for user in all_users
            ]
            await supabase_client.table("acknowledgements").insert(ack_records).execute()
            await self.log_step("create_acknowledgements", "completed", {"count": len(ack_records)})
            
            await self.complete({
                "diff_generated": diff_summary is not None,
                "slack_posted": True,
                "acknowledgements_created": len(all_users)
            })
            
        except Exception as e:
            await self.fail(str(e))
            raise
    
    def _build_policy_slack_message(self, title: str, diff: str, has_changes: bool) -> str:
        if has_changes and diff:
            return f"""📋 *Policy Update: {title}*

*What changed:*
{diff}

All team members have been asked to acknowledge this update in Company Brain.
"""
        return f"""📋 *New Policy Document: {title}*

This document has been added to Company Brain. All team members have been asked to acknowledge it.
"""
```

**Trigger hook:** In your document processing pipeline (Inngest function that runs after a document status becomes 'ready'), check if `doc.tags` contains "policy". If yes, fire the policy propagation agent.

**End of Day 9 checklist:**
- [ ] All three migrations run (`document_versions`, `document_diffs`, `acknowledgements`)
- [ ] `PolicyPropagationAgent` created with 4 steps
- [ ] Hook added to document processing pipeline
- [ ] `_detect_relevant_channel` function maps doc tags to Slack channels (stored in org_config)
- [ ] Acknowledgement banner shown to users in the app
- [ ] Admin can mark documents as `requires_acknowledgement` from document detail page

---

### DAY 10 — Compliance Acknowledgement Tracker + Daily Reminder Cron

**Goal:** Users see pending acknowledgements on login. Admins see a compliance dashboard. Daily cron sends reminders to non-acknowledgers.

---

**Frontend: Acknowledgement Banner**

On the main dashboard (and chat page), check for pending acknowledgements on mount:

```tsx
// apps/web/components/AcknowledgementBanner.tsx
export function AcknowledgementBanner() {
  const { data } = useSWR("/api/compliance/my-pending")
  
  if (!data?.pending?.length) return null
  
  return (
    <Alert className="border-blue-200 bg-blue-50 mb-4">
      <FileText className="h-4 w-4 text-blue-600" />
      <AlertDescription className="text-blue-800">
        You have {data.pending.length} policy document{data.pending.length > 1 ? "s" : ""} to acknowledge.{" "}
        <Link href="/compliance/pending" className="underline font-medium">Review now</Link>
      </AlertDescription>
    </Alert>
  )
}
```

**File: `apps/api/app/routers/compliance.py`** (NEW FILE)

```python
@router.get("/my-pending")
async def get_my_pending_acknowledgements(current_user=Depends(get_current_user)):
    result = await supabase_client.table("acknowledgements").select(
        "*, documents(id, title, updated_at)"
    ).eq("user_id", current_user.id).eq("status", "pending").execute()
    return {"pending": result.data}


@router.post("/{document_id}/acknowledge")
async def acknowledge_document(document_id: str, current_user=Depends(get_current_user)):
    await supabase_client.table("acknowledgements").update({
        "status": "acknowledged",
        "acknowledged_at": datetime.now(timezone.utc).isoformat()
    }).eq("document_id", document_id).eq("user_id", current_user.id).execute()
    return {"acknowledged": True}


@router.get("/admin/compliance-report")
async def get_compliance_report(current_user=Depends(get_current_admin_user)):
    """
    Returns per-document acknowledgement rates and per-user completion rates.
    """
    # Group by document: total expected, total acknowledged, completion %
    # Group by user: which documents they haven't acknowledged
    ...
```

**Daily Reminder Cron:**

```python
@inngest.create_function(
    fn_id="compliance-daily-reminders",
    trigger=inngest.TriggerCron("compliance-reminders", cron="0 9 * * *")  # 9am daily
)
async def send_compliance_reminders(ctx, step):
    # Find all overdue acknowledgements (pending for 3+ days, reminder_count < 3)
    overdue = await supabase_client.table("acknowledgements").select(
        "*, users(email, name), documents(title)"
    ).eq("status", "pending").lte("created_at", (datetime.now() - timedelta(days=3)).isoformat()).lt("reminder_count", 3).execute()
    
    # Group by user, send one email per user listing all their pending docs
    by_user = group_by(overdue.data, "user_id")
    
    for user_id, acks in by_user.items():
        await step.run(f"remind-user-{user_id}", lambda: send_acknowledgement_reminder(
            user=acks[0]["users"],
            pending_docs=[a["documents"] for a in acks]
        ))
        
        # Increment reminder_count
        ack_ids = [a["id"] for a in acks]
        await supabase_client.table("acknowledgements").update({
            "reminder_count": supabase_client.rpc("increment", {"x": 1}),
            "last_reminder_at": datetime.now(timezone.utc).isoformat()
        }).in_("id", ack_ids).execute()
```

**Frontend: Admin Compliance Dashboard at `/admin/compliance`**

- Two views: By Document and By Employee
- By Document: table of all policy docs, columns: Document Name, Total Required, Acknowledged, Pending, Completion %, Last Updated
- By Employee: table of all users, columns: Name, Role, Docs Acknowledged, Docs Pending, Last Acknowledged At
- Export to CSV button

**End of Day 10 checklist:**
- [ ] `AcknowledgementBanner` shown on dashboard
- [ ] `/compliance/pending` page with acknowledge buttons
- [ ] `compliance.py` router with all endpoints
- [ ] Daily reminder cron registered in Inngest
- [ ] Admin compliance dashboard page with both views
- [ ] CSV export for compliance report

---

### DAY 11 — Weekly Admin Knowledge Digest

**Goal:** Every Friday at 9am, every org admin receives a formatted email: time saved, queries run, knowledge gaps, low-confidence answers, suggested actions.

---

**Inngest Cron Function:**

```python
@inngest.create_function(
    fn_id="weekly-knowledge-digest",
    trigger=inngest.TriggerCron("weekly-digest", cron="0 9 * * 5")  # Every Friday 9am UTC
)
async def send_weekly_digest(ctx, step):
    # Get all active orgs
    orgs = await step.run("get-active-orgs", lambda:
        supabase_client.table("organizations")
        .select("id, name")
        .eq("status", "active")
        .execute()
    )
    
    for org in orgs.data:
        await step.run(f"digest-{org['id']}", lambda: generate_and_send_digest(org["id"], org["name"]))


async def generate_and_send_digest(org_id: str, org_name: str):
    week_ago = (datetime.now() - timedelta(days=7)).isoformat()
    
    # Gather all data from existing analytics tables
    stats = await gather_weekly_stats(org_id, week_ago)
    # stats includes: queries_run, time_saved_minutes, knowledge_gaps, 
    # negative_feedback_count, new_documents, top_topics, low_confidence_count
    
    # Get admin emails for this org
    admins = await get_org_admins(org_id)
    
    for admin in admins:
        await send_weekly_digest_email(
            to=admin["email"],
            name=admin["name"],
            org_name=org_name,
            stats=stats
        )


async def gather_weekly_stats(org_id: str, since: str) -> dict:
    """Pull from existing analytics tables — no new tables needed."""
    
    queries = await supabase_client.table("messages").select("id, time_saved_minutes, feedback, confidence_score, intent").eq("org_id", org_id).gte("created_at", since).execute()
    
    gaps = await supabase_client.table("knowledge_gaps").select("topic").eq("org_id", org_id).gte("created_at", since).execute()
    
    docs = await supabase_client.table("documents").select("id, title").eq("org_id", org_id).gte("created_at", since).execute()
    
    time_saved = sum(m.get("time_saved_minutes", 0) for m in queries.data)
    negative_count = sum(1 for m in queries.data if m.get("feedback") == "negative")
    low_conf_count = sum(1 for m in queries.data if (m.get("confidence_score") or 0) < 5.0)
    
    # Top topics from query intent/content
    gap_topics = [g["topic"] for g in gaps.data]
    top_gap_topics = Counter(gap_topics).most_common(3)
    
    return {
        "queries_run": len(queries.data),
        "time_saved_minutes": round(time_saved, 1),
        "time_saved_hours": round(time_saved / 60, 1),
        "knowledge_gaps_count": len(gaps.data),
        "top_gap_topics": top_gap_topics,
        "new_documents": len(docs.data),
        "negative_feedback_count": negative_count,
        "low_confidence_count": low_conf_count,
        "new_document_titles": [d["title"] for d in docs.data[:5]]
    }
```

**Resend Email Template** — `weekly-digest` template:

The email structure:
1. **Header**: "Your Company Brain Weekly Report — Week of [date]"
2. **Hero stat**: "Your team saved **X hours** this week" (big, bold)
3. **Stats row**: Queries Run | Documents Added | Knowledge Gaps Found
4. **Knowledge Gaps section**: "These topics were asked but had no answers: [list]" → "Click to review AI-drafted stubs"
5. **Action items section**: "3 things to do this week: [specific recommendations]"
6. **Footer**: Unsubscribe link, Settings link

This email is built as a React Email component and registered with Resend.

**End of Day 11 checklist:**
- [ ] `gather_weekly_stats` function pulls from all existing analytics tables
- [ ] Inngest cron function registered (`0 9 * * 5`)
- [ ] React Email weekly digest template created
- [ ] Template registered in Resend dashboard
- [ ] Test send with real org data
- [ ] Admin can manually trigger digest from Settings page (for testing)
- [ ] Unsubscribe preference stored in `org_config.digest_unsubscribed`

---

### DAY 12 — Support Response Agent + Auto-Tagger

**Goal:** Inbound emails to the company support address get processed through Company Brain, draft responses created, routed to support queue. Auto-tagging makes collections work without manual effort.

---

#### PART A — Support Response Agent

**Migration: `20260601_009_support_tickets.sql`**

```sql
CREATE TABLE support_tickets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL,
    inbound_email_id TEXT,           -- ID from Resend inbound
    from_email TEXT NOT NULL,
    from_name TEXT,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    category TEXT,                   -- 'support' | 'sales' | 'billing' | 'internal'
    ai_draft_message_id UUID REFERENCES messages(id),
    status TEXT DEFAULT 'pending',   -- pending | approved | sent | rejected
    agent_run_id UUID REFERENCES agent_runs(id),
    created_at TIMESTAMPTZ DEFAULT now(),
    resolved_at TIMESTAMPTZ
);
```

**File: `apps/api/app/services/integrations/email_forward.py`**

Find the existing email ingestion handler. Add routing logic before the "ingest as document" path:

```python
async def handle_inbound_email(email_data: dict, org_id: str):
    sender = email_data["from"]
    subject = email_data["subject"]
    body = email_data["text"]
    
    # Classify the email
    classification = await classify_inbound_email(subject, body)
    
    if classification == "support" or classification == "sales":
        # Route to support agent pipeline
        await inngest.send({
            "name": "support/email-received",
            "data": {
                "org_id": org_id,
                "from_email": sender,
                "subject": subject,
                "body": body,
                "category": classification
            }
        })
    else:
        # Existing behavior: ingest as knowledge document
        await ingest_email_as_document(email_data, org_id)
```

**File: `apps/api/app/services/agents/support_response_agent.py`** (NEW FILE)

```python
class SupportResponseAgent(BaseAgent):
    async def run(self, email_data: dict):
        await self.create_run_record()
        
        try:
            # Step 1: Save ticket
            await self.log_step("create_ticket", "started")
            ticket = await supabase_client.table("support_tickets").insert({
                "org_id": self.org_id,
                "from_email": email_data["from_email"],
                "subject": email_data["subject"],
                "body": email_data["body"],
                "category": email_data["category"],
                "agent_run_id": self.run_id
            }).execute()
            ticket_id = ticket.data[0]["id"]
            await self.log_step("create_ticket", "completed", {"ticket_id": ticket_id})
            
            # Step 2: Draft response using knowledge base
            await self.log_step("draft_response", "started")
            draft = await execute_task(
                org_id=self.org_id,
                query=f"""
                    A customer sent us this email:
                    Subject: {email_data['subject']}
                    Message: {email_data['body']}
                    
                    Draft a helpful, professional response using our company knowledge base.
                    Match our brand voice. Be specific — cite actual policies, prices, or 
                    procedures from our documentation. Keep it under 200 words.
                    Do NOT make up information. If you don't know the answer, say so clearly.
                """,
                intent="task_generation"
            )
            await self.log_step("draft_response", "completed", {"confidence": draft.confidence_score})
            self.confidence_scores.append(draft.confidence_score)
            
            # Step 3: Save draft as message, link to ticket
            message_result = await save_agent_message(
                org_id=self.org_id,
                content=draft.text,
                sources=draft.sources,
                confidence_score=draft.confidence_score,
                agent_run_id=self.run_id
            )
            await supabase_client.table("support_tickets").update({
                "ai_draft_message_id": message_result["id"]
            }).eq("id", ticket_id).execute()
            
            # Step 4: Notify support team (Slack + dashboard)
            await self.log_step("notify_support_team", "started")
            await SlackAdapter(org_id=self.org_id).post_message(
                channel_id=await self._get_support_channel_id(),
                text=f"📧 *New support email from {email_data['from_email']}*\n*Subject:* {email_data['subject']}\n*Confidence:* {draft.confidence_score}/10\n<{self._get_ticket_url(ticket_id)}|Review & Send Draft>"
            )
            await self.log_step("notify_support_team", "completed")
            
            await self.complete({"ticket_id": ticket_id, "draft_ready": True})
            
        except Exception as e:
            await self.fail(str(e))
            raise
```

**Frontend: `/admin/support` page**

List of inbound tickets with columns: From, Subject, Category, AI Confidence, Status, Created At.
Clicking opens ticket detail: full email body, AI draft response (editable), sources used, "Approve & Send" / "Edit & Send" / "Reject" buttons.

---

#### PART B — Auto-Tagger

**Hook into existing document processing Inngest function** (after `status → 'ready'`):

```python
# In your existing process-document Inngest function, add this step:
await step.run("auto-tag-document", lambda: auto_tag_document(document_id, org_id))

async def auto_tag_document(document_id: str, org_id: str):
    doc = await get_document(document_id, org_id)
    
    result = await execute_task(
        org_id=org_id,
        query=f"""
            Analyze this document and return ONLY a JSON array of tags (no other text).
            Document title: {doc.title}
            Document content (first 1000 chars): {doc.content[:1000]}
            
            Choose from these categories: policy, hr, onboarding, recruiting, legal, 
            finance, engineering, product, sales, marketing, customer-success, 
            operations, compliance, handbook, meeting-notes, announcement
            
            Return only the most relevant 2-4 tags as a JSON array.
            Example: ["hr", "policy", "compliance"]
        """,
        intent="analysis"
    )
    
    try:
        tags = json.loads(result.text.strip())
        await supabase_client.table("documents").update({
            "tags": tags,
            "auto_tagged": True
        }).eq("id", document_id).execute()
    except json.JSONDecodeError:
        pass  # Fail silently — manual tagging remains available
```

**End of Day 12 checklist:**
- [ ] `support_tickets` migration run
- [ ] `email_forward.py` updated with classification routing
- [ ] `SupportResponseAgent` created
- [ ] Support Inngest function registered
- [ ] Admin support queue page at `/admin/support`
- [ ] Ticket detail page with approve/send actions
- [ ] Auto-tagger hooked into document processing pipeline
- [ ] `auto_tagged` boolean added to documents table (migration)
- [ ] Admin UI shows auto-assigned tags with "AI" badge

---

## PHASE 3 — ENTERPRISE MOAT (Days 13–15)

---

### DAY 13 — Enhanced Slack /brain + Meeting Notes Ingestion

**Goal:** Slack becomes a full execution surface. Meeting transcripts uploaded or forwarded trigger structured extraction.

---

#### PART A — Slack /brain Execution Commands

**Extend `apps/api/app/routers/slack_router.py`:**

New command handlers for `/brain`:

```python
COMMAND_PATTERNS = {
    r"draft email to (.+) about (.+)": handle_draft_email_command,
    r"post (.+) to (#\S+)": handle_post_to_channel_command,
    r"add action item[:\s]+(.+)": handle_add_action_item_command,
    r"onboard (.+) as (.+)": handle_onboard_command,
    r"summarize (.+)": handle_summarize_command,
}

async def handle_slack_command(command_text: str, user_id: str, channel_id: str, org_id: str):
    for pattern, handler in COMMAND_PATTERNS.items():
        match = re.search(pattern, command_text, re.IGNORECASE)
        if match:
            return await handler(match, user_id, channel_id, org_id)
    
    # Default: pass to execute_task and return ephemeral response
    return await handle_general_query(command_text, user_id, channel_id, org_id)


async def handle_draft_email_command(match, user_id, channel_id, org_id):
    recipient_hint = match.group(1)
    topic = match.group(2)
    
    draft = await execute_task(
        org_id=org_id,
        query=f"Draft an email to {recipient_hint} about {topic}",
        intent="task_generation"
    )
    
    # Return as ephemeral Block Kit message with Send/Edit/Discard buttons
    blocks = build_email_draft_blocks(
        draft_text=draft.text,
        message_id=draft.message_id,
        confidence_score=draft.confidence_score,
        confidence_label=get_confidence_label(draft.confidence_score, org_id)
    )
    
    return {
        "response_type": "ephemeral",
        "blocks": blocks
    }
```

**Block Kit interaction handler** (extending Day 1's `/interactions` endpoint):

```python
async def handle_email_send_action(payload: dict):
    message_id = payload["actions"][0]["value"]
    user_id = payload["user"]["id"]
    
    # Get the draft from DB
    message = await get_message(message_id)
    
    # Open a Slack modal for To/Subject before sending
    await slack_client.views_open(
        trigger_id=payload["trigger_id"],
        view=build_email_send_modal(message_id, message["content"])
    )
    return {"ok": True}
```

---

#### PART B — Meeting Notes Auto-Ingestion

**File: `apps/api/app/services/parsers/meeting_transcript.py`** (NEW FILE)

```python
import re


def parse_zoom_vtt(content: str) -> dict:
    """
    Parses Zoom VTT transcript format.
    Returns: { speakers: [], utterances: [{speaker, text, start_time}] }
    """
    lines = content.strip().split("\n")
    utterances = []
    current_speaker = None
    current_text = []
    
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        
        # Timestamp line
        if "-->" in line:
            i += 1
            if i < len(lines):
                text_line = lines[i].strip()
                # Speaker detection: "Speaker Name: text"
                if ":" in text_line:
                    parts = text_line.split(":", 1)
                    current_speaker = parts[0].strip()
                    current_text = [parts[1].strip()]
                else:
                    current_text.append(text_line)
        elif line and not line.isdigit() and "-->" not in line:
            if current_speaker:
                current_text.append(line)
        elif not line and current_speaker and current_text:
            utterances.append({
                "speaker": current_speaker,
                "text": " ".join(current_text).strip()
            })
            current_text = []
        
        i += 1
    
    speakers = list(set(u["speaker"] for u in utterances))
    return {"speakers": speakers, "utterances": utterances}


def parse_teams_transcript(content: str) -> dict:
    """Teams generates JSON transcripts — parse accordingly."""
    import json
    data = json.loads(content)
    utterances = [
        {"speaker": e["speakerDisplayName"], "text": e["spokenText"]}
        for e in data.get("entries", [])
    ]
    speakers = list(set(u["speaker"] for u in utterances))
    return {"speakers": speakers, "utterances": utterances}
```

**Extend document upload handler** to detect meeting transcript file types (.vtt, .json with Teams format):

```python
async def process_meeting_transcript(document_id: str, org_id: str, file_content: str, file_ext: str):
    if file_ext == ".vtt":
        parsed = parse_zoom_vtt(file_content)
    elif file_ext == ".json":
        parsed = parse_teams_transcript(file_content)
    else:
        return  # Not a transcript
    
    full_text = "\n".join([f"{u['speaker']}: {u['text']}" for u in parsed["utterances"]])
    
    # Extract structured info using LLM
    extraction = await execute_task(
        org_id=org_id,
        query=f"""
            Analyze this meeting transcript and extract:
            1. ATTENDEES: List all speakers
            2. DECISIONS: Key decisions made (each on a new line starting with "- ")
            3. ACTION ITEMS: Each in format "- [OWNER]: [Task] by [Date if mentioned]"
            4. SUMMARY: 3-sentence meeting summary
            
            Transcript:
            {full_text[:8000]}
            
            Return in this exact format with these exact headers.
        """,
        intent="analysis"
    )
    
    # Post action items to Slack
    action_items = extract_action_items_from_response(extraction.text)
    if action_items:
        await SlackAdapter(org_id=org_id).post_message(
            channel_id=await get_general_channel_id(org_id),
            text=f"📋 *Action items from meeting:*\n" + "\n".join(action_items)
        )
    
    # Save structured summary as a new document
    await create_document_from_extraction(org_id, extraction.text, document_id)
```

**End of Day 13 checklist:**
- [ ] Slack `/brain` command router extended with all patterns
- [ ] Email draft modal in Slack (Block Kit)
- [ ] `meeting_transcript.py` parser for VTT and Teams JSON
- [ ] Meeting transcript auto-detection in upload pipeline
- [ ] Action item extraction and Slack post
- [ ] Structured meeting summary saved to knowledge base

---

### DAY 14 — Public Agent Trigger API + API-Triggered Onboarding

**Goal:** External systems (BambooHR, Rippling, Notion, Airtable) can trigger Company Brain agents via a simple REST API with API key auth.

---

**File: `apps/api/app/routers/public_api.py`**

Extend existing public API with agent trigger endpoints:

```python
class AgentTriggerRequest(BaseModel):
    input: dict                              # Agent-specific input
    output_channels: list[str] = []          # ["email", "slack", "notion"]
    approver_email: str = None               # If set, routes to approval before execution
    webhook_url: str = None                  # Optional: POST result to this URL when done


class AgentTriggerResponse(BaseModel):
    agent_run_id: str
    status: str
    estimated_completion_seconds: int


AGENT_INPUT_SCHEMAS = {
    "onboarding": {
        "required": ["name", "email", "role", "start_date"],
        "optional": ["manager_email", "department"]
    },
    "policy_propagation": {
        "required": ["document_id"],
        "optional": []
    },
    "support_response": {
        "required": ["from_email", "subject", "body"],
        "optional": ["category"]
    },
    "weekly_digest": {
        "required": [],
        "optional": ["send_to_email"]
    }
}


@router.post("/v1/agents/{agent_type}/run", response_model=AgentTriggerResponse)
async def trigger_agent(
    agent_type: str,
    req: AgentTriggerRequest,
    api_key: str = Header(alias="X-API-Key")
):
    # 1. Validate API key (existing mechanism)
    org = await validate_api_key(api_key)
    if not org:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    # 2. Validate agent type
    if agent_type not in AGENT_INPUT_SCHEMAS:
        raise HTTPException(status_code=400, detail=f"Unknown agent type. Valid types: {list(AGENT_INPUT_SCHEMAS.keys())}")
    
    # 3. Validate required input fields
    schema = AGENT_INPUT_SCHEMAS[agent_type]
    missing = [f for f in schema["required"] if f not in req.input]
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing required fields: {missing}")
    
    # 4. If approver set, create approval record instead of running directly
    if req.approver_email:
        approval_id = await create_api_approval(
            org_id=org.id,
            agent_type=agent_type,
            input_data=req.input,
            approver_email=req.approver_email
        )
        return AgentTriggerResponse(
            agent_run_id=approval_id,
            status="pending_approval",
            estimated_completion_seconds=0
        )
    
    # 5. Fire the agent via Inngest
    run_id = str(uuid.uuid4())
    await inngest.send({
        "name": f"agent/{agent_type}/triggered",
        "data": {
            "run_id": run_id,
            "org_id": org.id,
            "input": req.input,
            "output_channels": req.output_channels,
            "webhook_url": req.webhook_url,
            "triggered_by": "api"
        }
    })
    
    return AgentTriggerResponse(
        agent_run_id=run_id,
        status="running",
        estimated_completion_seconds=30
    )


@router.get("/v1/agent-runs/{run_id}")
async def get_agent_run_status(
    run_id: str,
    api_key: str = Header(alias="X-API-Key")
):
    org = await validate_api_key(api_key)
    result = await supabase_client.table("agent_runs").select(
        "id, status, steps, output, error, created_at, completed_at"
    ).eq("id", run_id).eq("org_id", org.id).single().execute()
    
    if not result.data:
        raise HTTPException(status_code=404, detail="Agent run not found")
    
    return result.data
```

**Webhook callback handler** — when an agent completes and `webhook_url` was provided:

```python
async def notify_webhook_on_completion(run_id: str, webhook_url: str, result: dict):
    """Called from agent completion step when webhook_url is set."""
    import httpx
    async with httpx.AsyncClient() as client:
        await client.post(
            webhook_url,
            json={
                "event": "agent.completed",
                "agent_run_id": run_id,
                "status": "completed",
                "output": result
            },
            timeout=10.0
        )
```

**API Documentation page** — `apps/web/app/settings/api/page.tsx`

Simple docs page showing:
- Your API key (masked with reveal button)
- Available endpoints with example curl commands
- Agent input schemas
- Example: "Trigger onboarding from BambooHR webhook"

```
POST https://api.companybrain.com/v1/agents/onboarding/run
X-API-Key: cb_live_xxxxxxxxxxxx
Content-Type: application/json

{
  "input": {
    "name": "Sarah Chen",
    "email": "sarah@acme.com",
    "role": "Senior Product Designer",
    "start_date": "2026-07-01",
    "manager_email": "john@acme.com"
  },
  "output_channels": ["email", "slack", "notion"]
}
```

**End of Day 14 checklist:**
- [ ] `/v1/agents/{agent_type}/run` endpoint created
- [ ] `/v1/agent-runs/{run_id}` status polling endpoint
- [ ] All agent types registered in `AGENT_INPUT_SCHEMAS`
- [ ] Approver flow for API-triggered agents
- [ ] Webhook callback on completion
- [ ] API docs page in settings
- [ ] Test with curl against staging

---

### DAY 15 — Output Improvement Loop + Version Diff + Final Integration Testing

**Goal:** Close the feedback loop. Negative feedback triggers analysis and admin alerts. Document version diffs surface automatically. Full end-to-end integration test of all 15 days.

---

#### PART A — Output Improvement Loop

**Migration: `20260601_010_feedback_analysis.sql`**

```sql
ALTER TABLE messages
ADD COLUMN IF NOT EXISTS feedback_analysis JSONB DEFAULT NULL;
-- Shape: { failure_reason: "wrong_tone"|"missing_context"|"outdated_policy"|"hallucination"|"wrong_format", suggested_doc: "...", analysis_at: "..." }

CREATE TABLE feedback_aggregations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL,
    week_of DATE NOT NULL,
    failure_reason TEXT NOT NULL,
    count INT NOT NULL,
    affected_topics TEXT[],
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(org_id, week_of, failure_reason)
);
```

**Inngest function triggered on negative feedback:**

```python
@inngest.create_function(
    fn_id="analyze-negative-feedback",
    trigger=inngest.TriggerEvent("message/feedback-negative")
)
async def analyze_negative_feedback(ctx, step):
    data = ctx.event["data"]
    
    # Step 1: Classify failure reason using LLM
    message = await get_message(data["message_id"])
    
    analysis = await step.run("classify-failure", lambda: execute_task(
        org_id=data["org_id"],
        query=f"""
            A user gave negative feedback on this AI-generated response.
            
            User Query: {message['query']}
            AI Response: {message['content'][:1000]}
            Sources Used: {message['sources']}
            Confidence Score: {message['confidence_score']}
            
            Classify the MOST LIKELY failure reason as EXACTLY one of:
            - wrong_tone: response tone didn't match the context
            - missing_context: key information was missing from the knowledge base
            - outdated_policy: response cited an old/incorrect policy
            - hallucination: response contained information not in any source document
            - wrong_format: output format was incorrect for the use case
            
            Also suggest: what document, if it existed, would have improved this answer?
            
            Return JSON only: {{ "failure_reason": "...", "suggested_doc": "..." }}
        """,
        intent="analysis"
    ))
    
    try:
        result = json.loads(analysis.text.strip())
        failure_reason = result.get("failure_reason")
        suggested_doc = result.get("suggested_doc")
    except:
        failure_reason = "unknown"
        suggested_doc = None
    
    # Step 2: Save analysis to message
    await step.run("save-analysis", lambda: supabase_client.table("messages").update({
        "feedback_analysis": {
            "failure_reason": failure_reason,
            "suggested_doc": suggested_doc,
            "analysis_at": datetime.now(timezone.utc).isoformat()
        }
    }).eq("id", data["message_id"]).execute())
    
    # Step 3: Check if this failure reason has hit a weekly threshold (5+)
    week_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = week_start - timedelta(days=week_start.weekday())
    
    count = await supabase_client.table("messages").select(
        "id", count="exact"
    ).eq("org_id", data["org_id"]).eq(
        "feedback_analysis->>failure_reason", failure_reason
    ).gte("created_at", week_start.isoformat()).execute()
    
    if count.count >= 5:
        await step.run("alert-admin", lambda: send_feedback_threshold_alert(
            org_id=data["org_id"],
            failure_reason=failure_reason,
            count=count.count,
            suggested_doc=suggested_doc
        ))
```

**Hook into existing feedback endpoint:** In your feedback save handler (wherever `messages.feedback` is set), fire the event:

```python
if feedback_value == "negative":
    await inngest.send({
        "name": "message/feedback-negative",
        "data": {"message_id": message_id, "org_id": org_id}
    })
```

---

#### PART B — Version Diff Summarizer

**Extend the document processing pipeline** — when a document is uploaded and a document with the same normalized title exists, run the diff:

```python
async def check_and_run_version_diff(document_id: str, org_id: str):
    new_doc = await get_document(document_id, org_id)
    normalized_title = normalize_title(new_doc.title)  # "Employee Handbook v4" → "Employee Handbook"
    
    # Find previous document with same normalized title
    previous = await find_previous_version(org_id, normalized_title, exclude_id=document_id)
    
    if not previous:
        # No previous version — just save as version 1
        await save_document_version(document_id, version_number=1, org_id=org_id)
        return
    
    # Generate diff
    diff_result = await execute_task(
        org_id=org_id,
        query=f"""
            Compare these two document versions and summarize what changed.
            
            Previous ({previous.title}):
            {previous.content[:3000]}
            
            New ({new_doc.title}):
            {new_doc.content[:3000]}
            
            List key changes in 3-5 bullet points.
            Be specific: mention section names, numbers, dates that changed.
            Start each bullet with: "Changed:", "Added:", or "Removed:".
        """,
        intent="analysis"
    )
    
    new_version_number = previous.version_number + 1
    await save_document_version(document_id, version_number=new_version_number, org_id=org_id)
    
    # Save diff
    await supabase_client.table("document_diffs").insert({
        "org_id": org_id,
        "document_id": document_id,
        "from_version": previous.version_number,
        "to_version": new_version_number,
        "diff_summary": diff_result.text
    }).execute()
    
    # Show diff on document detail page
    # (stored in document_diffs, fetched by frontend on document load)
```

**Frontend: Version diff on document detail page**

On `/documents/[id]`, check for entries in `document_diffs` for this document. If found, show a "What Changed" collapsible section at the top of the document detail with the diff bullets.

---

#### PART C — Day 15: Full Integration Testing Checklist

Work through this systematically. Test each feature with a real org (create a test org with dummy documents):

**Execution Layer (Days 1–4):**
- [ ] Gmail Send: Draft an email, click Send, verify delivery in Gmail sent items + delivery_status in DB
- [ ] Gmail re-auth banner: Remove gmail.send scope from a test token, confirm banner appears on Send click
- [ ] Slack Post: Generate Slack announcement, post to #general, verify it appears in Slack
- [ ] Notion: Generate a JD, create in Notion, verify page appears in workspace
- [ ] Google Docs: Generate a policy, export to Doc, verify link opens correct Doc

**Confidence + Approval (Days 2–3, 6):**
- [ ] Confidence badge: Verify green/amber/red states appear correctly
- [ ] Org admin sets thresholds in settings, verify badge updates
- [ ] Approval flow: Submit for approval, approver receives email + Slack DM, approves, Gmail sends

**Knowledge Gap (Day 5):**
- [ ] Ask a question with no knowledge base results, verify KnowledgeGapEvent fires
- [ ] Check `knowledge_gaps` table has entry
- [ ] Ask same topic 3x, verify admin email arrives with AI stub draft
- [ ] Admin reviews draft in panel, approves, document created

**Agent Runs (Day 7):**
- [ ] Audit trail shows all steps for onboarding agent run
- [ ] Failed run shows error state and step where it failed

**Onboarding Autoflow (Day 8):**
- [ ] Invite a new test user to org
- [ ] Verify Inngest `org/member-joined` event fires
- [ ] Verify Notion page created, welcome email sent, manager DMed in Slack
- [ ] Check `onboarding_plans` table populated

**Policy Propagation (Day 9):**
- [ ] Upload a document tagged "policy"
- [ ] Verify Slack post with change summary appears
- [ ] Verify all org users have acknowledgement records created
- [ ] Upload same document again — verify diff is generated

**Compliance (Day 10):**
- [ ] Login as non-admin user, verify acknowledgement banner appears
- [ ] Click through and acknowledge, verify `acknowledged_at` set
- [ ] Admin compliance dashboard shows correct completion rates
- [ ] Manually trigger daily reminder cron, verify emails sent to pending users

**Weekly Digest (Day 11):**
- [ ] Manually trigger digest via Settings
- [ ] Verify email arrives with correct stats (queries, time saved, gaps)
- [ ] Verify action items in email are specific and accurate

**Support Agent (Day 12):**
- [ ] Forward a test email to inbound address
- [ ] Verify it's classified as "support"
- [ ] Verify ticket created, draft response generated
- [ ] Admin approves in support queue, email sends

**Auto-Tagger (Day 12):**
- [ ] Upload an HR document — verify "hr", "policy" tags auto-applied
- [ ] Upload a meeting notes doc — verify "meeting-notes" tag applied
- [ ] Admin UI shows "AI" badge on auto-tagged documents

**Slack /brain (Day 13):**
- [ ] `/brain draft email to John about the new policy` — verify Block Kit card appears
- [ ] Click Send — verify Gmail send modal opens in Slack

**Meeting Notes (Day 13):**
- [ ] Upload a Zoom VTT file
- [ ] Verify action items extracted and posted to Slack
- [ ] Verify meeting summary saved to knowledge base

**Public API (Day 14):**
- [ ] `curl POST /v1/agents/onboarding/run` with valid API key
- [ ] Verify agent_run_id returned
- [ ] `curl GET /v1/agent-runs/{id}` returns status: completed
- [ ] Webhook URL receives POST callback on completion

**Feedback Loop (Day 15):**
- [ ] Click thumbs down on an AI response
- [ ] Verify `message/feedback-negative` fires in Inngest
- [ ] Verify `feedback_analysis` JSONB populated on message
- [ ] Trigger 5 negative feedbacks with same failure reason, verify admin alert email

**Version Diff (Day 15):**
- [ ] Upload "Employee Handbook v2" when v1 exists
- [ ] Verify diff generated and saved to `document_diffs`
- [ ] Document detail page shows "What Changed" section

---

## APPENDIX: ALL MIGRATIONS IN ORDER

| # | File | Tables Created / Altered | Day |
|---|------|--------------------------|-----|
| 001 | `20260601_001_gmail_integrations.sql` | `gmail_integrations`, `messages.delivery_status` | 1 |
| 002 | `20260601_002_org_config_confidence.sql` | `org_config.min_confidence_*` | 3 |
| 003 | `20260601_003_knowledge_gaps.sql` | `knowledge_gaps` | 5 |
| 004 | `20260601_004_document_drafts.sql` | `document_drafts` | 5 |
| 005 | `20260601_005_approvals.sql` | `approvals` | 6 |
| 006 | `20260601_006_agent_runs.sql` | `agent_runs` | 7 |
| 007 | `20260601_007_onboarding.sql` | `onboarding_plans`, `users.onboarding_*` | 8 |
| 008 | `20260601_008_policy.sql` | `document_versions`, `document_diffs`, `acknowledgements`, `documents.requires_acknowledgement` | 9 |
| 009 | `20260601_009_support_tickets.sql` | `support_tickets` | 12 |
| 010 | `20260601_010_feedback_analysis.sql` | `messages.feedback_analysis`, `feedback_aggregations` | 15 |

---

## APPENDIX: ALL NEW INNGEST FUNCTIONS

| Function ID | Event Trigger | Day | Priority |
|---|---|---|---|
| `gmail-send-email` | `gmail/send-email` | 2 | P0 |
| `slack-post-message` | `slack/post-message` | 3 | P0 |
| `knowledge-gap-detected` | `knowledge/gap-detected` | 5 | P0 |
| `knowledge-gap-threshold` | `knowledge/gap-threshold-hit` | 5 | P0 |
| `approval-requested` | `approval/requested` | 6 | P0 |
| `approval-execute-action` | `approval/execute-action` | 6 | P0 |
| `onboard-new-member` | `org/member-joined` | 8 | P0 |
| `policy-propagation` | `document/policy-uploaded` | 9 | P0 |
| `compliance-daily-reminders` | Cron `0 9 * * *` | 10 | P0 |
| `weekly-knowledge-digest` | Cron `0 9 * * 5` | 11 | P0 |
| `support-email-received` | `support/email-received` | 12 | P0 |
| `analyze-negative-feedback` | `message/feedback-negative` | 15 | P1 |

---

## APPENDIX: ALL NEW API ENDPOINTS

| Method | Path | Auth | Day |
|---|---|---|---|
| POST | `/integrations/gmail/send` | User session | 2 |
| GET | `/integrations/gmail/scope-check` | User session | 2 |
| POST | `/integrations/slack/post` | User session | 3 |
| GET | `/integrations/slack/channels` | User session | 3 |
| PUT | `/admin/config/confidence-thresholds` | Admin session | 3 |
| POST | `/integrations/notion/create-page` | User session | 4 |
| GET | `/integrations/notion/pages` | User session | 4 |
| POST | `/integrations/drive/create-document` | User session | 4 |
| POST | `/approvals` | User session | 6 |
| POST | `/approvals/{id}/resolve` | User session | 6 |
| GET | `/approvals` | User session | 6 |
| GET | `/admin/agent-runs` | Admin session | 7 |
| GET | `/admin/agent-runs/{id}` | Admin session | 7 |
| GET | `/compliance/my-pending` | User session | 10 |
| POST | `/compliance/{doc_id}/acknowledge` | User session | 10 |
| GET | `/admin/compliance-report` | Admin session | 10 |
| GET | `/admin/support` | Admin session | 12 |
| POST | `/admin/support/{id}/send` | Admin session | 12 |
| POST | `/slack/interactions` | Slack signing secret | 1/13 |
| POST | `/v1/agents/{agent_type}/run` | API key | 14 |
| GET | `/v1/agent-runs/{run_id}` | API key | 14 |

---

## APPENDIX: ALL NEW FRONTEND PAGES + COMPONENTS

| Path / Component | Type | Day |
|---|---|---|
| `MessageActions.tsx` | Component | 2 |
| `GmailSendDialog.tsx` | Component | 2 |
| `ConfidenceBadge.tsx` | Component | 2 |
| `SlackChannelPicker.tsx` | Component | 3 |
| `/settings/confidence` | Page | 3 |
| `NotionPagePicker.tsx` | Component | 4 |
| `GoogleDocsExportDialog.tsx` | Component | 4 |
| `/admin/knowledge-gaps` | Page | 5 |
| `DraftReviewDialog.tsx` | Component | 5 |
| `/approvals` | Page | 6 |
| `ApprovalRequestDialog.tsx` | Component | 6 |
| `/admin/agent-runs` | Page | 7 |
| `AgentRunStepDetail.tsx` | Component | 7 |
| `AcknowledgementBanner.tsx` | Component | 10 |
| `/compliance/pending` | Page | 10 |
| `/admin/compliance` | Page | 10 |
| `/admin/support` | Page | 12 |
| `SupportTicketDetail.tsx` | Component | 12 |
| `/settings/api` | Page | 14 |
| `VersionDiffSection.tsx` | Component | 15 |