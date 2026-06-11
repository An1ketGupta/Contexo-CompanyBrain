# Company Brain — V4 Feature Roadmap (4 Days)

## Confirmed Architecture Decisions

| Decision | Choice |
|---|---|
| Charts | recharts (raw charts) + tremor (stat cards) |
| Analytics data | analytics_events (UI events) + query_logs/chunk_citations (query data) |
| Health score | Weighted formula: recency + access frequency + gap flags |
| Chrome Extension manifest | Manifest V3 |
| Chrome Extension UI | iframe embed of existing Next.js chat UI |
| Browser notifications | Skipped — in-app toast on tab return |
| Content moderation | Keyword + regex patterns |
| Template variables | `{{variable}}` double curly brace syntax |
| Meeting Prep | Dedicated page at `/chat/meeting-prep` |

## Feature Index

| # | Feature | Priority | Day |
|---|---|---|---|
| #18 | Admin Usage Analytics Dashboard | P1 | 1 |
| #34 | AI-Powered Document Health Score | P2 | 1 |
| #57 | Activity Feed for Teams | P3 | 1 |
| #60 | Browser Notifications (in-app toast only) | P2 | 1 |
| #79 | Content Moderation | P2 | 2 |
| #70 | Conversation Templates | P2 | 2 |
| #39 | Meeting Prep Assistant | P2 | 2 |
| #68 | Document Versioning | P3 | 2 |
| #32 | Chrome Extension | P2 | 3 |
| #94 | Help Center / In-App Docs | P2 | 4 |
| #74 | Knowledge Base Coverage Score | P3 | 4 |

---

## Day 1 — Analytics, Document Health, Activity Feed, Browser Notifications

**Goal:** Admins get full visibility into product ROI. Documents get scored for health. Team activity is visible. Users get notified when documents finish processing.

**Features:** #18 Admin Analytics Dashboard, #34 Document Health Score, #57 Activity Feed, #60 Browser Notifications (toast)

---

### #18 — Admin Usage Analytics Dashboard

**Install dependencies:**
```bash
npm install @tremor/react recharts
```

**Database — `analytics_events` table:**

```sql
CREATE TABLE analytics_events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  user_id UUID REFERENCES users(id) ON DELETE SET NULL,
  event_type VARCHAR(100) NOT NULL,
  -- Event types: 'chat_sent' | 'doc_uploaded' | 'doc_deleted' | 'feedback_given'
  --              'invite_sent' | 'invite_accepted' | 'template_used' | 'share_created'
  metadata JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_analytics_events_org_time ON analytics_events(org_id, created_at DESC);
CREATE INDEX idx_analytics_events_type ON analytics_events(org_id, event_type, created_at DESC);
CREATE INDEX idx_analytics_events_user ON analytics_events(user_id, created_at DESC);
```

**Backend — `app/services/analytics.py`**

```python
async def track_event(
    org_id: str,
    event_type: str,
    db: AsyncSession,
    user_id: Optional[str] = None,
    metadata: dict = {}
):
    """Fire-and-forget event logger. Never raises — analytics must not break core flows."""
    try:
        db.add(AnalyticsEvent(
            org_id=org_id,
            user_id=user_id,
            event_type=event_type,
            metadata=metadata
        ))
        await db.commit()
    except Exception as e:
        # Log to stderr but never propagate
        print(f"[analytics] Failed to track {event_type}: {e}", file=sys.stderr)
```

**Wire up event tracking across the codebase:**

Add `await track_event(...)` calls (non-blocking, wrapped in try/except) at:

| Location | Event type | metadata |
|---|---|---|
| `POST /api/chat/messages` | `chat_sent` | `{ intent, query_length, source_count }` |
| `POST /api/documents/upload` | `doc_uploaded` | `{ file_type, file_size_kb }` |
| `DELETE /api/documents/{id}` | `doc_deleted` | `{ file_type }` |
| `PATCH /api/chat/messages/{id}/feedback` | `feedback_given` | `{ feedback: 'positive'|'negative' }` |
| `POST /api/organizations/invite` | `invite_sent` | `{ role }` |
| `GET /api/auth/accept-invite` | `invite_accepted` | `{}` |
| `POST /api/templates/{id}/use` | `template_used` | `{ category, template_id }` |
| `POST /api/chat/messages/{id}/share` | `share_created` | `{}` |

**Backend — Analytics query endpoints in `app/api/routes/admin.py`:**

```python
# GET /api/admin/analytics?period=7d|30d|90d
@router.get("/admin/analytics")
async def get_analytics(
    period: str = "30d",
    current_user: User = Depends(get_admin_user),  # Admin-only
    db: AsyncSession = Depends(get_db)
):
    org_id = current_user.org_id
    days = {"7d": 7, "30d": 30, "90d": 90}.get(period, 30)
    cutoff = datetime.utcnow() - timedelta(days=days)

    # --- Stat cards ---

    # Total queries in period
    total_queries = await db.scalar(
        select(func.count()).select_from(QueryLog)
        .where(QueryLog.org_id == org_id, QueryLog.created_at >= cutoff)
    )

    # Active users this week
    week_cutoff = datetime.utcnow() - timedelta(days=7)
    active_users = await db.scalar(
        select(func.count(func.distinct(QueryLog.user_id)))
        .where(QueryLog.org_id == org_id, QueryLog.created_at >= week_cutoff)
    )

    total_users = await db.scalar(
        select(func.count()).select_from(User)
        .where(User.org_id == org_id)
    )

    # Feedback ratio
    positive = await db.scalar(
        select(func.count()).select_from(AnalyticsEvent)
        .where(AnalyticsEvent.org_id == org_id,
               AnalyticsEvent.event_type == 'feedback_given',
               AnalyticsEvent.metadata['feedback'].astext == 'positive',
               AnalyticsEvent.created_at >= cutoff)
    )
    negative = await db.scalar(
        select(func.count()).select_from(AnalyticsEvent)
        .where(AnalyticsEvent.org_id == org_id,
               AnalyticsEvent.event_type == 'feedback_given',
               AnalyticsEvent.metadata['feedback'].astext == 'negative',
               AnalyticsEvent.created_at >= cutoff)
    )
    total_feedback = (positive or 0) + (negative or 0)
    feedback_score = round((positive or 0) / total_feedback * 100) if total_feedback else None

    # Documents stats
    total_docs = await db.scalar(
        select(func.count()).select_from(Document)
        .where(Document.org_id == org_id, Document.status == 'ready')
    )
    docs_accessed = await db.scalar(
        select(func.count(func.distinct(Chunk.document_id)))
        .join(ChunkCitation, ChunkCitation.chunk_id == Chunk.id)
        .where(ChunkCitation.org_id == org_id, ChunkCitation.cited_at >= cutoff)
    )

    # --- Time series: queries per day ---
    daily_queries = await db.execute(
        select(
            func.date_trunc('day', QueryLog.created_at).label('day'),
            func.count().label('count')
        )
        .where(QueryLog.org_id == org_id, QueryLog.created_at >= cutoff)
        .group_by('day')
        .order_by('day')
    )

    # --- Per-user breakdown ---
    user_breakdown = await db.execute(
        select(
            User.name,
            User.email,
            func.count(QueryLog.id).label('query_count'),
            func.max(QueryLog.created_at).label('last_active')
        )
        .join(QueryLog, QueryLog.user_id == User.id)
        .where(QueryLog.org_id == org_id, QueryLog.created_at >= cutoff)
        .group_by(User.id, User.name, User.email)
        .order_by(func.count(QueryLog.id).desc())
    )

    # --- Most-cited documents ---
    top_docs = await db.execute(
        select(Document.name, func.count(ChunkCitation.id).label('citations'))
        .join(Chunk, Chunk.document_id == Document.id)
        .join(ChunkCitation, ChunkCitation.chunk_id == Chunk.id)
        .where(ChunkCitation.org_id == org_id, ChunkCitation.cited_at >= cutoff)
        .group_by(Document.id, Document.name)
        .order_by(func.count(ChunkCitation.id).desc())
        .limit(5)
    )

    # --- Top query categories (intents) ---
    intent_breakdown = await db.execute(
        select(QueryLog.intent, func.count().label('count'))
        .where(QueryLog.org_id == org_id, QueryLog.created_at >= cutoff)
        .group_by(QueryLog.intent)
        .order_by(func.count().desc())
    )

    return {
        "period": period,
        "stats": {
            "total_queries": total_queries,
            "active_users": active_users,
            "total_users": total_users,
            "feedback_score": feedback_score,
            "total_docs": total_docs,
            "docs_accessed": docs_accessed,
        },
        "daily_queries": [{"day": row.day.isoformat(), "count": row.count} for row in daily_queries],
        "user_breakdown": [
            {"name": row.name, "email": row.email, "queries": row.query_count, "last_active": row.last_active.isoformat()}
            for row in user_breakdown
        ],
        "top_documents": [{"name": row.name, "citations": row.citations} for row in top_docs],
        "intent_breakdown": [{"intent": row.intent, "count": row.count} for row in intent_breakdown],
    }
```

**Frontend — `app/admin/analytics/page.tsx`**

Full page layout:

```tsx
'use client'
import { AreaChart, BarChart } from 'recharts'
import { Card, Metric, Text, Badge, ProgressBar } from '@tremor/react'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

const PERIOD_OPTIONS = [
  { label: '7 days', value: '7d' },
  { label: '30 days', value: '30d' },
  { label: '90 days', value: '90d' },
]

const INTENT_LABELS: Record<string, string> = {
  task_generation: 'Writing',
  factual_qa: 'Q&A',
  analysis: 'Analysis',
  search: 'Search',
}

export default function AnalyticsPage() {
  const [period, setPeriod] = useState('30d')
  const { data, isLoading } = useQuery(
    ['analytics', period],
    () => fetch(`/api/admin/analytics?period=${period}`).then(r => r.json())
  )

  if (isLoading) return <AnalyticsSkeleton />

  const { stats, daily_queries, user_breakdown, top_documents, intent_breakdown } = data

  return (
    <div className="max-w-6xl mx-auto px-4 py-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Usage Analytics</h1>
        <div className="flex gap-1 bg-muted rounded-lg p-1">
          {PERIOD_OPTIONS.map(opt => (
            <button
              key={opt.value}
              onClick={() => setPeriod(opt.value)}
              className={cn(
                "px-3 py-1.5 text-sm rounded-md transition-colors",
                period === opt.value
                  ? "bg-background shadow text-foreground"
                  : "text-muted-foreground hover:text-foreground"
              )}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>

      {/* Stat cards — Tremor */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <Card>
          <Text>Total Queries</Text>
          <Metric>{stats.total_queries.toLocaleString()}</Metric>
          <Text className="mt-1 text-xs text-muted-foreground">in last {period}</Text>
        </Card>
        <Card>
          <Text>Active Users</Text>
          <Metric>{stats.active_users}</Metric>
          <Text className="mt-1 text-xs text-muted-foreground">
            of {stats.total_users} total ({Math.round(stats.active_users / stats.total_users * 100)}% adoption)
          </Text>
          <ProgressBar value={stats.active_users / stats.total_users * 100} className="mt-2" />
        </Card>
        <Card>
          <Text>Satisfaction Score</Text>
          <Metric>
            {stats.feedback_score !== null ? `${stats.feedback_score}%` : '—'}
          </Metric>
          <Text className="mt-1 text-xs text-muted-foreground">positive feedback</Text>
        </Card>
        <Card>
          <Text>Docs Accessed</Text>
          <Metric>{stats.docs_accessed}</Metric>
          <Text className="mt-1 text-xs text-muted-foreground">
            of {stats.total_docs} uploaded ({Math.round(stats.docs_accessed / stats.total_docs * 100)}% utilization)
          </Text>
          <ProgressBar value={stats.docs_accessed / stats.total_docs * 100} className="mt-2" />
        </Card>
      </div>

      {/* Queries over time — recharts AreaChart */}
      <Card>
        <Text className="font-medium mb-4">Queries over time</Text>
        <AreaChart
          width={800}
          height={200}
          data={daily_queries}
          margin={{ top: 5, right: 20, left: 0, bottom: 5 }}
        >
          <defs>
            <linearGradient id="queryGradient" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#6366f1" stopOpacity={0.2} />
              <stop offset="95%" stopColor="#6366f1" stopOpacity={0} />
            </linearGradient>
          </defs>
          <XAxis dataKey="day" tickFormatter={d => new Date(d).toLocaleDateString('en', { month: 'short', day: 'numeric' })}
            tick={{ fontSize: 11 }} tickLine={false} axisLine={false} />
          <YAxis tick={{ fontSize: 11 }} tickLine={false} axisLine={false} />
          <Tooltip
            contentStyle={{ fontSize: 12, borderRadius: 8 }}
            labelFormatter={d => new Date(d).toLocaleDateString('en', { weekday: 'short', month: 'short', day: 'numeric' })}
          />
          <Area type="monotone" dataKey="count" stroke="#6366f1" strokeWidth={2}
            fill="url(#queryGradient)" name="Queries" />
        </AreaChart>
      </Card>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Intent breakdown — recharts BarChart */}
        <Card>
          <Text className="font-medium mb-4">Query types</Text>
          <BarChart
            width={380}
            height={200}
            data={intent_breakdown.map((i: any) => ({ ...i, intent: INTENT_LABELS[i.intent] || i.intent }))}
            layout="vertical"
            margin={{ left: 40, right: 20 }}
          >
            <XAxis type="number" tick={{ fontSize: 11 }} tickLine={false} axisLine={false} />
            <YAxis type="category" dataKey="intent" tick={{ fontSize: 11 }} tickLine={false} axisLine={false} />
            <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8 }} />
            <Bar dataKey="count" fill="#6366f1" radius={[0, 4, 4, 0]} name="Queries" />
          </BarChart>
        </Card>

        {/* Top documents */}
        <Card>
          <Text className="font-medium mb-3">Most cited documents</Text>
          <div className="space-y-2">
            {top_documents.map((doc: any, i: number) => (
              <div key={doc.name} className="flex items-center gap-3">
                <span className="text-xs font-mono text-muted-foreground w-4">{i + 1}</span>
                <span className="text-sm flex-1 truncate">{doc.name}</span>
                <Badge size="xs">{doc.citations} cites</Badge>
              </div>
            ))}
          </div>
        </Card>
      </div>

      {/* Per-user breakdown table */}
      <Card>
        <Text className="font-medium mb-3">User breakdown</Text>
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-muted-foreground border-b">
              <th className="pb-2 font-medium">User</th>
              <th className="pb-2 font-medium">Queries</th>
              <th className="pb-2 font-medium">Last active</th>
            </tr>
          </thead>
          <tbody>
            {user_breakdown.map((user: any) => (
              <tr key={user.email} className="border-b last:border-0">
                <td className="py-2">
                  <p className="font-medium">{user.name}</p>
                  <p className="text-xs text-muted-foreground">{user.email}</p>
                </td>
                <td className="py-2">
                  <span className="font-mono text-sm">{user.queries}</span>
                </td>
                <td className="py-2 text-xs text-muted-foreground">
                  {formatRelativeTime(user.last_active)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  )
}
```

Add "Analytics" to the admin settings sidebar nav.

---

### #34 — AI-Powered Document Health Score

**Database Migration:**

```sql
ALTER TABLE documents
  ADD COLUMN IF NOT EXISTS last_accessed_at TIMESTAMP,
  ADD COLUMN IF NOT EXISTS health_score FLOAT,         -- 0.0 to 1.0
  ADD COLUMN IF NOT EXISTS health_label VARCHAR(20),   -- 'healthy' | 'stale' | 'at_risk' | 'unused'
  ADD COLUMN IF NOT EXISTS health_computed_at TIMESTAMP,
  ADD COLUMN IF NOT EXISTS gap_flag_count INTEGER NOT NULL DEFAULT 0;
  -- gap_flag_count incremented from V3 knowledge gap detection
```

**Backend — `app/services/health_score.py`**

```python
from datetime import datetime, timedelta
from dataclasses import dataclass

@dataclass
class HealthFactors:
    age_days: int                    # Days since document was uploaded
    days_since_accessed: int | None  # Days since last chunk citation. None = never
    access_frequency: float          # Citations per week over last 90 days
    gap_flag_count: int              # Times this doc contributed to a knowledge gap

def compute_health_score(f: HealthFactors) -> tuple[float, str]:
    """
    Returns (score: 0.0–1.0, label: 'healthy'|'stale'|'at_risk'|'unused')

    Weights:
    - Recency of access: 40%
    - Access frequency: 40%
    - Gap flags penalty: 20%
    """
    # --- Recency score (0–1) ---
    if f.days_since_accessed is None:
        recency_score = 0.0  # Never accessed
    elif f.days_since_accessed <= 7:
        recency_score = 1.0
    elif f.days_since_accessed <= 30:
        recency_score = 0.75
    elif f.days_since_accessed <= 60:
        recency_score = 0.4
    elif f.days_since_accessed <= 90:
        recency_score = 0.2
    else:
        recency_score = 0.0

    # --- Frequency score (0–1) ---
    # Normalize: 5+ citations/week = perfect score
    freq_score = min(f.access_frequency / 5.0, 1.0)

    # --- Gap penalty (0–1, subtracted) ---
    # Each gap flag reduces score. 10+ flags = max penalty
    gap_penalty = min(f.gap_flag_count / 10.0, 1.0) * 0.2

    # --- Weighted sum ---
    score = (recency_score * 0.40) + (freq_score * 0.40) - gap_penalty
    score = max(0.0, min(1.0, score))  # Clamp to [0, 1]

    # --- Label ---
    if score >= 0.65:
        label = "healthy"
    elif score >= 0.35:
        label = "stale"
    elif score > 0.0:
        label = "at_risk"
    else:
        label = "unused"

    return round(score, 3), label


async def recompute_health_scores(org_id: str, db: AsyncSession):
    """Recalculate health for all ready documents in an org. Called by cron."""
    documents = await db.execute(
        select(Document).where(Document.org_id == org_id, Document.status == 'ready')
    )
    now = datetime.utcnow()
    cutoff_90d = now - timedelta(days=90)

    for doc in documents.scalars():
        # Age
        age_days = (now - doc.created_at).days

        # Days since last access
        days_since_accessed = (
            (now - doc.last_accessed_at).days if doc.last_accessed_at else None
        )

        # Access frequency: citations in last 90 days / 13 weeks
        citation_count = await db.scalar(
            select(func.count(ChunkCitation.id))
            .join(Chunk, Chunk.id == ChunkCitation.chunk_id)
            .where(
                Chunk.document_id == doc.id,
                ChunkCitation.cited_at >= cutoff_90d
            )
        )
        access_frequency = (citation_count or 0) / 13.0  # Per week

        factors = HealthFactors(
            age_days=age_days,
            days_since_accessed=days_since_accessed,
            access_frequency=access_frequency,
            gap_flag_count=doc.gap_flag_count or 0
        )
        score, label = compute_health_score(factors)

        doc.health_score = score
        doc.health_label = label
        doc.health_computed_at = now

    await db.commit()
```

**Update `last_accessed_at` on every chunk citation:**

In `execute_task()`, where chunk citations are logged (V2 Day 12), add:
```python
# Update last_accessed_at on source documents
doc_ids = list(set(chunk.document_id for chunk in source_chunks))
await db.execute(
    update(Document)
    .where(Document.id.in_(doc_ids))
    .values(last_accessed_at=datetime.utcnow())
)
```

**Inngest cron — nightly health score recalculation:**

```python
@inngest.create_function(
    fn_id="documents/recompute-health",
    trigger=inngest.TriggerCron(cron="0 2 * * *")  # 2am UTC every night
)
async def recompute_all_health_scores(ctx, step):
    orgs = await step.run("fetch-orgs", get_all_active_orgs)
    for org in orgs:
        await step.run(
            f"health-{org.id}",
            lambda: recompute_health_scores(org.id)
        )
```

**Frontend — `components/documents/HealthBadge.tsx`**

```tsx
const HEALTH_CONFIG = {
  healthy:  { label: 'Healthy',  color: 'bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300', dot: '🟢' },
  stale:    { label: 'Stale',    color: 'bg-amber-100 text-amber-700 dark:bg-amber-900 dark:text-amber-300', dot: '🟡' },
  at_risk:  { label: 'At Risk',  color: 'bg-orange-100 text-orange-700 dark:bg-orange-900 dark:text-orange-300', dot: '🟠' },
  unused:   { label: 'Unused',   color: 'bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400', dot: '⚪' },
}

export function HealthBadge({ label, score }: { label: string, score: number }) {
  const config = HEALTH_CONFIG[label as keyof typeof HEALTH_CONFIG] || HEALTH_CONFIG.unused
  return (
    <span className={cn("text-xs px-2 py-0.5 rounded-full font-medium flex items-center gap-1", config.color)}
      title={`Health score: ${Math.round(score * 100)}/100`}>
      <span className="text-xs">{config.dot}</span>
      {config.label}
    </span>
  )
}
```

Add `<HealthBadge />` as a column in the document table (between status and actions).

**Knowledge Base Health Dashboard — `app/admin/health/page.tsx`**

```tsx
export default function KnowledgeBaseHealthPage() {
  const { data } = useQuery(['kb-health'], () =>
    fetch('/api/admin/knowledge-health').then(r => r.json())
  )

  return (
    <div className="max-w-4xl mx-auto px-4 py-6 space-y-6">
      <h1 className="text-xl font-semibold">Knowledge Base Health</h1>

      {/* Overall score — Tremor */}
      <div className="grid grid-cols-4 gap-4">
        {['healthy', 'stale', 'at_risk', 'unused'].map(label => (
          <Card key={label}>
            <Text>{HEALTH_CONFIG[label].label}</Text>
            <Metric>{data?.counts[label] || 0}</Metric>
            <Text className="text-xs text-muted-foreground">documents</Text>
          </Card>
        ))}
      </div>

      {/* At-risk and unused documents — action required */}
      {data?.at_risk_docs?.length > 0 && (
        <div>
          <p className="text-sm font-medium mb-2 text-orange-600">
            ⚠ {data.at_risk_docs.length} documents need attention
          </p>
          <div className="space-y-2">
            {data.at_risk_docs.map((doc: any) => (
              <div key={doc.id} className="border rounded-lg px-4 py-3 flex items-center gap-3">
                <FileText size={16} className="text-muted-foreground shrink-0" />
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium truncate">{doc.name}</p>
                  <p className="text-xs text-muted-foreground">
                    {doc.last_accessed_at
                      ? `Last accessed ${formatRelativeTime(doc.last_accessed_at)}`
                      : 'Never accessed'
                    } · {doc.age_days} days old
                  </p>
                </div>
                <HealthBadge label={doc.health_label} score={doc.health_score} />
                <Link href={`/documents?id=${doc.id}`}
                  className="text-xs text-indigo-600 hover:underline shrink-0">
                  Review →
                </Link>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
```

**Backend — `GET /api/admin/knowledge-health`:**

```python
@router.get("/admin/knowledge-health")
async def get_knowledge_health(current_user: User = Depends(get_admin_user), ...):
    docs = await db.execute(
        select(Document).where(
            Document.org_id == current_user.org_id,
            Document.status == 'ready'
        )
    )
    all_docs = docs.scalars().all()

    counts = {"healthy": 0, "stale": 0, "at_risk": 0, "unused": 0}
    for doc in all_docs:
        counts[doc.health_label or 'unused'] += 1

    at_risk = [d for d in all_docs if d.health_label in ('at_risk', 'unused')]
    at_risk.sort(key=lambda d: d.health_score or 0)

    return {
        "counts": counts,
        "total": len(all_docs),
        "at_risk_docs": [doc_to_dict(d) for d in at_risk[:20]]
    }
```

**Weekly digest email** — Extend the existing V2 weekly digest Inngest cron to include health summary:
```python
# In weekly_digest function (V2 Day 3)
health_summary = await get_health_summary(org.id)
if health_summary["at_risk"] > 0:
    # Append to digest email:
    # "⚠ 3 documents are stale or unused — [Review Knowledge Base →]"
```

---

### #57 — Activity Feed for Teams

**Database Migration:**

```sql
CREATE TABLE activity_feed (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  activity_type VARCHAR(100) NOT NULL,
  -- Types: 'generated_content' | 'uploaded_doc' | 'shared_output' | 'used_template'
  summary TEXT NOT NULL,         -- Human-readable: "Generated a job description"
  metadata JSONB NOT NULL DEFAULT '{}',
  is_private BOOLEAN NOT NULL DEFAULT false,  -- User's privacy toggle
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_activity_feed_org ON activity_feed(org_id, is_private, created_at DESC);
CREATE INDEX idx_activity_feed_user ON activity_feed(user_id, created_at DESC);
```

**Backend — Activity logging service `app/services/activity.py`:**

```python
ACTIVITY_TEMPLATES = {
    'generated_content': lambda meta: f"Generated {meta.get('intent_label', 'content')}",
    'uploaded_doc': lambda meta: f"Uploaded "{meta.get('doc_name', 'a document')}"",
    'shared_output': lambda meta: f"Shared an output",
    'used_template': lambda meta: f"Used the "{meta.get('template_title', 'a template')}" template",
}

async def log_activity(
    org_id: str,
    user_id: str,
    activity_type: str,
    metadata: dict,
    db: AsyncSession
):
    try:
        summary = ACTIVITY_TEMPLATES.get(activity_type, lambda m: activity_type)(metadata)
        db.add(ActivityFeed(
            org_id=org_id,
            user_id=user_id,
            activity_type=activity_type,
            summary=summary,
            metadata=metadata
        ))
        await db.commit()
    except Exception:
        pass  # Never break core flows
```

Wire up in same locations as `track_event`:
- After `chat_sent` with intent = `task_generation` → log `generated_content`
- After `doc_uploaded` → log `uploaded_doc`
- After `share_created` → log `shared_output`
- After `template_used` → log `used_template`

**Backend — `GET /api/team/activity`:**

```python
@router.get("/team/activity")
async def get_team_activity(
    limit: int = 20,
    current_user: User = Depends(get_current_user),
    ...
):
    feed = await db.execute(
        select(ActivityFeed, User.name.label('user_name'), User.avatar_url.label('avatar'))
        .join(User, User.id == ActivityFeed.user_id)
        .where(
            ActivityFeed.org_id == current_user.org_id,
            ActivityFeed.is_private == False
        )
        .order_by(ActivityFeed.created_at.desc())
        .limit(limit)
    )
    return feed.all()
```

**Privacy toggle** — User setting in `app/settings/profile/page.tsx`:

```tsx
<div className="flex items-center justify-between">
  <div>
    <p className="text-sm font-medium">Private activity</p>
    <p className="text-xs text-muted-foreground">
      Hide your activity from the team feed
    </p>
  </div>
  <Switch
    checked={user.activity_private}
    onCheckedChange={v => updateUserSettings({ activity_private: v })}
  />
</div>
```

Store `activity_private` boolean on the `users` table:
```sql
ALTER TABLE users ADD COLUMN IF NOT EXISTS activity_private BOOLEAN NOT NULL DEFAULT false;
```

When logging activity, check `user.activity_private` and set `is_private=True` if so.

**Frontend — `components/dashboard/ActivityFeed.tsx`:**

```tsx
export function ActivityFeed() {
  const { data: activities } = useQuery(['team-activity'], () =>
    fetch('/api/team/activity').then(r => r.json()),
    { refetchInterval: 30000 }  // Refresh every 30s
  )

  return (
    <div className="space-y-1">
      <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider px-1 mb-2">
        Team Activity
      </p>
      {(activities || []).map((item: any) => (
        <div key={item.id} className="flex items-start gap-2.5 py-1.5">
          <Avatar name={item.user_name} size="sm" />
          <div className="flex-1 min-w-0">
            <p className="text-xs">
              <span className="font-medium">{item.user_name.split(' ')[0]}</span>
              {' '}{item.summary}
            </p>
            <p className="text-xs text-muted-foreground mt-0.5">
              {formatRelativeTime(item.created_at)}
            </p>
          </div>
        </div>
      ))}
      {!activities?.length && (
        <p className="text-xs text-muted-foreground py-4 text-center">
          No recent activity
        </p>
      )}
    </div>
  )
}
```

Render `<ActivityFeed />` in a right panel on the main dashboard page, or as a collapsible section in the sidebar below conversations.

---

### #60 — Browser Notifications (In-App Toast on Return)

Since we're skipping Web Push, this is a lightweight "welcome back" toast triggered when the user returns to a tab after a document finishes processing.

**Frontend — `hooks/useDocumentReadyToast.ts`**

```tsx
import { useEffect, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

export function useDocumentReadyToast() {
  const qc = useQueryClient()
  const processingDocNames = useRef<Map<string, string>>(new Map())  // id → name

  // Track documents that enter processing state
  const { data: documents } = useQuery(['documents'], fetchDocuments, {
    refetchInterval: 5000  // Poll while tab is active
  })

  useEffect(() => {
    if (!documents) return

    documents.forEach((doc: Document) => {
      if (doc.status === 'processing') {
        processingDocNames.current.set(doc.id, doc.name)
      }

      if (doc.status === 'ready' && processingDocNames.current.has(doc.id)) {
        const name = processingDocNames.current.get(doc.id)!
        processingDocNames.current.delete(doc.id)

        // Show toast when document transitions from processing → ready
        toast.success(`"${name}" is ready!`, {
          description: 'Your document has been processed and is now searchable.',
          action: {
            label: 'Ask a question',
            onClick: () => router.push('/chat/new')
          },
          duration: 8000,
        })
        // Invalidate document list cache
        qc.invalidateQueries(['documents'])
        qc.invalidateQueries(['document-status'])
      }
    })
  }, [documents])
}
```

Call `useDocumentReadyToast()` in the root layout component so it's active across all pages.

**End of Day 1 checklist:**
- [ ] `analytics_events` table created, indexes in place
- [ ] `track_event()` wired into all 8 trigger points
- [ ] `GET /api/admin/analytics?period=` returns all required data shapes
- [ ] Analytics page renders: 4 stat cards, area chart, bar chart, top docs, user table
- [ ] Period selector (7d/30d/90d) works and re-fetches
- [ ] `health_score`, `health_label`, `last_accessed_at` columns on documents
- [ ] `compute_health_score()` weighted formula implemented and unit-testable
- [ ] `last_accessed_at` updated on every chunk citation in `execute_task()`
- [ ] Nightly health score cron registered in Inngest
- [ ] `HealthBadge` column in document table
- [ ] Knowledge Base Health admin page with at-risk list
- [ ] Weekly digest email extended with health summary
- [ ] `activity_feed` table created
- [ ] Activity logged for: generated_content, uploaded_doc, shared_output, used_template
- [ ] `GET /api/team/activity` respects `is_private` flag
- [ ] Privacy toggle in user profile settings
- [ ] Activity feed widget renders in sidebar/dashboard
- [ ] Document-ready toast fires when status transitions processing → ready

---

## Day 2 — Content Moderation, Conversation Templates, Meeting Prep, Document Versioning

**Goal:** The product is safe (moderation), powerful for recurring workflows (templates + meeting prep), and tracks document history (versioning).

**Features:** #79 Content Moderation, #70 Conversation Templates, #39 Meeting Prep Assistant, #68 Document Versioning

---

### #79 — Content Moderation

**Backend — `app/services/moderation.py`**

```python
import re
from enum import Enum

class ModerationResult(str, Enum):
    CLEAN = "clean"
    FLAGGED = "flagged"
    BLOCKED = "blocked"

# Prompt injection patterns — attempts to override system behavior
INJECTION_PATTERNS = [
    r'ignore (previous|all|prior|above) (instructions?|prompts?|rules?|context)',
    r'disregard (your|the|all) (instructions?|system|rules?)',
    r'(you are|act as|pretend to be|roleplay as) (a|an)? (different|evil|unrestricted|jailbroken)',
    r'(new|updated|revised) (system|instructions?|rules?|prompt):',
    r'DAN (mode|prompt|jailbreak)',
    r'developer mode',
    r'bypass (your|all|the) (filter|restriction|safety|guideline)',
    r'forget (everything|your|all|prior) (you|instructions?|rules?)',
    r'(print|output|reveal|show) (your|the) (system prompt|instructions?|training)',
    r'translate.*to.*language.*and.*back',  # Classic evasion
    r'base64.{0,30}(decode|encode)',         # Encoding evasion
]

# Off-policy content patterns — flag but don't necessarily block
SUSPICIOUS_PATTERNS = [
    r'(how to|steps? to).{0,30}(hack|exploit|break into|bypass)',
    r'(generate|write|create).{0,30}(malware|virus|exploit)',
    r'(personal|private|confidential) (data|information).{0,30}(of|about).{0,30}(users?|employees?|customers?)',
]

def moderate_input(query: str) -> tuple[ModerationResult, str | None]:
    """
    Returns (result, reason).
    BLOCKED: reject immediately, don't send to LLM.
    FLAGGED: log and proceed (soft flag).
    CLEAN: proceed normally.
    """
    q_lower = query.lower().strip()

    # Check injection patterns — hard block
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, q_lower, re.IGNORECASE):
            return ModerationResult.BLOCKED, f"Prompt injection detected: {pattern}"

    # Check suspicious patterns — soft flag only
    for pattern in SUSPICIOUS_PATTERNS:
        if re.search(pattern, q_lower, re.IGNORECASE):
            return ModerationResult.FLAGGED, f"Suspicious pattern: {pattern}"

    return ModerationResult.CLEAN, None
```

**Database — Moderation log table:**

```sql
CREATE TABLE moderation_logs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  user_id UUID REFERENCES users(id) ON DELETE SET NULL,
  query_text TEXT NOT NULL,
  result VARCHAR(20) NOT NULL,     -- 'blocked' | 'flagged'
  reason TEXT,
  action_taken VARCHAR(50),        -- 'rejected' | 'logged_and_proceeded'
  reviewed_by UUID REFERENCES users(id),
  reviewed_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_moderation_logs_org ON moderation_logs(org_id, created_at DESC);
CREATE INDEX idx_moderation_logs_result ON moderation_logs(org_id, result, created_at DESC);
```

**Wire into `execute_task()` — before ANY other processing:**

```python
async def execute_task(query: str, org_id: str, user_id: str, ...):
    # STEP 0: Moderate input — runs before vector search or LLM call
    result, reason = moderate_input(query)

    if result == ModerationResult.BLOCKED:
        # Log to moderation_logs
        db.add(ModerationLog(
            org_id=org_id,
            user_id=user_id,
            query_text=query[:500],
            result='blocked',
            reason=reason,
            action_taken='rejected'
        ))
        await db.commit()
        # Return a safe refusal via SSE
        yield f"data: {json.dumps({'type': 'moderation_block', 'message': 'This query was blocked by content moderation.'})}\n\n"
        return

    if result == ModerationResult.FLAGGED:
        # Log but proceed
        db.add(ModerationLog(
            org_id=org_id,
            user_id=user_id,
            query_text=query[:500],
            result='flagged',
            reason=reason,
            action_taken='logged_and_proceeded'
        ))
        await db.commit()
        # Continue to normal processing

    # ... rest of execute_task ...
```

**Frontend — Handle `moderation_block` SSE event:**

```tsx
// In useChat.ts SSE handler
if (event.type === 'moderation_block') {
  setMessages(prev => [...prev, {
    role: 'assistant',
    content: '⚠️ This query was blocked by content moderation. If you believe this is an error, please contact your administrator.',
    isModerated: true
  }])
  return
}
```

Render moderated messages with an amber border and shield icon instead of the normal assistant message style.

**Admin — `app/admin/moderation/page.tsx`:**

```tsx
export default function ModerationPage() {
  const [filter, setFilter] = useState<'all' | 'blocked' | 'flagged'>('all')
  const { data } = useQuery(['moderation-logs', filter], () =>
    fetch(`/api/admin/moderation?result=${filter}`).then(r => r.json())
  )

  return (
    <div className="max-w-4xl mx-auto px-4 py-6">
      <h1 className="text-xl font-semibold mb-4">Content Moderation</h1>

      {/* Filter tabs */}
      <div className="flex gap-2 mb-4">
        {['all', 'blocked', 'flagged'].map(f => (
          <button key={f} onClick={() => setFilter(f as any)}
            className={cn("px-3 py-1.5 rounded-lg text-sm capitalize",
              filter === f ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground")}>
            {f}
          </button>
        ))}
      </div>

      {/* Logs table */}
      <div className="space-y-2">
        {(data || []).map((log: any) => (
          <div key={log.id} className="border rounded-lg px-4 py-3">
            <div className="flex items-start justify-between gap-3">
              <div className="flex-1 min-w-0">
                <p className="text-sm truncate font-mono bg-muted px-2 py-0.5 rounded text-xs">
                  {log.query_text}
                </p>
                <p className="text-xs text-muted-foreground mt-1">{log.reason}</p>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <span className={cn(
                  "text-xs px-2 py-0.5 rounded-full",
                  log.result === 'blocked'
                    ? "bg-red-100 text-red-700"
                    : "bg-amber-100 text-amber-700"
                )}>
                  {log.result}
                </span>
                <span className="text-xs text-muted-foreground">
                  {formatRelativeTime(log.created_at)}
                </span>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
```

**`GET /api/admin/moderation`** endpoint:
```python
@router.get("/admin/moderation")
async def get_moderation_logs(
    result: Optional[str] = None,  # 'blocked' | 'flagged' | None (all)
    limit: int = 50,
    current_user: User = Depends(get_admin_user),
    ...
):
    query = select(ModerationLog).where(ModerationLog.org_id == current_user.org_id)
    if result and result != 'all':
        query = query.where(ModerationLog.result == result)
    query = query.order_by(ModerationLog.created_at.desc()).limit(limit)
    return await db.execute(query)
```

---

### #70 — Conversation Templates

Conversation templates differ from prompt templates (V3 #20) in that they save the full conversation flow structure, not just a single prompt.

**Database Migration:**

```sql
CREATE TABLE conversation_templates (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  created_by UUID NOT NULL REFERENCES users(id),
  title VARCHAR(255) NOT NULL,
  description TEXT,
  -- Stores the initial prompt with {{variable}} placeholders
  initial_prompt TEXT NOT NULL,
  -- JSON array of variable definitions: [{name, label, placeholder, required}]
  variables JSONB NOT NULL DEFAULT '[]',
  is_shared BOOLEAN NOT NULL DEFAULT false,
  use_count INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_conversation_templates_org ON conversation_templates(org_id);
```

Variable schema stored in `variables` JSONB:
```json
[
  { "name": "client_name", "label": "Client Name", "placeholder": "Acme Corp", "required": true },
  { "name": "date", "label": "Date", "placeholder": "Q1 2024", "required": false },
  { "name": "product_name", "label": "Product", "placeholder": "Company Brain", "required": true }
]
```

**Backend — `app/api/routes/conversation_templates.py`:**

```python
# GET /api/conversation-templates — list templates for org
@router.get("/conversation-templates")
async def list_conversation_templates(current_user: User = Depends(get_current_user), ...):
    return await db.execute(
        select(ConversationTemplate).where(
            or_(
                and_(ConversationTemplate.org_id == current_user.org_id,
                     ConversationTemplate.is_shared == True),
                ConversationTemplate.created_by == current_user.id
            )
        ).order_by(ConversationTemplate.use_count.desc())
    )

# POST /api/conversation-templates — create
@router.post("/conversation-templates")
async def create_conversation_template(body: CreateConversationTemplateBody, ...):
    # Extract {{variable}} placeholders from initial_prompt automatically
    import re
    found_vars = re.findall(r'\{\{(\w+)\}\}', body.initial_prompt)
    auto_variables = [
        {"name": v, "label": v.replace('_', ' ').title(), "placeholder": "", "required": True}
        for v in dict.fromkeys(found_vars)  # dedupe preserving order
    ]
    template = ConversationTemplate(
        org_id=current_user.org_id,
        created_by=current_user.id,
        variables=body.variables or auto_variables,
        **body.dict(exclude={'variables'})
    )
    db.add(template)
    await db.commit()
    return template

# POST /api/conversation-templates/{id}/use — resolve variables and create conversation
@router.post("/conversation-templates/{template_id}/use")
async def use_conversation_template(
    template_id: str,
    body: UseTemplateBody,  # { variable_values: { client_name: "Acme", ... } }
    ...
):
    template = await db.get(ConversationTemplate, template_id)

    # Substitute {{variable}} with provided values
    resolved_prompt = template.initial_prompt
    for var_name, var_value in body.variable_values.items():
        resolved_prompt = resolved_prompt.replace(f"{{{{{var_name}}}}}", var_value)

    # Check no unresolved required variables remain
    remaining = re.findall(r'\{\{(\w+)\}\}', resolved_prompt)
    required_vars = {v['name'] for v in template.variables if v.get('required')}
    unresolved_required = required_vars & set(remaining)
    if unresolved_required:
        raise HTTPException(400, f"Missing required variables: {', '.join(unresolved_required)}")

    # Create new conversation pre-seeded with resolved prompt
    conversation = Conversation(
        org_id=current_user.org_id,
        user_id=current_user.id,
        title=f"{template.title} — {datetime.utcnow().strftime('%b %d')}"
    )
    db.add(conversation)

    # Increment use count
    await db.execute(
        update(ConversationTemplate)
        .where(ConversationTemplate.id == template_id)
        .values(use_count=ConversationTemplate.use_count + 1)
    )
    await db.commit()

    return {
        "conversation_id": str(conversation.id),
        "resolved_prompt": resolved_prompt
    }
```

**Frontend — "Save as Conversation Template" button**

On the conversation header (next to Export and Share buttons):

```tsx
<button onClick={() => setSaveTemplateOpen(true)}
  className="p-2 rounded hover:bg-muted text-muted-foreground"
  title="Save as template">
  <BookmarkPlus size={16} />
</button>
```

**`components/conversation-templates/SaveTemplateDialog.tsx`:**

```tsx
export function SaveTemplateDialog({
  open, onClose, initialPrompt
}: {
  open: boolean
  onClose: () => void
  initialPrompt: string  // Pre-filled from the first user message
}) {
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [prompt, setPrompt] = useState(initialPrompt)
  const [isShared, setIsShared] = useState(false)

  // Auto-detect variables from prompt
  const detectedVars = [...new Set(prompt.match(/\{\{(\w+)\}\}/g) || [])]

  const save = async () => {
    await fetch('/api/conversation-templates', {
      method: 'POST',
      body: JSON.stringify({ title, description, initial_prompt: prompt, is_shared: isShared }),
      headers: { 'Content-Type': 'application/json' }
    })
    onClose()
    toast.success('Template saved!')
  }

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Save as Conversation Template</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          <div>
            <label className="text-sm font-medium">Title</label>
            <Input value={title} onChange={e => setTitle(e.target.value)} placeholder="Weekly Status Update" />
          </div>
          <div>
            <label className="text-sm font-medium">Template prompt</label>
            <p className="text-xs text-muted-foreground mb-1">
              Use {'{{variable_name}}'} for parts that change each time.
            </p>
            <Textarea value={prompt} onChange={e => setPrompt(e.target.value)} rows={5} />
            {detectedVars.length > 0 && (
              <p className="text-xs text-muted-foreground mt-1">
                Detected variables: {detectedVars.join(', ')}
              </p>
            )}
          </div>
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm font-medium">Share with team</p>
              <p className="text-xs text-muted-foreground">Make visible to all org members</p>
            </div>
            <Switch checked={isShared} onCheckedChange={setIsShared} />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button onClick={save} disabled={!title}>Save Template</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
```

**"New from template" flow — `components/conversation-templates/UseTemplateDialog.tsx`:**

```tsx
export function UseTemplateDialog({ template, open, onClose }: {
  template: ConversationTemplate
  open: boolean
  onClose: () => void
}) {
  const router = useRouter()
  const [values, setValues] = useState<Record<string, string>>({})

  const start = async () => {
    const { conversation_id, resolved_prompt } = await fetch(
      `/api/conversation-templates/${template.id}/use`,
      {
        method: 'POST',
        body: JSON.stringify({ variable_values: values }),
        headers: { 'Content-Type': 'application/json' }
      }
    ).then(r => r.json())

    // Navigate to new conversation with pre-filled input
    router.push(`/chat/${conversation_id}?prefill=${encodeURIComponent(resolved_prompt)}`)
    onClose()
  }

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{template.title}</DialogTitle>
          {template.description && (
            <DialogDescription>{template.description}</DialogDescription>
          )}
        </DialogHeader>
        <div className="space-y-3">
          {template.variables.map((v: any) => (
            <div key={v.name}>
              <label className="text-sm font-medium">
                {v.label}{v.required && <span className="text-destructive ml-0.5">*</span>}
              </label>
              <Input
                value={values[v.name] || ''}
                onChange={e => setValues(prev => ({ ...prev, [v.name]: e.target.value }))}
                placeholder={v.placeholder}
              />
            </div>
          ))}
          {template.variables.length === 0 && (
            <p className="text-sm text-muted-foreground">No variables to fill in.</p>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button onClick={start}>Start conversation →</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
```

**Templates gallery page — `app/templates/page.tsx`**

A dedicated page (link in sidebar) showing all conversation templates, organized by shared vs. personal:

```
Templates

[Shared with Team]                        [My Templates]
📋 Weekly Status Update     [Use →]       📋 Client Proposal      [Use →]
   by Admin · used 12x                       private · used 3x
📋 Monthly All-Hands Summary [Use →]      [+ Create Template]
   by Admin · used 8x
```

---

### #39 — Meeting Prep Assistant

**Route: `app/chat/meeting-prep/page.tsx`**

```tsx
'use client'
import { useState } from 'react'
import { useRouter } from 'next/navigation'

const MEETING_TYPES = [
  'Investor Meeting', 'Client Pitch', 'Team All-Hands',
  'One-on-One', 'Board Meeting', 'Sales Call',
  'Performance Review', 'Project Kickoff', 'Other'
]

export default function MeetingPrepPage() {
  const router = useRouter()
  const [form, setForm] = useState({
    meetingType: '',
    attendees: '',
    topics: '',
    date: '',
    additionalContext: ''
  })
  const [loading, setLoading] = useState(false)

  const generateBrief = async () => {
    setLoading(true)
    const { conversation_id } = await fetch('/api/chat/meeting-prep', {
      method: 'POST',
      body: JSON.stringify(form),
      headers: { 'Content-Type': 'application/json' }
    }).then(r => r.json())
    router.push(`/chat/${conversation_id}`)
  }

  return (
    <div className="max-w-2xl mx-auto px-4 py-8">
      {/* Header */}
      <div className="mb-8">
        <div className="flex items-center gap-2 mb-2">
          <CalendarDays size={22} className="text-indigo-500" />
          <h1 className="text-2xl font-semibold">Meeting Prep Assistant</h1>
        </div>
        <p className="text-muted-foreground">
          Get a structured brief for your meeting in seconds, grounded in your knowledge base.
        </p>
      </div>

      {/* Form */}
      <div className="space-y-5">
        {/* Meeting type */}
        <div>
          <label className="text-sm font-medium mb-1.5 block">Meeting type</label>
          <div className="flex flex-wrap gap-2">
            {MEETING_TYPES.map(type => (
              <button
                key={type}
                onClick={() => setForm(f => ({ ...f, meetingType: type }))}
                className={cn(
                  "px-3 py-1.5 rounded-lg border text-sm transition-colors",
                  form.meetingType === type
                    ? "bg-indigo-600 text-white border-indigo-600"
                    : "border-border hover:border-indigo-300 text-foreground"
                )}
              >
                {type}
              </button>
            ))}
          </div>
        </div>

        {/* Attendees */}
        <div>
          <label className="text-sm font-medium mb-1.5 block">
            Who's attending?
            <span className="font-normal text-muted-foreground ml-1">(names, roles, or company)</span>
          </label>
          <Input
            value={form.attendees}
            onChange={e => setForm(f => ({ ...f, attendees: e.target.value }))}
            placeholder="e.g. Sarah Chen (CTO at Acme Corp), our VP Sales"
          />
        </div>

        {/* Key topics */}
        <div>
          <label className="text-sm font-medium mb-1.5 block">Key topics to cover</label>
          <Textarea
            value={form.topics}
            onChange={e => setForm(f => ({ ...f, topics: e.target.value }))}
            placeholder="e.g. Q3 roadmap review, pricing discussion, integration timeline"
            rows={3}
          />
        </div>

        {/* Date */}
        <div>
          <label className="text-sm font-medium mb-1.5 block">Meeting date</label>
          <Input
            type="date"
            value={form.date}
            onChange={e => setForm(f => ({ ...f, date: e.target.value }))}
          />
        </div>

        {/* Additional context */}
        <div>
          <label className="text-sm font-medium mb-1.5 block">
            Additional context
            <span className="font-normal text-muted-foreground ml-1">(optional)</span>
          </label>
          <Textarea
            value={form.additionalContext}
            onChange={e => setForm(f => ({ ...f, additionalContext: e.target.value }))}
            placeholder="Any specific concerns, recent history, or context the AI should know..."
            rows={2}
          />
        </div>

        <Button
          onClick={generateBrief}
          disabled={!form.meetingType || !form.topics || loading}
          className="w-full"
          size="lg"
        >
          {loading ? (
            <><Loader2 size={16} className="mr-2 animate-spin" /> Generating your brief...</>
          ) : (
            <><Sparkles size={16} className="mr-2" /> Generate Meeting Brief</>
          )}
        </Button>
      </div>
    </div>
  )
}
```

**Backend — `POST /api/chat/meeting-prep`:**

```python
MEETING_PREP_SYSTEM_PROMPT = """You are an expert meeting preparation assistant with access to the organization's knowledge base.
Generate a comprehensive, structured meeting brief using the available context.

Format the brief with these exact sections:
## Meeting Overview
## Background on Attendees / Organization
## Key Talking Points  
## Anticipated Questions & Suggested Answers
## Relevant Numbers & Metrics
## Recommended Next Steps

Be specific and grounded in the knowledge base context.
If information about specific attendees or their company isn't in the knowledge base, note this clearly.
Keep each section concise but comprehensive — this is a working document, not an essay."""

@router.post("/chat/meeting-prep")
async def generate_meeting_prep(
    body: MeetingPrepBody,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    # Build the meeting prep query from form fields
    query = f"""Prepare a meeting brief for the following:

Meeting Type: {body.meeting_type}
Date: {body.date or 'Not specified'}
Attendees: {body.attendees}
Key Topics: {body.topics}
{f'Additional Context: {body.additional_context}' if body.additional_context else ''}

Search the knowledge base for anything relevant to these attendees, topics, and our company's position on these subjects."""

    # Create a new conversation for this brief
    conversation = Conversation(
        org_id=current_user.org_id,
        user_id=current_user.id,
        title=f"Meeting Prep: {body.meeting_type} — {body.attendees[:30]}",
        metadata={"source": "meeting_prep", "form": body.dict()}
    )
    db.add(conversation)
    await db.commit()

    # Create the user message
    user_message = Message(
        conversation_id=conversation.id,
        role='user',
        content=query
    )
    db.add(user_message)
    await db.commit()

    # Trigger the execute_task with the meeting prep system prompt override
    await inngest.send("chat/execute-task", {
        "conversation_id": str(conversation.id),
        "message_id": str(user_message.id),
        "query": query,
        "system_prompt_override": MEETING_PREP_SYSTEM_PROMPT,
        "org_id": str(current_user.org_id),
        "user_id": str(current_user.id)
    })

    return { "conversation_id": str(conversation.id) }
```

The frontend navigates to `/chat/{conversation_id}` immediately — the user sees the brief streaming in as the Inngest job processes it.

**Add Meeting Prep to sidebar navigation:**

```tsx
// In sidebar nav
<Link href="/chat/meeting-prep"
  className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm hover:bg-muted">
  <CalendarDays size={16} />
  Meeting Prep
</Link>
```

---

### #68 — Document Versioning

**Database Migration:**

```sql
CREATE TABLE document_versions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  version_number INTEGER NOT NULL,
  file_path TEXT NOT NULL,          -- Storage key for this version's file
  file_size_kb INTEGER,
  uploaded_by UUID NOT NULL REFERENCES users(id),
  is_current BOOLEAN NOT NULL DEFAULT false,
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  UNIQUE(document_id, version_number)
);

CREATE INDEX idx_document_versions_doc ON document_versions(document_id, version_number DESC);
```

Also add to `chunks` table to know which version chunks came from:
```sql
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS document_version_id UUID REFERENCES document_versions(id);
```

And to `messages` to know which version was cited:
```sql
-- In chunk_citations, the chunk already has version_id — no change needed
-- Add version info to citation metadata for display
```

**Backend — "Upload New Version" endpoint:**

```python
@router.post("/documents/{document_id}/versions")
async def upload_new_version(
    document_id: str,
    file: UploadFile,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    doc = await db.get(Document, document_id)
    if not doc or doc.org_id != current_user.org_id:
        raise HTTPException(403)

    # Get current version number
    latest_version = await db.scalar(
        select(func.max(DocumentVersion.version_number))
        .where(DocumentVersion.document_id == document_id)
    ) or 0
    new_version_number = latest_version + 1

    # Upload new file to storage
    file_path = await upload_to_storage(file, org_id=current_user.org_id)

    # Soft-delete old chunks (archive them, don't hard-delete)
    await db.execute(
        update(Chunk)
        .where(Chunk.document_id == document_id, Chunk.is_archived == False)
        .values(is_archived=True)
    )

    # Mark previous versions as not current
    await db.execute(
        update(DocumentVersion)
        .where(DocumentVersion.document_id == document_id)
        .values(is_current=False)
    )

    # Create new version record
    version = DocumentVersion(
        document_id=document_id,
        version_number=new_version_number,
        file_path=file_path,
        file_size_kb=file.size // 1024 if file.size else None,
        uploaded_by=current_user.id,
        is_current=True
    )
    db.add(version)

    # Reset document to processing state
    doc.status = 'processing'
    doc.file_path = file_path
    await db.commit()

    # Trigger re-processing
    await inngest.send("document/process", {
        "document_id": document_id,
        "version_id": str(version.id)
    })

    return { "version": new_version_number, "status": "processing" }
```

Add `is_archived` to chunks:
```sql
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS is_archived BOOLEAN NOT NULL DEFAULT false;
```

Update vector search to exclude archived chunks:
```python
# In hybrid_search
.where(Chunk.is_archived == False)  # ADD THIS filter
```

**`GET /api/documents/{id}/versions`** — Version history endpoint:

```python
@router.get("/documents/{document_id}/versions")
async def get_document_versions(document_id: str, ...):
    versions = await db.execute(
        select(DocumentVersion, User.name.label('uploader_name'))
        .join(User, User.id == DocumentVersion.uploaded_by)
        .where(DocumentVersion.document_id == document_id)
        .order_by(DocumentVersion.version_number.desc())
    )
    return versions.all()
```

**Frontend — Version history in document detail view:**

Add a "Versions" tab or expandable section on the document row:

```tsx
export function DocumentVersionHistory({ documentId }: { documentId: string }) {
  const { data: versions } = useQuery(
    ['document-versions', documentId],
    () => fetch(`/api/documents/${documentId}/versions`).then(r => r.json())
  )

  return (
    <div className="space-y-1 mt-2">
      <p className="text-xs font-medium text-muted-foreground">Version history</p>
      {(versions || []).map((v: any) => (
        <div key={v.id} className="flex items-center gap-2 text-xs">
          <span className={cn(
            "font-mono px-1.5 py-0.5 rounded",
            v.is_current ? "bg-indigo-100 text-indigo-700" : "bg-muted text-muted-foreground"
          )}>
            v{v.version_number}
          </span>
          <span className="text-muted-foreground">
            uploaded by {v.uploader_name} · {formatRelativeTime(v.created_at)}
          </span>
          {v.is_current && <Badge size="xs" variant="outline">Current</Badge>}
        </div>
      ))}
    </div>
  )
}
```

**"Upload New Version" button** on each document row (admin only):

```tsx
{isAdmin && document.status === 'ready' && (
  <label className="cursor-pointer">
    <input
      type="file"
      className="hidden"
      accept=".pdf,.docx,.txt,.xlsx,.pptx,.csv"
      onChange={e => e.target.files && uploadNewVersion(document.id, e.target.files[0])}
    />
    <span className="text-xs text-indigo-600 hover:underline flex items-center gap-1">
      <UploadCloud size={12} /> New version
    </span>
  </label>
)}
```

Citation version display — update `CitationCard` to show version:
```tsx
{citation.version_number && (
  <span className="text-muted-foreground text-xs">v{citation.version_number}</span>
)}
```

**End of Day 2 checklist:**
- [ ] `moderation_logs` table created
- [ ] `moderate_input()` covers all injection + suspicious patterns
- [ ] Moderation runs as STEP 0 in `execute_task()` before any processing
- [ ] Blocked queries return SSE `moderation_block` event, no LLM call made
- [ ] Flagged queries logged and proceed normally
- [ ] Frontend renders moderated messages with amber border + shield icon
- [ ] Admin moderation log page with blocked/flagged filter
- [ ] `conversation_templates` table created, `{{variable}}` extraction works
- [ ] CRUD routes for conversation templates working
- [ ] Variable resolution with required-field validation
- [ ] Save-as-template dialog on conversation header
- [ ] Use-template dialog with per-variable inputs
- [ ] Templates gallery page in sidebar nav
- [ ] Meeting Prep page at `/chat/meeting-prep` with full form
- [ ] `POST /api/chat/meeting-prep` creates conversation and fires Inngest job
- [ ] Meeting prep system prompt produces structured 6-section brief
- [ ] Meeting Prep link in sidebar nav
- [ ] `document_versions` table, `chunks.is_archived`, `chunks.document_version_id`
- [ ] "Upload new version" soft-archives old chunks, creates version record
- [ ] Re-processing triggered automatically on new version
- [ ] Vector search excludes archived chunks
- [ ] Version history shown on document row
- [ ] Citation cards show version number

---

## Day 3 — Chrome Extension

**Goal:** Company Brain is available anywhere in Chrome — Gmail, Notion, Lever, Google Docs — as a 400px sidebar that slides in from the right. "Add to Brain" scrapes the current page and ingests it as a document.

**Feature:** #32 Chrome Extension (Manifest V3, iframe embed)

---

### Setup: Extension Project Structure

```
extensions/chrome/
├── manifest.json
├── background.js          # Service worker (Manifest V3)
├── content.js             # Injected into every page
├── content.css            # Sidebar iframe styles
├── popup/
│   ├── popup.html         # Extension toolbar popup
│   └── popup.js
└── icons/
    ├── icon16.png
    ├── icon48.png
    └── icon128.png
```

All icons: simple brain icon in indigo (`#6366f1`) on white background. Export at 16×16, 48×48, 128×128.

---

### `manifest.json`

```json
{
  "manifest_version": 3,
  "name": "Company Brain",
  "version": "1.0.0",
  "description": "Access your Company Brain knowledge base from any webpage",
  "permissions": [
    "activeTab",
    "storage",
    "scripting"
  ],
  "host_permissions": [
    "https://companybrain.app/*",
    "<all_urls>"
  ],
  "background": {
    "service_worker": "background.js"
  },
  "content_scripts": [
    {
      "matches": ["<all_urls>"],
      "js": ["content.js"],
      "css": ["content.css"],
      "run_at": "document_end"
    }
  ],
  "action": {
    "default_popup": "popup/popup.html",
    "default_icon": {
      "16": "icons/icon16.png",
      "48": "icons/icon48.png",
      "128": "icons/icon128.png"
    },
    "default_title": "Open Company Brain"
  },
  "web_accessible_resources": [
    {
      "resources": ["icons/*"],
      "matches": ["<all_urls>"]
    }
  ]
}
```

---

### `background.js` — Service Worker

```javascript
// Manifest V3 service worker
const APP_URL = 'https://companybrain.app'

// Toggle sidebar when extension icon is clicked
chrome.action.onClicked.addListener(async (tab) => {
  try {
    await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: toggleSidebar,
      args: [APP_URL]
    })
  } catch (err) {
    console.error('Failed to toggle sidebar:', err)
  }
})

// Listen for messages from content script
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === 'GET_PAGE_CONTENT') {
    // Relay page content to the iframe
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      sendResponse({ url: tabs[0]?.url, title: tabs[0]?.title })
    })
    return true // Required for async sendResponse
  }
})

function toggleSidebar(appUrl) {
  const SIDEBAR_ID = 'companybrain-sidebar-container'
  const existing = document.getElementById(SIDEBAR_ID)
  if (existing) {
    existing.style.display = existing.style.display === 'none' ? 'flex' : 'none'
    return
  }
  // Create sidebar if it doesn't exist yet
  // (This is called via executeScript, so it runs in page context)
  createSidebar(appUrl)
}
```

---

### `content.js` — Sidebar Injection

```javascript
const APP_URL = 'https://companybrain.app'
const SIDEBAR_ID = 'companybrain-sidebar-container'
const IFRAME_ID = 'companybrain-iframe'

function createSidebar(appUrl) {
  // Container
  const container = document.createElement('div')
  container.id = SIDEBAR_ID
  container.style.cssText = `
    position: fixed;
    top: 0;
    right: 0;
    width: 400px;
    height: 100vh;
    z-index: 2147483647;
    display: flex;
    flex-direction: column;
    box-shadow: -4px 0 24px rgba(0,0,0,0.12);
    border-left: 1px solid rgba(0,0,0,0.1);
    background: white;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  `

  // Header bar (close button + page context indicator)
  const header = document.createElement('div')
  header.style.cssText = `
    display: flex;
    align-items: center;
    padding: 10px 12px;
    border-bottom: 1px solid #e5e7eb;
    background: #f9fafb;
    gap: 8px;
  `
  header.innerHTML = `
    <div style="flex:1; display:flex; align-items:center; gap:8px;">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
        <circle cx="12" cy="12" r="10" fill="#6366f1" opacity="0.15"/>
        <circle cx="12" cy="12" r="5" fill="#6366f1"/>
      </svg>
      <span style="font-size:13px; font-weight:600; color:#111827;">Company Brain</span>
    </div>
    <button id="cb-add-to-brain" style="
      font-size:11px; padding:4px 10px; background:#6366f1; color:white;
      border:none; border-radius:6px; cursor:pointer; font-weight:500;
    " title="Add this page to your knowledge base">
      + Add to Brain
    </button>
    <button id="cb-close-btn" style="
      background:none; border:none; cursor:pointer; padding:4px; color:#6b7280;
      font-size:18px; line-height:1;
    ">×</button>
  `
  container.appendChild(header)

  // Page context badge
  const contextBadge = document.createElement('div')
  contextBadge.id = 'cb-context-badge'
  contextBadge.style.cssText = `
    padding: 6px 12px;
    background: #eff6ff;
    border-bottom: 1px solid #dbeafe;
    font-size: 11px;
    color: #1d4ed8;
    display: flex;
    align-items: center;
    gap: 6px;
    cursor: pointer;
  `
  contextBadge.innerHTML = `
    <span>📄</span>
    <span id="cb-page-title" style="flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">
      ${document.title || window.location.hostname}
    </span>
    <span style="font-size:10px; opacity:0.7; white-space:nowrap;">Click to use as context</span>
  `
  container.appendChild(contextBadge)

  // Iframe — loads the full Next.js chat UI
  const iframe = document.createElement('iframe')
  iframe.id = IFRAME_ID
  iframe.src = `${appUrl}/chat/new?embedded=true`  // ?embedded=true hides sidebar nav
  iframe.style.cssText = `
    flex: 1;
    border: none;
    width: 100%;
    background: white;
  `
  iframe.allow = 'clipboard-write'  // For copy button
  container.appendChild(iframe)

  document.body.appendChild(container)

  // Push page content right so it doesn't get covered
  document.body.style.marginRight = '400px'
  document.body.style.transition = 'margin-right 0.2s ease'

  // Close button
  document.getElementById('cb-close-btn').addEventListener('click', () => {
    container.style.display = 'none'
    document.body.style.marginRight = '0'
  })

  // "Add to Brain" button
  document.getElementById('cb-add-to-brain').addEventListener('click', () => {
    addPageToBrain(iframe, appUrl)
  })

  // Page context badge — click sends page URL to iframe as context
  contextBadge.addEventListener('click', () => {
    iframe.contentWindow?.postMessage({
      type: 'CB_USE_PAGE_CONTEXT',
      url: window.location.href,
      title: document.title,
    }, appUrl)
    contextBadge.style.background = '#dcfce7'
    contextBadge.style.color = '#15803d'
    contextBadge.querySelector('#cb-page-title').textContent = '✓ Using this page as context'
  })
}

async function addPageToBrain(iframe, appUrl) {
  // Extract readable text from current page
  const btn = document.getElementById('cb-add-to-brain')
  btn.textContent = 'Adding...'
  btn.disabled = true

  // Get page text content (remove scripts, styles, nav)
  const clone = document.body.cloneNode(true)
  clone.querySelectorAll('script, style, nav, footer, header, [role="navigation"]').forEach(el => el.remove())
  const text = clone.innerText?.slice(0, 50000) || ''  // Cap at 50k chars

  const pageData = {
    url: window.location.href,
    title: document.title,
    content: text,
    hostname: window.location.hostname
  }

  // Send to iframe which will call the API (iframe has auth session)
  iframe.contentWindow?.postMessage({
    type: 'CB_ADD_PAGE',
    data: pageData
  }, appUrl)

  // Listen for confirmation
  window.addEventListener('message', (event) => {
    if (event.origin !== appUrl) return
    if (event.data.type === 'CB_ADD_PAGE_SUCCESS') {
      btn.textContent = '✓ Added!'
      btn.style.background = '#16a34a'
      setTimeout(() => {
        btn.textContent = '+ Add to Brain'
        btn.style.background = '#6366f1'
        btn.disabled = false
      }, 2500)
    }
  }, { once: true })
}
```

---

### `content.css`

```css
#companybrain-sidebar-container {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif !important;
  box-sizing: border-box;
}

#companybrain-sidebar-container * {
  box-sizing: border-box;
}
```

---

### `popup/popup.html` + `popup/popup.js`

The popup is minimal — it opens when user clicks the toolbar icon (before content script has run):

```html
<!DOCTYPE html>
<html>
<head>
  <style>
    body { width: 280px; padding: 16px; font-family: -apple-system, sans-serif; margin: 0; }
    .logo { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; }
    .logo-dot { width: 20px; height: 20px; background: #6366f1; border-radius: 50%; }
    h1 { font-size: 15px; font-weight: 600; color: #111827; margin: 0; }
    p { font-size: 12px; color: #6b7280; margin: 0 0 16px; }
    .btn { display: block; width: 100%; padding: 10px; background: #6366f1; color: white;
           border: none; border-radius: 8px; cursor: pointer; font-size: 13px;
           font-weight: 500; text-align: center; margin-bottom: 8px; }
    .btn:hover { background: #4f46e5; }
    .btn-outline { background: transparent; border: 1px solid #e5e7eb; color: #374151; }
    .btn-outline:hover { background: #f9fafb; }
    .status { font-size: 11px; color: #9ca3af; text-align: center; margin-top: 8px; }
  </style>
</head>
<body>
  <div class="logo">
    <div class="logo-dot"></div>
    <h1>Company Brain</h1>
  </div>
  <p>Your AI knowledge base — available on every page.</p>
  <button class="btn" id="open-sidebar">Open Sidebar</button>
  <button class="btn btn-outline" id="open-app">Open Full App</button>
  <div class="status" id="status"></div>
  <script src="popup.js"></script>
</body>
</html>
```

```javascript
// popup.js
const APP_URL = 'https://companybrain.app'

document.getElementById('open-sidebar').addEventListener('click', async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true })
  // Inject content script if not already there, then toggle
  try {
    await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: (appUrl) => {
        // Same toggleSidebar logic
        const SIDEBAR_ID = 'companybrain-sidebar-container'
        const existing = document.getElementById(SIDEBAR_ID)
        if (existing) {
          const isHidden = existing.style.display === 'none'
          existing.style.display = isHidden ? 'flex' : 'none'
          document.body.style.marginRight = isHidden ? '400px' : '0'
        }
      },
      args: [APP_URL]
    })
  } catch (e) {
    document.getElementById('status').textContent = "Can't inject on this page"
  }
  window.close()
})

document.getElementById('open-app').addEventListener('click', () => {
  chrome.tabs.create({ url: APP_URL })
  window.close()
})
```

---

### Backend — Handle iframe `?embedded=true` mode

In the Next.js app, detect the `embedded` query param and hide sidebar nav:

```tsx
// app/chat/new/page.tsx (and layout)
const searchParams = useSearchParams()
const isEmbedded = searchParams.get('embedded') === 'true'

// In layout.tsx or chat layout:
<div className={cn("flex h-screen", isEmbedded && "overflow-hidden")}>
  {!isEmbedded && <Sidebar />}
  <main className="flex-1 flex flex-col">
    {children}
  </main>
</div>
```

The iframe loads the full Next.js chat at `https://companybrain.app/chat/new?embedded=true`. The user's existing session cookie is sent with the request (same origin as the app). No separate auth needed.

---

### Handle "Add to Brain" from iframe

In the Next.js app, listen for the `CB_ADD_PAGE` postMessage:

```tsx
// In app/chat/new/page.tsx (client component)
useEffect(() => {
  const handleMessage = async (event: MessageEvent) => {
    if (event.origin !== window.location.origin) return

    if (event.data.type === 'CB_ADD_PAGE') {
      const { url, title, content } = event.data.data
      // Create a document from the page content
      const response = await fetch('/api/documents/from-url', {
        method: 'POST',
        body: JSON.stringify({ url, title, content }),
        headers: { 'Content-Type': 'application/json' }
      })
      if (response.ok) {
        toast.success(`Added "${title}" to your knowledge base`)
        event.source?.postMessage({ type: 'CB_ADD_PAGE_SUCCESS' }, { targetOrigin: '*' })
      }
    }

    if (event.data.type === 'CB_USE_PAGE_CONTEXT') {
      // Pre-fill chat input with page context
      setChatContext({
        url: event.data.url,
        title: event.data.title
      })
      setInputValue(`Using context from: ${event.data.title}\n\n`)
    }
  }

  window.addEventListener('message', handleMessage)
  return () => window.removeEventListener('message', handleMessage)
}, [])
```

**Backend — `POST /api/documents/from-url`:**

```python
@router.post("/documents/from-url")
async def create_document_from_url(
    body: FromUrlBody,  # { url, title, content }
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    # Create document with text content (no file upload needed)
    doc = Document(
        org_id=current_user.org_id,
        name=body.title[:255] or body.url[:255],
        content=body.content,
        file_type='webpage',
        source_url=body.url,
        status='pending'
    )
    db.add(doc)
    await db.commit()

    # Trigger ingestion (text already extracted — skip file parsing)
    await inngest.send("document/process-text", {"document_id": str(doc.id)})
    return { "document_id": str(doc.id) }
```

Add `source_url TEXT` and `content TEXT` to documents table:
```sql
ALTER TABLE documents
  ADD COLUMN IF NOT EXISTS source_url TEXT,
  ADD COLUMN IF NOT EXISTS content TEXT;  -- Pre-extracted text (for webpage/email sources)
```

---

### Building and Publishing

**`package.json`** in `extensions/chrome/`:
```json
{
  "name": "company-brain-extension",
  "version": "1.0.0",
  "scripts": {
    "build": "echo 'No build step — pure JS/HTML' && exit 0",
    "zip": "zip -r company-brain-extension.zip . --exclude='*.zip' --exclude='node_modules/*'"
  }
}
```

**Publishing to Chrome Web Store:**
1. Go to `chrome.google.com/webstore/devconsole`
2. Pay one-time $5 developer fee
3. Create new item → upload `.zip`
4. Fill in: name, description, screenshots (1280×800 of sidebar in Gmail)
5. Category: Productivity
6. Submit for review (typically 1–3 business days)

**Local testing during development:**
1. Open `chrome://extensions`
2. Enable "Developer mode" (top right toggle)
3. Click "Load unpacked" → select `extensions/chrome/` directory
4. Extension appears in toolbar immediately

**End of Day 3 checklist:**
- [ ] Manifest V3 manifest.json with correct permissions
- [ ] Background service worker toggles sidebar via `executeScript`
- [ ] Content script creates 400px fixed sidebar with iframe
- [ ] Sidebar pushes page content right (400px margin)
- [ ] Close button hides sidebar, restores margin
- [ ] iframe loads `https://companybrain.app/chat/new?embedded=true`
- [ ] `?embedded=true` hides sidebar nav in Next.js app
- [ ] User's auth session works in iframe (same origin cookies)
- [ ] Page context badge sends `CB_USE_PAGE_CONTEXT` message to iframe
- [ ] iframe pre-fills chat input with page context on message receipt
- [ ] "Add to Brain" button extracts page text and sends `CB_ADD_PAGE` message
- [ ] `POST /api/documents/from-url` creates document from text content
- [ ] Success confirmation posted back, "Add to Brain" shows ✓
- [ ] Popup HTML with "Open Sidebar" + "Open Full App" buttons
- [ ] `source_url` and `content` columns on documents table
- [ ] Extension loads correctly via "Load unpacked" in developer mode
- [ ] Test on: Gmail, Notion, Google Docs, LinkedIn

---

## Day 4 — Help Center + Knowledge Base Coverage Score

**Goal:** Users can self-serve support. Admins know exactly what topics their knowledge base covers and what it's missing.

**Features:** #94 Help Center / In-App Docs, #74 Knowledge Base Coverage Score

---

### #94 — Help Center / In-App Docs

**Architecture decision:** Static MDX articles stored in the Next.js repo — no external CMS, no database. Content lives in `app/help/articles/` as `.mdx` files. Fast, versioned with code, zero external dependency.

**Install:**
```bash
npm install next-mdx-remote gray-matter
```

**Article structure — `app/help/articles/`:**

```
app/help/
├── articles/
│   ├── invite-teammates.mdx
│   ├── upload-documents.mdx
│   ├── supported-file-types.mdx
│   ├── how-search-works.mdx
│   ├── prompt-templates.mdx
│   ├── slack-integration.mdx
│   ├── api-access.mdx
│   ├── billing-and-plans.mdx
│   ├── dark-mode.mdx
│   └── troubleshooting.mdx
└── page.tsx         # Help center index
```

Each `.mdx` file has frontmatter:
```mdx
---
title: How do I invite a teammate?
category: Team & Access
order: 1
tags: [invite, team, users, access]
---

You can invite teammates from **Settings > Team**.

1. Click **Invite Member** in the top right
2. Enter their email address
3. Choose their role (Admin or Member)
4. Click **Send Invite**

They'll receive an email with a link to join your workspace. Invites expire after 7 days.

## Roles

**Admin** — Can manage documents, invite users, view analytics, and configure settings.

**Member** — Can chat, upload documents, and use templates.
```

**Article manifest — `app/help/articles/index.ts`**

```typescript
export const HELP_ARTICLES = [
  // Team & Access
  { slug: 'invite-teammates', title: 'How do I invite a teammate?', category: 'Team & Access', tags: ['invite', 'team'] },
  { slug: 'supported-file-types', title: 'What file types are supported?', category: 'Documents', tags: ['pdf', 'docx', 'upload', 'files'] },
  { slug: 'upload-documents', title: 'How do I upload documents?', category: 'Documents', tags: ['upload', 'documents', 'drag drop'] },
  { slug: 'how-search-works', title: 'How does the AI search my documents?', category: 'AI & Search', tags: ['search', 'ai', 'embeddings', 'rag'] },
  { slug: 'prompt-templates', title: 'Using prompt templates', category: 'Features', tags: ['templates', 'prompts', 'shortcuts'] },
  { slug: 'slack-integration', title: 'Setting up the Slack integration', category: 'Integrations', tags: ['slack', 'bot', 'slash command'] },
  { slug: 'api-access', title: 'Using the developer API', category: 'Integrations', tags: ['api', 'keys', 'developer'] },
  { slug: 'billing-and-plans', title: 'Billing and plan limits', category: 'Account', tags: ['billing', 'plans', 'quota', 'upgrade'] },
  { slug: 'dark-mode', title: 'How to enable dark mode', category: 'Account', tags: ['dark mode', 'theme', 'settings'] },
  { slug: 'troubleshooting', title: 'Troubleshooting document processing', category: 'Documents', tags: ['failed', 'retry', 'processing', 'error'] },
] as const

export const CATEGORIES = ['Team & Access', 'Documents', 'AI & Search', 'Features', 'Integrations', 'Account']
```

**`app/help/page.tsx`** — Help center index:

```tsx
'use client'
import { useState } from 'react'
import { HELP_ARTICLES, CATEGORIES } from './articles/index'
import Link from 'next/link'
import { Search, Book, ChevronRight } from 'lucide-react'

export default function HelpCenterPage() {
  const [search, setSearch] = useState('')

  const filtered = HELP_ARTICLES.filter(a =>
    search === '' ||
    a.title.toLowerCase().includes(search.toLowerCase()) ||
    a.tags.some(t => t.includes(search.toLowerCase()))
  )

  const groupedByCategory = CATEGORIES.reduce((acc, cat) => {
    acc[cat] = filtered.filter(a => a.category === cat)
    return acc
  }, {} as Record<string, typeof filtered>)

  return (
    <div className="max-w-3xl mx-auto px-4 py-8">
      {/* Header */}
      <div className="text-center mb-8">
        <div className="inline-flex items-center justify-center w-12 h-12 bg-indigo-100 rounded-xl mb-4">
          <Book size={22} className="text-indigo-600" />
        </div>
        <h1 className="text-2xl font-semibold mb-2">Help Center</h1>
        <p className="text-muted-foreground">Answers to common questions about Company Brain</p>
      </div>

      {/* Search */}
      <div className="relative mb-8">
        <Search size={16} className="absolute left-3 top-3 text-muted-foreground" />
        <input
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="Search help articles..."
          className="w-full pl-9 pr-4 py-2.5 border rounded-xl bg-background outline-none focus:ring-2 focus:ring-indigo-200 text-sm"
          autoFocus
        />
      </div>

      {/* Article list by category */}
      <div className="space-y-6">
        {CATEGORIES.map(cat => {
          const articles = groupedByCategory[cat]
          if (!articles?.length) return null
          return (
            <div key={cat}>
              <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">
                {cat}
              </p>
              <div className="border rounded-xl overflow-hidden divide-y">
                {articles.map(article => (
                  <Link
                    key={article.slug}
                    href={`/help/${article.slug}`}
                    className="flex items-center gap-3 px-4 py-3 hover:bg-muted/50 transition-colors"
                  >
                    <span className="flex-1 text-sm">{article.title}</span>
                    <ChevronRight size={14} className="text-muted-foreground shrink-0" />
                  </Link>
                ))}
              </div>
            </div>
          )
        })}
      </div>

      {/* Contact support */}
      <div className="mt-8 p-4 bg-muted rounded-xl text-center">
        <p className="text-sm text-muted-foreground">
          Can't find what you're looking for?{' '}
          <a href="mailto:support@companybrain.app"
            className="text-indigo-600 hover:underline font-medium">
            Contact support →
          </a>
        </p>
      </div>
    </div>
  )
}
```

**`app/help/[slug]/page.tsx`** — Individual article:

```tsx
import { promises as fs } from 'fs'
import path from 'path'
import matter from 'gray-matter'
import { MDXRemote } from 'next-mdx-remote/rsc'
import Link from 'next/link'
import { ChevronLeft } from 'lucide-react'

export default async function HelpArticlePage({ params }: { params: { slug: string } }) {
  const filePath = path.join(process.cwd(), 'app/help/articles', `${params.slug}.mdx`)
  const raw = await fs.readFile(filePath, 'utf-8')
  const { content, data: frontmatter } = matter(raw)

  return (
    <div className="max-w-2xl mx-auto px-4 py-8">
      {/* Back link */}
      <Link href="/help"
        className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground mb-6">
        <ChevronLeft size={14} /> Help Center
      </Link>

      {/* Article header */}
      <div className="mb-6">
        <span className="text-xs font-medium text-indigo-600 uppercase tracking-wider">
          {frontmatter.category}
        </span>
        <h1 className="text-xl font-semibold mt-1">{frontmatter.title}</h1>
      </div>

      {/* Article content */}
      <article className="prose prose-sm dark:prose-invert max-w-none
        prose-headings:font-semibold
        prose-a:text-indigo-600 prose-a:no-underline hover:prose-a:underline
        prose-code:bg-muted prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-code:text-sm">
        <MDXRemote source={content} />
      </article>

      {/* Feedback */}
      <div className="mt-8 pt-6 border-t">
        <p className="text-sm text-muted-foreground">Was this helpful?</p>
        <div className="flex gap-2 mt-2">
          <button className="px-4 py-1.5 text-sm border rounded-lg hover:bg-muted">👍 Yes</button>
          <button className="px-4 py-1.5 text-sm border rounded-lg hover:bg-muted">👎 No</button>
        </div>
      </div>
    </div>
  )
}

// Generate static pages for all articles at build time
export async function generateStaticParams() {
  const { HELP_ARTICLES } = await import('../articles/index')
  return HELP_ARTICLES.map(a => ({ slug: a.slug }))
}
```

**In-app help trigger — searchable from anywhere:**

Add a `?` help button in the sidebar footer:

```tsx
// In sidebar footer, next to theme toggle
<Link href="/help"
  className="p-2 rounded-md hover:bg-muted text-muted-foreground"
  title="Help Center">
  <HelpCircle size={16} />
</Link>
```

**Command palette integration** (if you have one, or add one with `cmdk`):

```tsx
// In command palette, add help articles as searchable items
const helpItems = HELP_ARTICLES.map(a => ({
  id: `help-${a.slug}`,
  label: a.title,
  group: 'Help',
  icon: <HelpCircle size={14} />,
  onSelect: () => router.push(`/help/${a.slug}`)
}))
```

---

### #74 — Knowledge Base Coverage Score

**Backend — `app/services/coverage.py`**

Auto-detect categories from uploaded documents and query patterns, then identify gaps.

```python
# Canonical question categories every business knowledge base should cover
CANONICAL_CATEGORIES = [
    {
        "id": "hr_policies",
        "label": "HR & People Policies",
        "description": "Vacation, leave, benefits, performance reviews",
        "detection_keywords": ["vacation", "leave", "pto", "benefits", "hr", "employee", "handbook", "onboarding", "performance"],
        "question_examples": ["What is our vacation policy?", "How do I request parental leave?"]
    },
    {
        "id": "product_overview",
        "label": "Product & Pricing",
        "description": "What you sell, how it's priced, feature list",
        "detection_keywords": ["product", "pricing", "plan", "feature", "tier", "subscription", "enterprise"],
        "question_examples": ["What does our product do?", "What are our pricing tiers?"]
    },
    {
        "id": "customer_support",
        "label": "Customer Support & Refunds",
        "description": "Refund policy, SLA, support process",
        "detection_keywords": ["refund", "return", "support", "sla", "ticket", "customer", "complaint", "chargeback"],
        "question_examples": ["What is our refund policy?", "What's our support SLA?"]
    },
    {
        "id": "sales_process",
        "label": "Sales Process",
        "description": "Sales playbook, objection handling, competitive positioning",
        "detection_keywords": ["sales", "prospect", "objection", "demo", "contract", "negotiation", "competitive"],
        "question_examples": ["What are common sales objections?", "How do we position against competitors?"]
    },
    {
        "id": "brand_voice",
        "label": "Brand & Communication",
        "description": "Tone of voice, brand guidelines, messaging",
        "detection_keywords": ["brand", "voice", "tone", "messaging", "style guide", "communication", "marketing"],
        "question_examples": ["What is our brand voice?", "How should we communicate with customers?"]
    },
    {
        "id": "legal_compliance",
        "label": "Legal & Compliance",
        "description": "Privacy policy, terms, compliance requirements",
        "detection_keywords": ["privacy", "gdpr", "compliance", "legal", "terms", "data protection", "security"],
        "question_examples": ["Where is customer data stored?", "Are we GDPR compliant?"]
    },
    {
        "id": "engineering_process",
        "label": "Engineering & Tech",
        "description": "Architecture docs, runbooks, tech stack",
        "detection_keywords": ["engineering", "architecture", "runbook", "api", "deployment", "infrastructure", "tech stack"],
        "question_examples": ["How do we deploy to production?", "What's our tech stack?"]
    },
    {
        "id": "finance_ops",
        "label": "Finance & Operations",
        "description": "Expense policy, invoicing, operational processes",
        "detection_keywords": ["expense", "invoice", "finance", "budget", "procurement", "vendor", "operations"],
        "question_examples": ["How do I submit an expense?", "What is our invoice process?"]
    },
]

async def compute_coverage_score(org_id: str, db: AsyncSession) -> dict:
    """
    For each canonical category:
    1. Check if any ready documents match its keywords (document coverage)
    2. Check if any chunks have been cited for that category's typical queries (query coverage)
    Returns per-category coverage and an overall score.
    """
    # Get all ready document names for this org
    docs = await db.execute(
        select(Document.name, Document.tags)
        .where(Document.org_id == org_id, Document.status == 'ready')
    )
    all_docs = docs.all()
    all_doc_text = " ".join(
        (d.name or "").lower() + " " + " ".join(d.tags or []).lower()
        for d in all_docs
    )

    # Get recent query topics from query_logs
    recent_queries = await db.execute(
        select(QueryLog.query_text)
        .where(QueryLog.org_id == org_id)
        .order_by(QueryLog.created_at.desc())
        .limit(200)
    )
    all_query_text = " ".join(q.query_text.lower() for q in recent_queries.scalars())

    coverage_results = []
    covered_count = 0

    for category in CANONICAL_CATEGORIES:
        keywords = category["detection_keywords"]

        # Document coverage: any keyword found in doc names/tags
        doc_matches = sum(1 for kw in keywords if kw in all_doc_text)
        has_doc_coverage = doc_matches >= 2  # Need at least 2 keyword matches

        # Query coverage: org has asked questions in this category
        query_matches = sum(1 for kw in keywords if kw in all_query_text)
        has_query_coverage = query_matches >= 1

        is_covered = has_doc_coverage
        if is_covered:
            covered_count += 1

        coverage_results.append({
            "id": category["id"],
            "label": category["label"],
            "description": category["description"],
            "covered": is_covered,
            "doc_matches": doc_matches,
            "has_query_coverage": has_query_coverage,
            "example_questions": category["question_examples"],
            "gap_message": (
                None if is_covered
                else f"No documents found covering {category['label'].lower()}. "
                     f"Consider uploading: {', '.join(category['detection_keywords'][:3])} related docs."
            )
        })

    overall_score = covered_count / len(CANONICAL_CATEGORIES)

    return {
        "overall_score": round(overall_score, 2),
        "covered": covered_count,
        "total": len(CANONICAL_CATEGORIES),
        "categories": coverage_results,
        "gaps": [c for c in coverage_results if not c["covered"]]
    }
```

**Backend — `GET /api/admin/coverage-score`:**

```python
@router.get("/admin/coverage-score")
async def get_coverage_score(
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db)
):
    return await compute_coverage_score(current_user.org_id, db)
```

Cache this endpoint in Redis with 1-hour TTL (it's expensive and doesn't need to be real-time):
```python
cache_key = f"coverage:{current_user.org_id}"
cached = await cache_get(cache_key)
if cached:
    return cached
result = await compute_coverage_score(current_user.org_id, db)
await cache_set(cache_key, result, ttl=3600)
return result
```

Invalidate on any document status change to 'ready':
```python
await cache_delete(f"coverage:{org_id}")
```

**Frontend — `app/admin/coverage/page.tsx`:**

```tsx
export default function CoverageScorePage() {
  const { data, isLoading } = useQuery(['coverage-score'], () =>
    fetch('/api/admin/coverage-score').then(r => r.json())
  )

  if (isLoading) return <CoverageSkeleton />

  const scorePercent = Math.round((data?.overall_score || 0) * 100)

  return (
    <div className="max-w-4xl mx-auto px-4 py-6 space-y-6">
      <h1 className="text-xl font-semibold">Knowledge Base Coverage</h1>

      {/* Overall score — big circular indicator */}
      <Card className="flex items-center gap-6 p-6">
        <div className="relative w-24 h-24 shrink-0">
          <svg viewBox="0 0 36 36" className="w-24 h-24 -rotate-90">
            <circle cx="18" cy="18" r="15.9" fill="none" stroke="#e5e7eb" strokeWidth="2" />
            <circle
              cx="18" cy="18" r="15.9"
              fill="none"
              stroke={scorePercent >= 75 ? "#22c55e" : scorePercent >= 50 ? "#f59e0b" : "#ef4444"}
              strokeWidth="2.5"
              strokeDasharray={`${scorePercent} ${100 - scorePercent}`}
              strokeLinecap="round"
            />
          </svg>
          <div className="absolute inset-0 flex items-center justify-center">
            <span className="text-xl font-bold">{scorePercent}%</span>
          </div>
        </div>
        <div>
          <p className="text-lg font-semibold">
            {data.covered} of {data.total} categories covered
          </p>
          <p className="text-sm text-muted-foreground mt-1">
            {data.gaps.length === 0
              ? "Excellent! Your knowledge base covers all major categories."
              : `${data.gaps.length} category gap${data.gaps.length > 1 ? 's' : ''} detected. Upload documents to fill them.`
            }
          </p>
        </div>
      </Card>

      {/* Category grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {(data?.categories || []).map((cat: any) => (
          <div
            key={cat.id}
            className={cn(
              "border rounded-xl p-4 flex items-start gap-3",
              cat.covered
                ? "bg-green-50 dark:bg-green-950 border-green-200 dark:border-green-800"
                : "bg-red-50 dark:bg-red-950 border-red-200 dark:border-red-800"
            )}
          >
            <div className={cn(
              "w-6 h-6 rounded-full flex items-center justify-center shrink-0 mt-0.5",
              cat.covered ? "bg-green-500" : "bg-red-400"
            )}>
              {cat.covered
                ? <Check size={13} className="text-white" strokeWidth={3} />
                : <X size={13} className="text-white" strokeWidth={3} />
              }
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium">{cat.label}</p>
              <p className="text-xs text-muted-foreground mt-0.5">{cat.description}</p>
              {!cat.covered && (
                <>
                  <p className="text-xs text-red-600 dark:text-red-400 mt-1.5">
                    {cat.gap_message}
                  </p>
                  <Link
                    href="/documents?upload=true"
                    className="text-xs text-indigo-600 hover:underline mt-1 inline-block"
                  >
                    Upload documents →
                  </Link>
                </>
              )}
            </div>
          </div>
        ))}
      </div>

      {/* Gap examples — what questions can't be answered */}
      {data?.gaps?.length > 0 && (
        <div className="border rounded-xl p-4">
          <p className="text-sm font-medium mb-3">
            ⚠ Questions your team can't answer yet
          </p>
          <div className="space-y-1.5">
            {data.gaps.slice(0, 6).flatMap((gap: any) =>
              gap.example_questions.map((q: string) => (
                <div key={q} className="flex items-start gap-2 text-sm">
                  <span className="text-muted-foreground mt-0.5">•</span>
                  <span className="text-muted-foreground italic">"{q}"</span>
                </div>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  )
}
```

**Add to admin navigation** in settings sidebar:

```tsx
// Admin nav section
{ label: 'Analytics', href: '/admin/analytics', icon: BarChart2 },
{ label: 'Knowledge Health', href: '/admin/health', icon: HeartPulse },
{ label: 'Coverage Score', href: '/admin/coverage', icon: PieChart },     // NEW
{ label: 'Competitor Mentions', href: '/admin/competitors', icon: ShieldAlert },
{ label: 'Moderation', href: '/admin/moderation', icon: Shield },
```

**End of Day 4 checklist:**
- [ ] `app/help/articles/` directory with 10 `.mdx` files, each with correct frontmatter
- [ ] `HELP_ARTICLES` manifest with correct slugs, categories, tags
- [ ] Help center index page with search (client-side, instant)
- [ ] Category grouping in help index
- [ ] Individual article pages render MDX with prose styles
- [ ] Article feedback buttons (👍 / 👎)
- [ ] `?` help button in sidebar footer links to `/help`
- [ ] `generateStaticParams` set up for build-time static generation
- [ ] `compute_coverage_score()` detects 8 canonical categories via keyword matching
- [ ] `GET /api/admin/coverage-score` endpoint, cached 1hr in Redis
- [ ] Cache invalidated when new document becomes ready
- [ ] Coverage page shows circular overall score indicator
- [ ] Category grid: green (covered) / red (gap) for all 8 categories
- [ ] Gap messages explain what to upload
- [ ] "Questions your team can't answer yet" section
- [ ] Coverage page linked in admin nav

---

## Database Migration Sequence

```
Day 1: CREATE TABLE analytics_events
        ALTER TABLE documents ADD health_score, health_label, last_accessed_at, health_computed_at, gap_flag_count
        CREATE TABLE activity_feed
        ALTER TABLE users ADD activity_private

Day 2: CREATE TABLE moderation_logs
        CREATE TABLE conversation_templates
        ALTER TABLE chunks ADD is_archived, document_version_id
        CREATE TABLE document_versions

Day 3: ALTER TABLE documents ADD source_url, content

Day 4: No new tables — coverage score is computed on demand, cached in Redis
```

---

## Environment Variables Checklist

No new environment variables for V4. All infrastructure from V2/V3 is reused. Verify:

```bash
REDIS_URL=redis://...          # Coverage score caching (Day 4)
INNGEST_EVENT_KEY=...          # Health score cron (Day 1)
APP_URL=https://companybrain.app  # Extension iframe URL (Day 3)
```

---

## Feature Completion Matrix

| # | Feature | Day | Priority |
|---|---|---|---|
| #18 | Admin Usage Analytics Dashboard | 1 | P1 |
| #34 | Document Health Score | 1 | P2 |
| #57 | Activity Feed for Teams | 1 | P3 |
| #60 | Browser Notifications (toast) | 1 | P2 |
| #79 | Content Moderation | 2 | P2 |
| #70 | Conversation Templates | 2 | P2 |
| #39 | Meeting Prep Assistant | 2 | P2 |
| #68 | Document Versioning | 2 | P3 |
| #32 | Chrome Extension | 3 | P2 |
| #94 | Help Center / In-App Docs | 4 | P2 |
| #74 | Knowledge Base Coverage Score | 4 | P3 |

**Total: 11 features across 4 days.**