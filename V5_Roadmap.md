# Company Brain — V5 Feature Roadmap (4 Days)

## Confirmed Architecture Decisions

| Decision | Choice |
|---|---|
| Command palette | kbar |
| Doc auto-summary LLM | Same LLM already in use for chat (no new provider) |
| Collections model | Tag-based (docs belong to multiple collections) |
| Support chat | Intercom (free tier) |
| Scheduled reports | User-configurable: day + frequency (daily/weekly/monthly) |
| Output quality signals | Copy click = implicit "used" signal (zero extra prompts) |
| Table of contents | Structural parsing only — PDF headings + DOCX styles (zero LLM cost) |
| LLM cost dashboard | Custom queries on existing DB (internal only, no Langfuse) |
| Time savings estimates | Fixed per intent: email=30min, JD=2hr, policy lookup=20min, analysis=45min |

## Feature Index

| # | Feature | Priority | Day |
|---|---|---|---|
| #7 | Keyboard Shortcuts | P1 | 1 |
| #12 | Global Keyboard Search / Command Palette | P1 | 1 |
| #24 | Document Auto-Summary | P2 | 1 |
| #107 | Automatic Table of Contents | P2 | 1 |
| #35 | Multi-Document Workspace Scoping (Collections) | P2 | 2 |
| #59 | Output Quality Signals | P2 | 2 |
| #73 | Time Savings Calculator | P2 | 2 |
| #97 | Org Metadata Enrichment | P2 | 2 |
| #75 | LLM Cost Dashboard (internal) | P2 | 3 |
| #78 | Rate Limit Dashboard (Admin) | P2 | 3 |
| #98 | Scheduled Reports | P2 | 3 |
| #95 | Support Chat (Intercom) | P2 | 3 |
| #106 | Fine-Tuned Embedding Models | P2 | 4 |

---

## Day 1 — Power User UX: Keyboard Shortcuts, Command Palette, Doc Auto-Summary, Table of Contents

**Goal:** Power users get keyboard-native navigation. Every document self-describes on ingestion. Long documents become navigable instantly.

**Features:** #7 Keyboard Shortcuts, #12 Global Command Palette, #24 Document Auto-Summary, #107 Auto Table of Contents

---

### #7 — Keyboard Shortcuts

**Install nothing — pure React hook.**

**`hooks/useKeyboardShortcuts.ts`**

```tsx
import { useEffect, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { useKBar } from 'kbar'  // Integrate with command palette

interface ShortcutOptions {
  onNewConversation: () => void
  onSendMessage: () => void
  onStopGeneration: () => void
  onCopyLastResponse: () => void
  conversationId?: string
}

export function useKeyboardShortcuts({
  onNewConversation,
  onSendMessage,
  onStopGeneration,
  onCopyLastResponse,
}: ShortcutOptions) {
  const { query } = useKBar()  // Opens kbar command palette

  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    const isMac = navigator.platform.includes('Mac')
    const cmdOrCtrl = isMac ? e.metaKey : e.ctrlKey

    // Cmd+K — Open command palette (handled by kbar directly, but also wire here)
    if (cmdOrCtrl && e.key === 'k') {
      e.preventDefault()
      query.toggle()
      return
    }

    // Cmd+Enter — Send message
    // Only fire when focus is inside the chat textarea
    if (cmdOrCtrl && e.key === 'Enter') {
      const active = document.activeElement
      const isInTextarea = active?.tagName === 'TEXTAREA' ||
        active?.closest('[data-chat-input]') !== null
      if (isInTextarea) {
        e.preventDefault()
        onSendMessage()
        return
      }
    }

    // Escape — Stop streaming generation
    if (e.key === 'Escape') {
      onStopGeneration()
      return
    }

    // Cmd+Shift+C — Copy last assistant response
    if (cmdOrCtrl && e.shiftKey && e.key === 'c') {
      e.preventDefault()
      onCopyLastResponse()
      return
    }

    // Cmd+/ — Focus conversation search (in sidebar)
    if (cmdOrCtrl && e.key === '/') {
      e.preventDefault()
      const searchInput = document.querySelector<HTMLInputElement>('[data-conversation-search]')
      searchInput?.focus()
      return
    }

    // Cmd+N — New conversation (alias for Cmd+K → New Chat)
    if (cmdOrCtrl && e.key === 'n') {
      // Only outside input fields to avoid interfering with browser new-tab
      const active = document.activeElement
      const isEditing = ['INPUT', 'TEXTAREA'].includes(active?.tagName || '')
      if (!isEditing) {
        e.preventDefault()
        onNewConversation()
        return
      }
    }
  }, [query, onNewConversation, onSendMessage, onStopGeneration, onCopyLastResponse])

  useEffect(() => {
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [handleKeyDown])
}
```

**Wire into `app/chat/[id]/page.tsx`:**

```tsx
useKeyboardShortcuts({
  onNewConversation: () => router.push('/chat/new'),
  onSendMessage: () => handleSendMessage(),
  onStopGeneration: () => abortControllerRef.current?.abort(),
  onCopyLastResponse: () => {
    const lastAssistantMsg = messages.filter(m => m.role === 'assistant').at(-1)
    if (lastAssistantMsg) {
      navigator.clipboard.writeText(lastAssistantMsg.content)
      toast.success('Last response copied!')
    }
  },
})
```

**Keyboard shortcuts reference panel — `components/ui/ShortcutsPanel.tsx`**

Triggered by `?` key (when not in a text field) or via command palette:

```tsx
const SHORTCUTS = [
  { keys: ['⌘', 'K'], label: 'Open command palette' },
  { keys: ['⌘', '↩'], label: 'Send message' },
  { keys: ['Esc'], label: 'Stop generation' },
  { keys: ['⌘', '⇧', 'C'], label: 'Copy last response' },
  { keys: ['⌘', '/'], label: 'Search conversations' },
  { keys: ['⌘', 'N'], label: 'New conversation' },
  { keys: ['?'], label: 'Show this panel' },
]

export function ShortcutsPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>Keyboard Shortcuts</DialogTitle>
        </DialogHeader>
        <div className="space-y-2">
          {SHORTCUTS.map(s => (
            <div key={s.label} className="flex items-center justify-between py-1">
              <span className="text-sm text-muted-foreground">{s.label}</span>
              <div className="flex items-center gap-1">
                {s.keys.map(k => (
                  <kbd key={k} className="px-2 py-0.5 text-xs bg-muted border rounded font-mono">
                    {k}
                  </kbd>
                ))}
              </div>
            </div>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  )
}
```

Add `?` key listener in `useKeyboardShortcuts` to toggle the panel:
```tsx
if (e.key === '?' && !isEditing) {
  e.preventDefault()
  setShortcutsPanelOpen(true)
}
```

Add a `⌘K` badge in the sidebar footer next to the help button as a discoverability hint:
```tsx
<button onClick={() => query.toggle()}
  className="flex items-center gap-1.5 px-2 py-1 text-xs text-muted-foreground hover:text-foreground rounded hover:bg-muted">
  <kbd className="px-1.5 py-0.5 bg-muted border rounded text-xs font-mono">⌘K</kbd>
  <span>Command</span>
</button>
```

---

### #12 — Global Keyboard Search / Command Palette (kbar)

**Install:**
```bash
npm install kbar
```

**`components/command-palette/CommandPalette.tsx`**

```tsx
import {
  KBarProvider, KBarPortal, KBarPositioner, KBarAnimator,
  KBarSearch, KBarResults, useMatches, useRegisterActions,
  Action
} from 'kbar'
import { useRouter } from 'next/navigation'
import { useQuery } from '@tanstack/react-query'
import {
  MessageSquare, FileText, Settings, Plus, Search,
  LayoutTemplate, CalendarDays, Archive, HelpCircle,
  BarChart2, LogOut
} from 'lucide-react'

// ── Static actions (always available) ──────────────────────────────────────
function useStaticActions() {
  const router = useRouter()

  useRegisterActions([
    // Navigation
    {
      id: 'new-conversation',
      name: 'New Conversation',
      shortcut: ['$mod+n'],
      keywords: 'new chat start',
      icon: <Plus size={14} />,
      section: 'Navigation',
      perform: () => router.push('/chat/new'),
    },
    {
      id: 'go-documents',
      name: 'Go to Documents',
      keywords: 'documents files upload',
      icon: <FileText size={14} />,
      section: 'Navigation',
      perform: () => router.push('/documents'),
    },
    {
      id: 'go-templates',
      name: 'Go to Templates',
      keywords: 'templates prompts',
      icon: <LayoutTemplate size={14} />,
      section: 'Navigation',
      perform: () => router.push('/templates'),
    },
    {
      id: 'go-meeting-prep',
      name: 'Meeting Prep Assistant',
      keywords: 'meeting prep brief',
      icon: <CalendarDays size={14} />,
      section: 'Navigation',
      perform: () => router.push('/chat/meeting-prep'),
    },
    {
      id: 'go-history',
      name: 'Query History',
      keywords: 'history logs past queries',
      icon: <Search size={14} />,
      section: 'Navigation',
      perform: () => router.push('/history'),
    },
    {
      id: 'go-archive',
      name: 'Archived Conversations',
      keywords: 'archive old',
      icon: <Archive size={14} />,
      section: 'Navigation',
      perform: () => router.push('/archive'),
    },
    // Settings
    {
      id: 'go-settings',
      name: 'Settings',
      keywords: 'settings preferences',
      icon: <Settings size={14} />,
      section: 'Settings',
      perform: () => router.push('/settings'),
    },
    {
      id: 'go-analytics',
      name: 'Usage Analytics',
      keywords: 'analytics stats usage',
      icon: <BarChart2 size={14} />,
      section: 'Settings',
      perform: () => router.push('/admin/analytics'),
    },
    // Help
    {
      id: 'go-help',
      name: 'Help Center',
      keywords: 'help docs support faq',
      icon: <HelpCircle size={14} />,
      section: 'Help',
      perform: () => router.push('/help'),
    },
    {
      id: 'show-shortcuts',
      name: 'Keyboard Shortcuts',
      keywords: 'keyboard shortcuts hotkeys',
      icon: <span className="text-xs font-mono">⌘</span>,
      section: 'Help',
      perform: () => {/* dispatch to shortcuts panel */},
    },
  ], [])
}

// ── Dynamic actions: recent conversations ──────────────────────────────────
function useDynamicConversationActions() {
  const router = useRouter()
  const { data: conversations } = useQuery(
    ['conversations-for-kbar'],
    () => fetch('/api/chat/conversations?limit=20').then(r => r.json()),
    { staleTime: 30000 }
  )

  useRegisterActions(
    (conversations || []).map((conv: any) => ({
      id: `conv-${conv.id}`,
      name: conv.title,
      keywords: conv.title,
      icon: <MessageSquare size={14} />,
      section: 'Recent Conversations',
      subtitle: formatRelativeTime(conv.updated_at),
      perform: () => router.push(`/chat/${conv.id}`),
    })),
    [conversations]
  )
}

// ── Dynamic actions: documents ─────────────────────────────────────────────
function useDynamicDocumentActions() {
  const router = useRouter()
  const { data: documents } = useQuery(
    ['documents-for-kbar'],
    () => fetch('/api/documents?limit=30').then(r => r.json()),
    { staleTime: 60000 }
  )

  useRegisterActions(
    (documents || [])
      .filter((d: any) => d.status === 'ready')
      .map((doc: any) => ({
        id: `doc-${doc.id}`,
        name: doc.name,
        keywords: [doc.name, ...(doc.tags || [])].join(' '),
        icon: <FileText size={14} />,
        section: 'Documents',
        subtitle: doc.tags?.join(', ') || doc.file_type,
        perform: () => router.push(`/chat/new?document_id=${doc.id}`),
      })),
    [documents]
  )
}

// ── Results renderer ───────────────────────────────────────────────────────
function CommandResults() {
  const { results, rootActionId } = useMatches()

  return (
    <KBarResults
      items={results}
      onRender={({ item, active }) => {
        if (typeof item === 'string') {
          return (
            <div className="px-4 py-1.5 text-xs font-semibold text-muted-foreground uppercase tracking-wider">
              {item}
            </div>
          )
        }
        return (
          <div className={cn(
            'flex items-center gap-3 px-4 py-2.5 cursor-pointer transition-colors',
            active ? 'bg-indigo-50 dark:bg-indigo-950' : 'bg-transparent'
          )}>
            <span className="text-muted-foreground shrink-0">{item.icon}</span>
            <div className="flex-1 min-w-0">
              <p className={cn('text-sm', active && 'text-indigo-700 dark:text-indigo-300')}>
                {item.name}
              </p>
              {item.subtitle && (
                <p className="text-xs text-muted-foreground truncate">{item.subtitle}</p>
              )}
            </div>
            {item.shortcut?.length ? (
              <div className="flex gap-1 shrink-0">
                {item.shortcut.map((sc: string) => (
                  <kbd key={sc} className="px-1.5 py-0.5 text-xs bg-muted border rounded font-mono">
                    {sc.replace('$mod', '⌘')}
                  </kbd>
                ))}
              </div>
            ) : null}
          </div>
        )
      }}
    />
  )
}

// ── Inner component (must be inside KBarProvider) ─────────────────────────
function CommandPaletteInner() {
  useStaticActions()
  useDynamicConversationActions()
  useDynamicDocumentActions()

  return (
    <KBarPortal>
      <KBarPositioner className="fixed inset-0 z-[9999] flex items-start justify-center pt-[15vh] bg-black/40 backdrop-blur-sm">
        <KBarAnimator className="w-full max-w-lg bg-background border rounded-2xl shadow-2xl overflow-hidden">
          {/* Search input */}
          <div className="flex items-center gap-3 px-4 py-3 border-b">
            <Search size={16} className="text-muted-foreground shrink-0" />
            <KBarSearch
              className="flex-1 bg-transparent outline-none text-sm placeholder:text-muted-foreground"
              defaultPlaceholder="Search conversations, documents, actions..."
            />
            <kbd className="px-1.5 py-0.5 text-xs bg-muted border rounded font-mono text-muted-foreground">
              Esc
            </kbd>
          </div>
          {/* Results */}
          <div className="max-h-80 overflow-y-auto py-1">
            <CommandResults />
          </div>
          {/* Footer */}
          <div className="px-4 py-2 border-t flex gap-4 text-xs text-muted-foreground">
            <span className="flex items-center gap-1"><kbd className="font-mono bg-muted border rounded px-1">↑↓</kbd> navigate</span>
            <span className="flex items-center gap-1"><kbd className="font-mono bg-muted border rounded px-1">↩</kbd> select</span>
            <span className="flex items-center gap-1"><kbd className="font-mono bg-muted border rounded px-1">Esc</kbd> close</span>
          </div>
        </KBarAnimator>
      </KBarPositioner>
    </KBarPortal>
  )
}

// ── Provider wrapper — wraps entire app ───────────────────────────────────
export function CommandPaletteProvider({ children }: { children: React.ReactNode }) {
  return (
    <KBarProvider
      options={{
        enableHistory: true,
        // Disable default Cmd+K since we handle it in useKeyboardShortcuts
        disableScrollbarManagement: false,
      }}
    >
      <CommandPaletteInner />
      {children}
    </KBarProvider>
  )
}
```

**Wrap root layout:**

```tsx
// app/layout.tsx
import { CommandPaletteProvider } from '@/components/command-palette/CommandPalette'

export default function RootLayout({ children }) {
  return (
    <html suppressHydrationWarning>
      <body>
        <ThemeProvider ...>
          <CommandPaletteProvider>
            {children}
          </CommandPaletteProvider>
        </ThemeProvider>
      </body>
    </html>
  )
}
```

**`useKBar` hook usage anywhere in the app:**

```tsx
// Any component can open the palette programmatically
import { useKBar } from 'kbar'
const { query } = useKBar()
query.toggle()  // Opens/closes palette
```

---

### #24 — Document Auto-Summary

**Database Migration:**

The `documents.metadata` JSONB column already exists (from V3 onboarding work). The summary and key_topics live inside it:

```json
{
  "summary": "This document covers the company's remote work policy including...",
  "key_topics": ["remote work", "vacation policy", "expense reimbursement", "equipment", "communication guidelines"],
  "summary_generated_at": "2024-02-01T12:00:00Z"
}
```

No new columns needed — everything goes into `metadata`.

**Backend — `app/services/document_summary.py`**

```python
from app.services.llm import call_llm  # Your existing LLM client

SUMMARY_PROMPT = """You are a document indexing assistant.

Read the following document content and respond with ONLY a JSON object in this exact format:
{{
  "summary": "2-3 sentence summary of what this document covers and who it's for",
  "key_topics": ["topic 1", "topic 2", "topic 3", "topic 4", "topic 5"]
}}

Rules:
- summary: exactly 2-3 sentences, factual, no fluff
- key_topics: exactly 5 short topics (2-4 words each), lowercase
- Return ONLY the JSON — no markdown fences, no preamble, no explanation

Document:
{content}"""

async def generate_document_summary(document_id: str, db: AsyncSession):
    """Called by Inngest after document ingestion completes."""
    doc = await db.get(Document, document_id)
    if not doc or doc.status != 'ready':
        return

    # Get the first ~3000 tokens of chunk content as the document text
    chunks = await db.execute(
        select(Chunk.content)
        .where(Chunk.document_id == document_id, Chunk.is_archived == False)
        .order_by(Chunk.chunk_index.asc())
        .limit(15)  # First 15 chunks ≈ 3000 tokens
    )
    content = "\n\n".join(row.content for row in chunks.scalars())

    if not content.strip():
        return

    try:
        response = await call_llm(
            messages=[{
                "role": "user",
                "content": SUMMARY_PROMPT.format(content=content[:8000])  # Cap input
            }],
            max_tokens=300,
            temperature=0.1  # Low temp for consistent JSON output
        )
        raw = response.strip()

        # Strip JSON fences if model wraps them anyway
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        result = json.loads(raw)
        summary = result.get("summary", "")
        key_topics = result.get("key_topics", [])[:5]

        # Store in metadata
        await db.execute(
            update(Document)
            .where(Document.id == document_id)
            .values(
                metadata=Document.metadata.op('||')({
                    "summary": summary,
                    "key_topics": key_topics,
                    "summary_generated_at": datetime.utcnow().isoformat()
                })
            )
        )
        await db.commit()

    except (json.JSONDecodeError, KeyError) as e:
        # Summary generation is non-critical — log and move on
        print(f"[summary] Failed for doc {document_id}: {e}", file=sys.stderr)
```

**Wire into Inngest `document/process` function:**

After the document status changes to 'ready', trigger summary generation as a separate non-blocking step:

```python
# In inngest_functions.py — document/process function
# After: await update_document_status(doc_id, 'ready')
await step.run(
    "generate-summary",
    lambda: generate_document_summary(doc_id)
)
# This runs AFTER the document is ready — user can start querying immediately
# Summary appears a few seconds later when it completes
```

**Frontend — Document summary on hover/expand**

**`components/documents/DocumentSummary.tsx`**

```tsx
import { ChevronDown, ChevronRight, Hash } from 'lucide-react'
import { useState } from 'react'

export function DocumentSummary({
  summary,
  keyTopics,
  onTopicClick
}: {
  summary?: string
  keyTopics?: string[]
  onTopicClick?: (topic: string) => void
}) {
  if (!summary) return null

  return (
    <div className="mt-2 space-y-2">
      <p className="text-xs text-muted-foreground leading-relaxed">{summary}</p>
      {keyTopics && keyTopics.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {keyTopics.map(topic => (
            <button
              key={topic}
              onClick={() => onTopicClick?.(topic)}
              className="flex items-center gap-0.5 px-2 py-0.5 bg-indigo-50 dark:bg-indigo-950 text-indigo-600 dark:text-indigo-400 rounded-full text-xs hover:bg-indigo-100 dark:hover:bg-indigo-900 transition-colors"
              title={`Search for "${topic}"`}
            >
              <Hash size={9} />
              {topic}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
```

**In `components/documents/DocumentRow.tsx`** — expandable summary:

```tsx
const [expanded, setExpanded] = useState(false)
const hasSummary = !!document.metadata?.summary

return (
  <div className="border rounded-lg">
    <div className="flex items-center gap-3 px-4 py-3">
      {/* existing row content */}
      <button
        onClick={() => setExpanded(!expanded)}
        disabled={!hasSummary}
        className={cn(
          "p-1 rounded text-muted-foreground transition-colors",
          hasSummary ? "hover:bg-muted hover:text-foreground" : "opacity-30 cursor-default"
        )}
        title={hasSummary ? "View summary" : "Summary generating..."}
      >
        {expanded
          ? <ChevronDown size={14} />
          : <ChevronRight size={14} />
        }
      </button>
    </div>

    {expanded && hasSummary && (
      <div className="px-4 pb-3 border-t bg-muted/30">
        <DocumentSummary
          summary={document.metadata.summary}
          keyTopics={document.metadata.key_topics}
          onTopicClick={topic => router.push(`/chat/new?q=${encodeURIComponent(topic)}`)}
        />
      </div>
    )}
  </div>
)
```

Also add summary to the kbar document actions as `subtitle`:
```tsx
subtitle: doc.metadata?.summary?.slice(0, 80) || doc.tags?.join(', ')
```

---

### #107 — Automatic Table of Contents

**Backend — `app/services/toc_extractor.py`**

Zero LLM cost — pure structural parsing using libraries already in the stack.

```python
import io
import re
from typing import Optional
import fitz  # PyMuPDF — already used for PDF extraction
from docx import Document as DocxDocument  # python-docx — already in stack

@dataclass
class TocEntry:
    level: int          # 1 = H1, 2 = H2, 3 = H3
    title: str
    page_number: Optional[int]  # PDF page, None for DOCX
    chunk_index: Optional[int]  # For linking to chunks

def extract_toc_from_pdf(file_bytes: bytes) -> list[TocEntry]:
    """Extract headings from PDF using PyMuPDF's built-in TOC and heading detection."""
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    entries = []

    # Method 1: Use built-in PDF TOC (works for PDFs with bookmarks)
    pdf_toc = doc.get_toc(simple=False)
    if pdf_toc:
        for entry in pdf_toc:
            level, title, page = entry[0], entry[1], entry[2]
            if title.strip() and level <= 3:
                entries.append(TocEntry(
                    level=level,
                    title=title.strip(),
                    page_number=page
                ))
        if entries:
            return entries

    # Method 2: Detect headings by font size analysis
    font_sizes = {}
    for page in doc:
        blocks = page.get_text("dict")["blocks"]
        for block in blocks:
            if block.get("type") != 0:  # Text block
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    size = round(span["size"])
                    text = span["text"].strip()
                    if text and len(text) < 150:  # Skip long body text
                        font_sizes[size] = font_sizes.get(size, 0) + 1

    if not font_sizes:
        return []

    # Body text = most common font size. Headings = larger sizes
    body_size = max(font_sizes, key=font_sizes.get)
    heading_sizes = sorted(
        [s for s in font_sizes if s > body_size + 1],
        reverse=True
    )[:3]  # Top 3 sizes = H1, H2, H3

    size_to_level = {size: i + 1 for i, size in enumerate(heading_sizes)}

    for page_num, page in enumerate(doc, 1):
        blocks = page.get_text("dict")["blocks"]
        for block in blocks:
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                line_text = ""
                max_size = 0
                for span in line.get("spans", []):
                    line_text += span["text"]
                    max_size = max(max_size, round(span["size"]))
                line_text = line_text.strip()
                if line_text and max_size in size_to_level and len(line_text) < 150:
                    entries.append(TocEntry(
                        level=size_to_level[max_size],
                        title=line_text,
                        page_number=page_num
                    ))

    doc.close()
    return entries[:50]  # Cap at 50 entries

def extract_toc_from_docx(file_bytes: bytes) -> list[TocEntry]:
    """Extract headings from DOCX using python-docx style names."""
    doc = DocxDocument(io.BytesIO(file_bytes))
    entries = []

    HEADING_STYLES = {
        'heading 1': 1, 'heading 2': 2, 'heading 3': 3,
        'title': 1, 'subtitle': 2,
    }

    for para in doc.paragraphs:
        style_name = para.style.name.lower()
        level = HEADING_STYLES.get(style_name)
        text = para.text.strip()
        if level and text and len(text) < 150:
            entries.append(TocEntry(level=level, title=text, page_number=None))

    return entries[:50]

def extract_toc(file_bytes: bytes, file_type: str) -> list[TocEntry]:
    """Route to correct extractor by file type."""
    if file_type == 'pdf':
        return extract_toc_from_pdf(file_bytes)
    elif file_type == 'docx':
        return extract_toc_from_docx(file_bytes)
    else:
        return []  # txt, csv, xlsx, pptx — no heading structure
```

**Wire into Inngest `document/process`:**

```python
# After chunking, before embedding — TOC extraction is fast (no LLM)
await step.run("extract-toc", lambda: extract_and_store_toc(doc_id))

async def extract_and_store_toc(document_id: str, db: AsyncSession):
    doc = await db.get(Document, document_id)
    file_bytes = await storage.download(doc.file_path)
    entries = extract_toc(file_bytes, doc.file_type)

    if not entries:
        return  # No headings found — skip

    toc_data = [
        {"level": e.level, "title": e.title, "page": e.page_number}
        for e in entries
    ]

    await db.execute(
        update(Document)
        .where(Document.id == document_id)
        .values(
            metadata=Document.metadata.op('||')({
                "toc": toc_data,
                "toc_generated_at": datetime.utcnow().isoformat()
            })
        )
    )
    await db.commit()
```

**Frontend — `components/documents/TableOfContents.tsx`**

```tsx
interface TocEntry {
  level: number
  title: string
  page?: number
}

export function TableOfContents({
  entries,
  documentId,
  onEntryClick
}: {
  entries: TocEntry[]
  documentId: string
  onEntryClick?: (entry: TocEntry) => void
}) {
  if (!entries?.length) return null

  return (
    <div className="space-y-0.5">
      <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-2">
        Contents
      </p>
      {entries.map((entry, i) => (
        <button
          key={i}
          onClick={() => onEntryClick?.(entry)}
          className={cn(
            "w-full text-left px-2 py-1 rounded text-xs hover:bg-muted transition-colors group",
            "flex items-center justify-between gap-2"
          )}
          style={{ paddingLeft: `${(entry.level - 1) * 12 + 8}px` }}
        >
          <span className={cn(
            "truncate",
            entry.level === 1 ? "font-medium" : "text-muted-foreground group-hover:text-foreground"
          )}>
            {entry.title}
          </span>
          {entry.page && (
            <span className="text-muted-foreground shrink-0 text-[10px]">
              p.{entry.page}
            </span>
          )}
        </button>
      ))}
    </div>
  )
}
```

**Render in document detail view / expand panel:**

```tsx
{document.metadata?.toc?.length > 0 && (
  <div className="mt-3 border-t pt-3">
    <TableOfContents
      entries={document.metadata.toc}
      documentId={document.id}
      onEntryClick={entry => {
        // If clicking with page number, open signed URL at that page
        if (entry.page) {
          openDocumentAtPage(document.id, entry.page)
        }
      }}
    />
  </div>
)}
```

**End of Day 1 checklist:**
- [ ] `useKeyboardShortcuts` hook wired into chat layout
- [ ] `⌘K` opens command palette, `⌘↩` sends message, `Esc` stops generation
- [ ] `⌘⇧C` copies last assistant response with toast
- [ ] `⌘/` focuses sidebar search, `⌘N` creates new conversation (outside inputs)
- [ ] `?` key opens ShortcutsPanel dialog
- [ ] `⌘K` badge in sidebar footer as discoverability hint
- [ ] kbar installed, `KBarProvider` wraps root layout
- [ ] Static actions: 10+ navigation + settings items registered
- [ ] Dynamic actions: last 20 conversations loaded and searchable
- [ ] Dynamic actions: documents (ready only) searchable with tag subtitle
- [ ] Results grouped by section, active item highlighted in indigo
- [ ] Footer shows ↑↓ navigate / ↩ select / Esc close hints
- [ ] `generate_document_summary()` function implemented with JSON output
- [ ] Summary triggered as Inngest step AFTER document is ready (non-blocking)
- [ ] JSON parse error handled gracefully (summary just won't appear)
- [ ] Summary + key_topics stored in `documents.metadata`
- [ ] DocumentRow has expand/collapse chevron, shows summary + topic chips
- [ ] Topic chips are clickable → navigate to `/chat/new?q=topic`
- [ ] kbar document subtitle shows summary snippet
- [ ] `extract_toc_from_pdf()` tries built-in PDF TOC first, falls back to font analysis
- [ ] `extract_toc_from_docx()` reads heading styles from DOCX
- [ ] TOC stored in `documents.metadata.toc` as JSON array
- [ ] TOC extraction wired into Inngest document/process (before embedding)
- [ ] `TableOfContents` component renders with indented levels and page numbers
- [ ] TOC shown in document expand panel, page click opens PDF at correct page

---

## Day 2 — Collections, Quality Signals, Time Savings, Org Enrichment

**Goal:** Documents are organized into searchable scopes. Every copy action generates a quality signal. Users see real ROI from the product. Onboarding gets smarter.

**Features:** #35 Multi-Document Collections, #59 Output Quality Signals, #73 Time Savings Calculator, #97 Org Metadata Enrichment

---

### #35 — Multi-Document Workspace Scoping (Collections)

Tag-based model: a collection is just a named group of tags. No new foreign keys on documents — collections reference the existing `tags TEXT[]` column.

**Database Migration:**

```sql
CREATE TABLE collections (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  name VARCHAR(255) NOT NULL,
  description TEXT,
  -- Which tags define this collection — all docs with ANY of these tags are in it
  tag_filters TEXT[] NOT NULL DEFAULT '{}',
  color VARCHAR(7) NOT NULL DEFAULT '#6366f1',  -- Hex color for UI
  icon VARCHAR(50),                              -- Emoji or lucide icon name
  created_by UUID NOT NULL REFERENCES users(id),
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  UNIQUE(org_id, name)
);

CREATE INDEX idx_collections_org ON collections(org_id);
```

No changes to the `documents` table — documents are already tagged. Collections are just saved views over the tags.

**Backend — `app/api/routes/collections.py`:**

```python
# GET /api/collections — list all collections for org
@router.get("/collections")
async def list_collections(current_user: User = Depends(get_current_user), ...):
    collections = await db.execute(
        select(Collection).where(Collection.org_id == current_user.org_id)
        .order_by(Collection.name)
    )
    result = []
    for col in collections.scalars():
        # Count matching documents
        count = await db.scalar(
            select(func.count(Document.id))
            .where(
                Document.org_id == current_user.org_id,
                Document.status == 'ready',
                Document.tags.overlap(col.tag_filters)  # PostgreSQL && operator
            )
        )
        result.append({**col.__dict__, "document_count": count})
    return result

# POST /api/collections — create collection
@router.post("/collections")
async def create_collection(body: CreateCollectionBody, ...):
    # body: { name, description?, tag_filters: [], color?, icon? }
    collection = Collection(
        org_id=current_user.org_id,
        created_by=current_user.id,
        **body.dict()
    )
    db.add(collection)
    await db.commit()
    return collection

# PATCH /api/collections/{id} — update
# DELETE /api/collections/{id} — delete (does NOT delete docs, just the saved view)

# GET /api/collections/{id}/documents — documents in this collection
@router.get("/collections/{collection_id}/documents")
async def get_collection_documents(collection_id: str, ...):
    collection = await db.get(Collection, collection_id)
    if collection.org_id != current_user.org_id:
        raise HTTPException(403)
    return await db.execute(
        select(Document)
        .where(
            Document.org_id == current_user.org_id,
            Document.status == 'ready',
            Document.tags.overlap(collection.tag_filters)
        )
    )
```

**Update `execute_task()` to accept `collection_id`:**

```python
async def execute_task(
    query: str,
    org_id: str,
    document_id: Optional[str] = None,
    collection_id: Optional[str] = None,  # NEW
    tags: Optional[List[str]] = None,      # From V3 tag-scoped search
    ...
):
    # Resolve collection to tags
    if collection_id:
        collection = await db.get(Collection, collection_id)
        if collection and collection.org_id == org_id:
            tags = collection.tag_filters  # Scope search to collection tags
```

No change to `hybrid_search` itself — it already accepts `tags` filter from V3.

**Frontend — `components/chat/CollectionSelector.tsx`**

Replace the V3 `ChatScopeSelector` (which showed raw tags) with a collection-aware version:

```tsx
export function CollectionSelector({
  activeCollectionId,
  onCollectionChange
}: {
  activeCollectionId: string | null
  onCollectionChange: (id: string | null) => void
}) {
  const { data: collections } = useQuery(['collections'], () =>
    fetch('/api/collections').then(r => r.json())
  )

  if (!collections?.length) return null

  return (
    <div className="flex items-center gap-1.5 px-3 py-1.5 border-b overflow-x-auto">
      <span className="text-xs text-muted-foreground shrink-0">Search in:</span>

      {/* All documents option */}
      <button
        onClick={() => onCollectionChange(null)}
        className={cn(
          "px-2.5 py-1 rounded-full text-xs border transition-colors whitespace-nowrap shrink-0",
          activeCollectionId === null
            ? "bg-foreground text-background border-foreground"
            : "border-border text-muted-foreground hover:border-foreground/30"
        )}
      >
        All
      </button>

      {collections.map((col: any) => (
        <button
          key={col.id}
          onClick={() => onCollectionChange(col.id === activeCollectionId ? null : col.id)}
          className={cn(
            "flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs border transition-colors whitespace-nowrap shrink-0",
            activeCollectionId === col.id
              ? "text-white border-transparent"
              : "border-border text-muted-foreground hover:border-foreground/30"
          )}
          style={activeCollectionId === col.id ? { backgroundColor: col.color } : {}}
          title={`${col.document_count} documents`}
        >
          {col.icon && <span>{col.icon}</span>}
          {col.name}
          <span className={cn(
            "text-[10px]",
            activeCollectionId === col.id ? "opacity-80" : "opacity-50"
          )}>
            {col.document_count}
          </span>
        </button>
      ))}

      <Link href="/settings/collections"
        className="text-xs text-muted-foreground hover:text-foreground px-1 shrink-0">
        + Manage
      </Link>
    </div>
  )
}
```

**Collections management page — `app/settings/collections/page.tsx`:**

```tsx
export default function CollectionsPage() {
  const { data: collections } = useQuery(['collections'], ...)
  const [creating, setCreating] = useState(false)

  return (
    <div className="max-w-2xl space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold">Collections</h2>
          <p className="text-sm text-muted-foreground">
            Group documents by tag for scoped search
          </p>
        </div>
        <Button onClick={() => setCreating(true)} size="sm">
          <Plus size={14} className="mr-1" /> New Collection
        </Button>
      </div>

      <div className="space-y-2">
        {(collections || []).map((col: any) => (
          <div key={col.id} className="border rounded-xl px-4 py-3 flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg flex items-center justify-center text-white text-sm shrink-0"
              style={{ backgroundColor: col.color }}>
              {col.icon || col.name[0].toUpperCase()}
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium">{col.name}</p>
              <p className="text-xs text-muted-foreground">
                Tags: {col.tag_filters.join(', ')} · {col.document_count} documents
              </p>
            </div>
            <Button variant="ghost" size="sm" onClick={() => editCollection(col)}>
              Edit
            </Button>
          </div>
        ))}
      </div>

      <CreateCollectionDialog
        open={creating}
        onClose={() => setCreating(false)}
      />
    </div>
  )
}
```

**`CreateCollectionDialog`** — form to create a collection:
- Name field
- Color picker (6 preset colors + hex input)
- Icon (emoji picker or leave blank)
- Tag filter multi-select (from org's existing tags via `GET /api/organizations/tags`)
- Live preview: "This collection matches N documents"

---

### #59 — Output Quality Signals

Copy click = implicit "used" signal. Zero new UI prompts.

**Database Migration:**

```sql
ALTER TABLE messages
  ADD COLUMN IF NOT EXISTS copy_count INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS first_copied_at TIMESTAMP,
  ADD COLUMN IF NOT EXISTS last_copied_at TIMESTAMP;
```

**Backend — `POST /api/chat/messages/{id}/copied`:**

```python
@router.post("/chat/messages/{message_id}/copied")
async def record_message_copy(message_id: str, current_user: User = Depends(get_current_user), ...):
    """Lightweight endpoint called every time the copy button is clicked."""
    now = datetime.utcnow()
    message = await db.get(Message, message_id)
    if not message or message.conversation.org_id != current_user.org_id:
        raise HTTPException(404)

    message.copy_count += 1
    if not message.first_copied_at:
        message.first_copied_at = now
    message.last_copied_at = now
    await db.commit()

    # Also forward to Langfuse as a quality signal
    if message.langfuse_trace_id:
        langfuse.score(
            trace_id=message.langfuse_trace_id,
            name="output_used",
            value=1,
            comment="User copied the output"
        )

    return {"copy_count": message.copy_count}
```

**Frontend — Update `CopyButton` in `components/chat/AssistantMessage.tsx`:**

The copy button already exists from V2 Day 1. Add a single fire-and-forget call after copy:

```tsx
const handleCopy = async () => {
  await navigator.clipboard.writeText(content)
  setCopied(true)
  setTimeout(() => setCopied(false), 2000)

  // Non-blocking quality signal — don't await, don't show errors
  fetch(`/api/chat/messages/${messageId}/copied`, { method: 'POST' })
    .catch(() => {})  // Silently ignore failures
}
```

**Admin quality dashboard — extend `GET /api/admin/analytics`:**

Add to the analytics response:
```python
# Most copied messages (proxy for "most useful outputs")
top_copied = await db.execute(
    select(
        Message.content.label('preview'),
        Message.copy_count,
        Conversation.title.label('conversation_title'),
        QueryLog.intent
    )
    .join(Conversation, Conversation.id == Message.conversation_id)
    .join(QueryLog, QueryLog.message_id == Message.id, isouter=True)
    .where(
        Message.org_id == current_user.org_id,
        Message.role == 'assistant',
        Message.copy_count > 0,
        Message.created_at >= cutoff
    )
    .order_by(Message.copy_count.desc())
    .limit(5)
)

# Copy rate by intent (what % of task_generation messages get copied)
copy_rates = await db.execute(
    select(
        QueryLog.intent,
        func.count(Message.id).label('total'),
        func.sum(func.cast(Message.copy_count > 0, Integer)).label('copied')
    )
    .join(Message, Message.id == QueryLog.message_id)
    .where(QueryLog.org_id == current_user.org_id, QueryLog.created_at >= cutoff)
    .group_by(QueryLog.intent)
)
```

Add a "Copy Rate" card to the analytics dashboard showing what % of outputs are actually used:

```tsx
// In analytics page
<Card>
  <Text>Output Usage Rate</Text>
  <Metric>{data.stats.copy_rate}%</Metric>
  <Text className="text-xs text-muted-foreground">
    of responses were copied and used
  </Text>
</Card>
```

---

### #73 — Time Savings Calculator

**Backend — `app/services/time_savings.py`**

```python
from enum import Enum

# Fixed time estimates per intent (minutes saved)
TIME_ESTIMATES_MINUTES = {
    "task_generation": {
        # Sub-estimates based on intent + response length
        "email": 30,
        "job_description": 120,
        "announcement": 45,
        "document": 60,
        "default": 30,
    },
    "factual_qa": 20,      # Policy lookup / Q&A
    "analysis": 45,         # Analysis that would take manual research
    "search": 10,           # Quick search
}

def estimate_time_saved(intent: str, query: str, response_length: int) -> int:
    """Returns estimated minutes saved for a single query."""
    if intent == "task_generation":
        q_lower = query.lower()
        if any(w in q_lower for w in ["email", "message", "reply"]):
            return TIME_ESTIMATES_MINUTES["task_generation"]["email"]
        if any(w in q_lower for w in ["job description", "jd", "job desc"]):
            return TIME_ESTIMATES_MINUTES["task_generation"]["job_description"]
        if any(w in q_lower for w in ["announcement", "company-wide", "all-hands"]):
            return TIME_ESTIMATES_MINUTES["task_generation"]["announcement"]
        # Longer responses = more time saved
        if response_length > 800:
            return TIME_ESTIMATES_MINUTES["task_generation"]["document"]
        return TIME_ESTIMATES_MINUTES["task_generation"]["default"]
    return TIME_ESTIMATES_MINUTES.get(intent, 10)
```

**Database Migration:**

```sql
ALTER TABLE query_logs
  ADD COLUMN IF NOT EXISTS time_saved_minutes INTEGER NOT NULL DEFAULT 0;
```

**Wire into `execute_task()`** — after response:

```python
minutes_saved = estimate_time_saved(intent.value, query, len(response_text))
await db.execute(
    update(QueryLog)
    .where(QueryLog.id == query_log_id)
    .values(time_saved_minutes=minutes_saved)
)
```

**Backend — `GET /api/me/time-savings` and `GET /api/admin/time-savings`:**

```python
@router.get("/me/time-savings")
async def get_my_time_savings(current_user: User = Depends(get_current_user), ...):
    period_cutoffs = {
        "this_week": datetime.utcnow() - timedelta(days=7),
        "this_month": datetime.utcnow() - timedelta(days=30),
        "all_time": datetime(2020, 1, 1)
    }
    result = {}
    for period, cutoff in period_cutoffs.items():
        total = await db.scalar(
            select(func.sum(QueryLog.time_saved_minutes))
            .where(
                QueryLog.user_id == current_user.id,
                QueryLog.created_at >= cutoff
            )
        ) or 0
        result[period] = total
    return result

@router.get("/admin/time-savings")
async def get_org_time_savings(current_user: User = Depends(get_admin_user), ...):
    # Same but org-wide
    total_month = await db.scalar(
        select(func.sum(QueryLog.time_saved_minutes))
        .where(
            QueryLog.org_id == current_user.org_id,
            QueryLog.created_at >= datetime.utcnow() - timedelta(days=30)
        )
    ) or 0

    # Per-user breakdown
    per_user = await db.execute(
        select(
            User.name,
            func.sum(QueryLog.time_saved_minutes).label('minutes')
        )
        .join(QueryLog, QueryLog.user_id == User.id)
        .where(
            QueryLog.org_id == current_user.org_id,
            QueryLog.created_at >= datetime.utcnow() - timedelta(days=30)
        )
        .group_by(User.id, User.name)
        .order_by(func.sum(QueryLog.time_saved_minutes).desc())
    )

    return {
        "total_minutes_this_month": total_month,
        "total_hours_this_month": round(total_month / 60, 1),
        "per_user": [{"name": r.name, "hours": round(r.minutes / 60, 1)} for r in per_user]
    }
```

**Frontend — `components/dashboard/TimeSavingsCard.tsx`:**

```tsx
export function TimeSavingsCard() {
  const { data: savings } = useQuery(['time-savings'], () =>
    fetch('/api/me/time-savings').then(r => r.json())
  )

  const monthHours = savings ? Math.round(savings.this_month / 60 * 10) / 10 : 0
  const weekHours = savings ? Math.round(savings.this_week / 60 * 10) / 10 : 0

  return (
    <div className="border rounded-xl p-4 bg-gradient-to-br from-indigo-50 to-purple-50 dark:from-indigo-950 dark:to-purple-950">
      <div className="flex items-start gap-3">
        <div className="p-2 bg-indigo-100 dark:bg-indigo-900 rounded-lg">
          <Clock size={18} className="text-indigo-600 dark:text-indigo-400" />
        </div>
        <div>
          <p className="text-sm font-medium">Time saved this month</p>
          <p className="text-2xl font-bold text-indigo-700 dark:text-indigo-300 mt-0.5">
            {monthHours}h
          </p>
          <p className="text-xs text-muted-foreground mt-1">
            {weekHours}h this week
          </p>
        </div>
      </div>
    </div>
  )
}
```

**Surface in 3 places:**

1. **Chat sidebar footer** — below quota meter: "⏱ 14.5h saved this month"
2. **Admin analytics page** — new stat card showing org-wide hours saved
3. **Weekly digest email** — extend existing template: "Your team saved ~14h this month using Company Brain"

---

### #97 — Org Metadata Enrichment

**Database Migration:**

```sql
ALTER TABLE organizations
  ADD COLUMN IF NOT EXISTS industry VARCHAR(100),
  ADD COLUMN IF NOT EXISTS company_size VARCHAR(20),
  -- '1-10' | '11-50' | '51-200' | '200-1000' | '1000+'
  ADD COLUMN IF NOT EXISTS primary_use_case VARCHAR(100),
  -- 'hr_policies' | 'sales_enablement' | 'customer_support' | 'engineering' | 'general'
  ADD COLUMN IF NOT EXISTS onboarding_completed_at TIMESTAMP;
```

**Backend — `POST /api/organizations/enrich`:**

```python
class OrgEnrichmentBody(BaseModel):
    industry: str
    company_size: str
    primary_use_case: str

@router.post("/organizations/enrich")
async def enrich_org_metadata(
    body: OrgEnrichmentBody,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db)
):
    org = await db.get(Organization, current_user.org_id)
    org.industry = body.industry
    org.company_size = body.company_size
    org.primary_use_case = body.primary_use_case
    org.onboarding_completed_at = datetime.utcnow()
    await db.commit()

    # Trigger downstream personalization
    await inngest.send("org/post-enrichment", {
        "org_id": str(current_user.org_id),
        "industry": body.industry,
        "use_case": body.primary_use_case
    })

    return {"enriched": True}
```

**Inngest handler — `org/post-enrichment`:**

Fires once after enrichment to set up personalized defaults:

```python
@inngest.create_function(
    fn_id="org/post-enrichment",
    trigger=inngest.TriggerEvent("org/post-enrichment")
)
async def post_enrichment_setup(ctx, step):
    org_id = ctx.event.data["org_id"]
    industry = ctx.event.data["industry"]
    use_case = ctx.event.data["use_case"]

    # 1. Pre-seed org with relevant prompt templates
    await step.run("seed-templates", lambda: seed_templates_for_org(org_id, industry, use_case))

    # 2. Pre-populate document recommendations (V3 #50 — already implemented)
    await step.run("seed-recommendations", lambda: seed_doc_recommendations(org_id, industry))

    # 3. Set default AI instructions based on use case
    await step.run("set-ai-instructions", lambda: set_default_ai_instructions(org_id, use_case))
```

**Template seeding by use case:**

```python
TEMPLATE_SEEDS = {
    "hr_policies": ["invite-teammates", "policy-qa", "handbook-summary"],
    "sales_enablement": ["client-pitch", "objection-handling", "proposal-draft"],
    "customer_support": ["customer-response", "escalation-email", "refund-policy"],
    "engineering": ["runbook", "incident-postmortem", "architecture-review"],
    "general": ["write-email", "job-description", "announcement"],
}

DEFAULT_AI_INSTRUCTIONS = {
    "hr_policies": "This organization uses Company Brain primarily for HR policy Q&A. Responses should be accurate, cite specific policies, and avoid speculation.",
    "sales_enablement": "This organization uses Company Brain for sales enablement. Responses should be persuasive, customer-focused, and reference product positioning documents.",
    "customer_support": "This organization uses Company Brain for customer support. Responses should be empathetic, solution-focused, and reference support policies.",
    "general": "",
}
```

**Frontend — Enrichment modal — `components/onboarding/EnrichmentModal.tsx`:**

Show this modal once, on first admin login after signup, before the onboarding checklist:

```tsx
const INDUSTRIES = [
  'SaaS / Software', 'E-commerce', 'Healthcare', 'Finance',
  'Agency / Consulting', 'Education', 'Real Estate', 'Manufacturing', 'Other'
]

const COMPANY_SIZES = ['1-10', '11-50', '51-200', '200-1000', '1000+']

const USE_CASES = [
  { value: 'hr_policies', label: '👥 HR & People Ops', desc: 'Policy Q&A, onboarding docs' },
  { value: 'sales_enablement', label: '💼 Sales Enablement', desc: 'Pitch decks, objection handling' },
  { value: 'customer_support', label: '🎧 Customer Support', desc: 'Support playbooks, refund policies' },
  { value: 'engineering', label: '⚙️ Engineering', desc: 'Runbooks, architecture docs' },
  { value: 'general', label: '🌐 General Knowledge Base', desc: 'Mixed use across teams' },
]

export function EnrichmentModal({ open, onComplete }: { open: boolean; onComplete: () => void }) {
  const [step, setStep] = useState<'industry' | 'size' | 'usecase'>('industry')
  const [form, setForm] = useState({ industry: '', company_size: '', primary_use_case: '' })

  const complete = async () => {
    await fetch('/api/organizations/enrich', {
      method: 'POST',
      body: JSON.stringify(form),
      headers: { 'Content-Type': 'application/json' }
    })
    onComplete()
  }

  return (
    <Dialog open={open} modal>
      <DialogContent className="max-w-md" onInteractOutside={e => e.preventDefault()}>
        <DialogHeader>
          <DialogTitle>Tell us about your organization</DialogTitle>
          <DialogDescription>
            Takes 30 seconds. We'll personalize Company Brain for your team.
          </DialogDescription>
        </DialogHeader>

        {step === 'industry' && (
          <div className="space-y-2">
            <label className="text-sm font-medium">What industry are you in?</label>
            <div className="grid grid-cols-2 gap-2">
              {INDUSTRIES.map(ind => (
                <button key={ind}
                  onClick={() => { setForm(f => ({...f, industry: ind})); setStep('size') }}
                  className={cn("px-3 py-2 text-sm border rounded-lg text-left hover:border-indigo-400 transition-colors",
                    form.industry === ind && "border-indigo-500 bg-indigo-50 dark:bg-indigo-950"
                  )}>
                  {ind}
                </button>
              ))}
            </div>
          </div>
        )}

        {step === 'size' && (
          <div className="space-y-2">
            <label className="text-sm font-medium">How many employees?</label>
            <div className="flex flex-wrap gap-2">
              {COMPANY_SIZES.map(size => (
                <button key={size}
                  onClick={() => { setForm(f => ({...f, company_size: size})); setStep('usecase') }}
                  className={cn("px-4 py-2 text-sm border rounded-lg hover:border-indigo-400 transition-colors",
                    form.company_size === size && "border-indigo-500 bg-indigo-50 dark:bg-indigo-950"
                  )}>
                  {size}
                </button>
              ))}
            </div>
          </div>
        )}

        {step === 'usecase' && (
          <div className="space-y-2">
            <label className="text-sm font-medium">Primary use case?</label>
            <div className="space-y-2">
              {USE_CASES.map(uc => (
                <button key={uc.value}
                  onClick={() => { setForm(f => ({...f, primary_use_case: uc.value})); complete() }}
                  className={cn("w-full text-left px-3 py-2.5 border rounded-lg hover:border-indigo-400 transition-colors",
                    form.primary_use_case === uc.value && "border-indigo-500 bg-indigo-50"
                  )}>
                  <p className="text-sm font-medium">{uc.label}</p>
                  <p className="text-xs text-muted-foreground">{uc.desc}</p>
                </button>
              ))}
            </div>
          </div>
        )}

        <DialogFooter>
          {step !== 'industry' && (
            <Button variant="ghost" size="sm"
              onClick={() => setStep(step === 'size' ? 'industry' : 'size')}>
              Back
            </Button>
          )}
          <Button variant="ghost" size="sm" onClick={onComplete}>
            Skip for now
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
```

**Trigger the modal** — in `app/layout.tsx` (client component), check if admin has not enriched:

```tsx
const showEnrichment = isAdmin && !org?.industry && !org?.onboarding_completed_at
```

**End of Day 2 checklist:**
- [ ] `collections` table created, CRUD routes working
- [ ] `GET /api/collections` returns document_count per collection
- [ ] `execute_task()` accepts `collection_id`, resolves to tag filter
- [ ] `CollectionSelector` renders in chat with color-coded pills, "All" default
- [ ] Collections settings page with create dialog (name, color, icon, tag filters)
- [ ] Live preview "matches N documents" in create dialog
- [ ] `messages.copy_count`, `first_copied_at`, `last_copied_at` columns added
- [ ] `POST /api/chat/messages/{id}/copied` increments count, forwards to Langfuse
- [ ] Copy button fires signal on click (non-blocking, silent failure)
- [ ] Copy rate stat card added to analytics dashboard
- [ ] Top copied messages list in analytics
- [ ] `query_logs.time_saved_minutes` column added
- [ ] `estimate_time_saved()` function with intent + query-based estimates
- [ ] Time saved recorded on every `execute_task()` completion
- [ ] `GET /api/me/time-savings` returns week/month/all-time totals
- [ ] `GET /api/admin/time-savings` returns org-wide + per-user breakdown
- [ ] Time savings card in sidebar footer
- [ ] Time savings stat card in admin analytics page
- [ ] Weekly digest email includes "X hours saved this month"
- [ ] `industry`, `company_size`, `primary_use_case` columns on organizations
- [ ] `POST /api/organizations/enrich` stores data, fires Inngest event
- [ ] Post-enrichment Inngest handler seeds templates + recommendations + AI instructions
- [ ] Enrichment modal shows for first-time admins (3-step: industry→size→use case)
- [ ] "Skip for now" option works, modal dismissed permanently

---

## Day 3 — Internal Dashboards, Rate Limits, Scheduled Reports, Support Chat

**Goal:** You see which customers cost you money. Admins see rate limit pressure. Reports email themselves. Users can reach you in-app.

**Features:** #75 LLM Cost Dashboard, #78 Rate Limit Dashboard, #98 Scheduled Reports, #95 Support Chat

---

### #75 — LLM Cost Dashboard (Internal — Founder-Only)

This is a **founder-only internal page** — not exposed to org admins. Gated by a hardcoded list of founder user IDs or a `is_founder` flag.

**Database:** Token usage already stored via Langfuse trace metadata. But for the custom dashboard, store it directly in DB:

```sql
CREATE TABLE llm_usage_logs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  user_id UUID REFERENCES users(id) ON DELETE SET NULL,
  model VARCHAR(100) NOT NULL,
  input_tokens INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL DEFAULT 0,
  -- Estimated cost in USD (calculated at log time based on model pricing)
  estimated_cost_usd NUMERIC(10, 6) NOT NULL DEFAULT 0,
  query_type VARCHAR(50),  -- 'chat' | 'summary' | 'meeting_prep'
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_llm_usage_org_month ON llm_usage_logs(org_id, created_at DESC);
CREATE INDEX idx_llm_usage_month ON llm_usage_logs(created_at DESC);
```

**Model pricing table (update as needed):**

```python
# app/services/llm_cost.py
MODEL_PRICING_PER_1K_TOKENS = {
    # (input_cost, output_cost) in USD per 1000 tokens
    "gpt-4o": (0.005, 0.015),
    "gpt-4o-mini": (0.00015, 0.0006),
    "gpt-4-turbo": (0.01, 0.03),
    "claude-3-5-sonnet": (0.003, 0.015),
    "claude-3-haiku": (0.00025, 0.00125),
}

def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    pricing = MODEL_PRICING_PER_1K_TOKENS.get(model, (0.005, 0.015))
    input_cost = (input_tokens / 1000) * pricing[0]
    output_cost = (output_tokens / 1000) * pricing[1]
    return round(input_cost + output_cost, 6)
```

**Wire into `execute_task()`** — after LLM response:

```python
cost = calculate_cost(model_name, response.usage.prompt_tokens, response.usage.completion_tokens)
db.add(LLMUsageLog(
    org_id=org_id,
    user_id=user_id,
    model=model_name,
    input_tokens=response.usage.prompt_tokens,
    output_tokens=response.usage.completion_tokens,
    estimated_cost_usd=cost,
    query_type='chat'
))
await db.commit()
```

**Backend — `GET /api/internal/llm-costs` (founder-only):**

```python
def require_founder(current_user: User = Depends(get_current_user)):
    FOUNDER_IDS = set(settings.FOUNDER_USER_IDS.split(','))  # In .env
    if str(current_user.id) not in FOUNDER_IDS:
        raise HTTPException(403, "Internal endpoint")
    return current_user

@router.get("/internal/llm-costs")
async def get_llm_costs(
    period: str = "30d",
    current_user: User = Depends(require_founder),
    ...
):
    days = {"7d": 7, "30d": 30, "90d": 90}.get(period, 30)
    cutoff = datetime.utcnow() - timedelta(days=days)

    # Total cost in period
    total_cost = await db.scalar(
        select(func.sum(LLMUsageLog.estimated_cost_usd))
        .where(LLMUsageLog.created_at >= cutoff)
    ) or 0

    # Cost per org (sorted by highest cost first)
    per_org = await db.execute(
        select(
            Organization.name,
            Organization.id,
            func.sum(LLMUsageLog.estimated_cost_usd).label('cost'),
            func.sum(LLMUsageLog.input_tokens + LLMUsageLog.output_tokens).label('tokens'),
            func.count(LLMUsageLog.id).label('queries')
        )
        .join(Organization, Organization.id == LLMUsageLog.org_id)
        .where(LLMUsageLog.created_at >= cutoff)
        .group_by(Organization.id, Organization.name)
        .order_by(func.sum(LLMUsageLog.estimated_cost_usd).desc())
    )

    # Daily cost trend
    daily_cost = await db.execute(
        select(
            func.date_trunc('day', LLMUsageLog.created_at).label('day'),
            func.sum(LLMUsageLog.estimated_cost_usd).label('cost')
        )
        .where(LLMUsageLog.created_at >= cutoff)
        .group_by('day')
        .order_by('day')
    )

    # Cost by model
    by_model = await db.execute(
        select(
            LLMUsageLog.model,
            func.sum(LLMUsageLog.estimated_cost_usd).label('cost'),
            func.count().label('queries')
        )
        .where(LLMUsageLog.created_at >= cutoff)
        .group_by(LLMUsageLog.model)
        .order_by(func.sum(LLMUsageLog.estimated_cost_usd).desc())
    )

    return {
        "total_cost_usd": round(total_cost, 4),
        "per_org": [...],
        "daily_cost": [...],
        "by_model": [...],
    }
```

**Frontend — `app/internal/costs/page.tsx`:**

Locked behind `require_founder` middleware. Standard analytics layout with:

- Total cost card (with month-over-month delta)
- Cost per org table: Name | Queries | Tokens | Cost | Cost/Query
- Flag orgs where `cost > $X/month` in red (unprofitable threshold)
- Daily cost area chart
- Cost by model pie/bar chart

Add `FOUNDER_USER_IDS=uuid1,uuid2` to `.env`.

---

### #78 — Rate Limit Dashboard (Admin)

**Backend — `GET /api/admin/rate-limits`:**

Rate limit data already exists in Redis (quota counters from V2 Day 8). Query and surface it:

```python
@router.get("/admin/rate-limits")
async def get_rate_limits(current_user: User = Depends(get_admin_user), ...):
    org_id = current_user.org_id
    org = await db.get(Organization, org_id)
    plan_limit = get_plan_query_limit(org)

    # Get current month usage from Redis
    month_key = f"quota:{org_id}:{get_current_month()}"
    org_used = int(await redis_client.get(month_key) or 0)

    # Per-user usage from query_logs (last 30 days)
    per_user = await db.execute(
        select(
            User.name,
            User.email,
            func.count(QueryLog.id).label('queries_30d'),
            func.count(QueryLog.id).filter(
                QueryLog.created_at >= datetime.utcnow() - timedelta(days=1)
            ).label('queries_today'),
        )
        .join(QueryLog, QueryLog.user_id == User.id)
        .where(
            User.org_id == org_id,
            QueryLog.created_at >= datetime.utcnow() - timedelta(days=30)
        )
        .group_by(User.id, User.name, User.email)
        .order_by(func.count(QueryLog.id).desc())
    )

    # Projected month-end usage (linear projection)
    day_of_month = datetime.utcnow().day
    days_in_month = 30  # Approximate
    projected = int(org_used / day_of_month * days_in_month) if day_of_month > 0 else org_used

    return {
        "org_quota": {
            "used": org_used,
            "limit": plan_limit,
            "reset_date": get_first_of_next_month(),
            "projected_month_end": projected,
            "will_exceed": projected > plan_limit,
            "pct_used": round(org_used / plan_limit * 100, 1) if plan_limit else 0,
        },
        "per_user": [...],
    }
```

**Frontend — `app/admin/rate-limits/page.tsx`:**

```tsx
export default function RateLimitsPage() {
  const { data } = useQuery(['rate-limits'], () =>
    fetch('/api/admin/rate-limits').then(r => r.json())
  )

  const quota = data?.org_quota

  return (
    <div className="max-w-3xl mx-auto px-4 py-6 space-y-6">
      <h1 className="text-xl font-semibold">Rate Limits & Quota</h1>

      {/* Org quota card */}
      <Card className="p-5">
        <div className="flex items-start justify-between mb-3">
          <div>
            <p className="text-sm font-medium">Monthly Query Quota</p>
            <p className="text-2xl font-bold mt-1">
              {quota?.used?.toLocaleString()} / {quota?.limit?.toLocaleString()}
            </p>
          </div>
          {quota?.will_exceed && (
            <Badge color="red" size="sm">⚠ Projected to exceed</Badge>
          )}
        </div>
        <ProgressBar
          value={quota?.pct_used || 0}
          color={quota?.pct_used > 80 ? "red" : quota?.pct_used > 60 ? "amber" : "indigo"}
          className="mb-2"
        />
        <div className="flex justify-between text-xs text-muted-foreground">
          <span>{quota?.pct_used}% used</span>
          <span>Resets {formatDate(quota?.reset_date)}</span>
        </div>
        {quota?.will_exceed && (
          <div className="mt-3 p-3 bg-red-50 dark:bg-red-950 border border-red-200 rounded-lg text-xs text-red-700">
            At current rate, your team will use ~{quota.projected_month_end.toLocaleString()} queries
            this month, exceeding your limit of {quota.limit.toLocaleString()}.{' '}
            <Link href="/settings/billing" className="underline font-medium">Upgrade your plan →</Link>
          </div>
        )}
      </Card>

      {/* Per-user breakdown */}
      <Card>
        <Text className="font-medium mb-3">Usage by team member</Text>
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs text-muted-foreground border-b text-left">
              <th className="pb-2 font-medium">User</th>
              <th className="pb-2 font-medium">Today</th>
              <th className="pb-2 font-medium">Last 30 days</th>
              <th className="pb-2 font-medium">Share of quota</th>
            </tr>
          </thead>
          <tbody>
            {(data?.per_user || []).map((user: any) => (
              <tr key={user.email} className="border-b last:border-0">
                <td className="py-2">
                  <p className="font-medium">{user.name}</p>
                  <p className="text-xs text-muted-foreground">{user.email}</p>
                </td>
                <td className="py-2 font-mono text-sm">{user.queries_today}</td>
                <td className="py-2 font-mono text-sm">{user.queries_30d}</td>
                <td className="py-2 w-32">
                  <ProgressBar
                    value={Math.round(user.queries_30d / (quota?.limit || 1) * 100)}
                    className="h-1.5"
                  />
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

Add to admin nav: `{ label: 'Rate Limits', href: '/admin/rate-limits', icon: Gauge }`.

---

### #98 — Scheduled Reports

**Database Migration:**

```sql
CREATE TABLE scheduled_reports (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  created_by UUID NOT NULL REFERENCES users(id),
  -- Recipient email(s) — JSON array
  recipients TEXT[] NOT NULL,
  frequency VARCHAR(20) NOT NULL,  -- 'daily' | 'weekly' | 'monthly'
  day_of_week INTEGER,             -- 0=Mon..6=Sun (for weekly)
  day_of_month INTEGER,            -- 1-28 (for monthly)
  send_time_utc INTEGER NOT NULL DEFAULT 8,  -- Hour in UTC (0-23)
  report_type VARCHAR(50) NOT NULL DEFAULT 'usage_summary',
  -- 'usage_summary' | 'knowledge_health' | 'cost_report'
  is_active BOOLEAN NOT NULL DEFAULT true,
  last_sent_at TIMESTAMP,
  next_send_at TIMESTAMP NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_scheduled_reports_next_send ON scheduled_reports(next_send_at, is_active);
```

**Backend — `app/services/report_scheduler.py`:**

```python
from croniter import croniter
# pip install croniter

def calculate_next_send(frequency: str, day_of_week: int, day_of_month: int, hour_utc: int) -> datetime:
    """Calculate the next send time based on schedule config."""
    now = datetime.utcnow()
    if frequency == 'daily':
        cron = f"0 {hour_utc} * * *"
    elif frequency == 'weekly':
        cron = f"0 {hour_utc} * * {day_of_week}"
    elif frequency == 'monthly':
        cron = f"0 {hour_utc} {min(day_of_month, 28)} * *"
    else:
        cron = f"0 {hour_utc} * * 1"  # Default: weekly Monday

    c = croniter(cron, now)
    return c.get_next(datetime)
```

Install:
```bash
pip install croniter
```

**CRUD routes — `app/api/routes/scheduled_reports.py`:**

```python
@router.get("/scheduled-reports")
async def list_scheduled_reports(current_user: User = Depends(get_admin_user), ...):
    return await db.execute(
        select(ScheduledReport)
        .where(ScheduledReport.org_id == current_user.org_id)
    )

@router.post("/scheduled-reports")
async def create_scheduled_report(body: CreateReportBody, ...):
    next_send = calculate_next_send(
        body.frequency, body.day_of_week or 0,
        body.day_of_month or 1, body.send_time_utc
    )
    report = ScheduledReport(
        org_id=current_user.org_id,
        created_by=current_user.id,
        next_send_at=next_send,
        **body.dict()
    )
    db.add(report)
    await db.commit()

@router.patch("/scheduled-reports/{id}")
async def update_scheduled_report(report_id: str, body: UpdateReportBody, ...):
    report = await db.get(ScheduledReport, report_id)
    # Update fields, recalculate next_send_at
    report.next_send_at = calculate_next_send(...)
    await db.commit()

@router.delete("/scheduled-reports/{id}")
async def delete_scheduled_report(report_id: str, ...):
    # Hard delete
```

**Inngest cron — checks every 15 minutes for due reports:**

```python
@inngest.create_function(
    fn_id="reports/send-due",
    trigger=inngest.TriggerCron(cron="*/15 * * * *")
)
async def send_due_reports(ctx, step):
    now = datetime.utcnow()
    due_reports = await step.run("fetch-due", lambda: get_due_reports(now))

    for report in due_reports:
        await step.run(f"send-{report.id}", lambda: send_report(report))

async def send_report(report: ScheduledReport):
    # Generate report data based on report_type
    if report.report_type == 'usage_summary':
        data = await generate_usage_summary(report.org_id)
        html = render_usage_summary_email(data)
        subject = f"Company Brain: Weekly usage summary — {report.org.name}"

    elif report.report_type == 'knowledge_health':
        data = await get_knowledge_health_data(report.org_id)
        html = render_health_email(data)
        subject = f"Company Brain: Knowledge base health report"

    # Send to all recipients
    for recipient in report.recipients:
        await send_email(to=recipient, subject=subject, html=html)

    # Update last_sent_at and calculate next_send_at
    report.last_sent_at = datetime.utcnow()
    report.next_send_at = calculate_next_send(
        report.frequency, report.day_of_week or 0,
        report.day_of_month or 1, report.send_time_utc
    )
    await db.commit()
```

**Frontend — `app/settings/reports/page.tsx`:**

```tsx
export default function ScheduledReportsPage() {
  const { data: reports } = useQuery(['scheduled-reports'], ...)
  const [creating, setCreating] = useState(false)

  return (
    <div className="max-w-2xl space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold">Scheduled Reports</h2>
          <p className="text-sm text-muted-foreground">
            Automated reports delivered to your inbox
          </p>
        </div>
        <Button size="sm" onClick={() => setCreating(true)}>
          <Plus size={14} className="mr-1" /> New Report
        </Button>
      </div>

      {(reports || []).map((report: any) => (
        <div key={report.id} className="border rounded-xl px-4 py-3 flex items-center gap-3">
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium capitalize">
              {report.report_type.replace('_', ' ')}
            </p>
            <p className="text-xs text-muted-foreground">
              {report.frequency === 'weekly' && `Every ${DAYS[report.day_of_week]}`}
              {report.frequency === 'daily' && 'Every day'}
              {report.frequency === 'monthly' && `Monthly on day ${report.day_of_month}`}
              {' '}at {report.send_time_utc}:00 UTC
              · to {report.recipients.join(', ')}
            </p>
            <p className="text-xs text-muted-foreground">
              Next: {formatDate(report.next_send_at)}
              {report.last_sent_at && ` · Last sent: ${formatRelativeTime(report.last_sent_at)}`}
            </p>
          </div>
          <Switch
            checked={report.is_active}
            onCheckedChange={v => toggleReport(report.id, v)}
          />
          <Button variant="ghost" size="sm" onClick={() => deleteReport(report.id)}>
            <Trash2 size={14} />
          </Button>
        </div>
      ))}
    </div>
  )
}
```

**Create report dialog — fields:**
- Report type: Usage Summary / Knowledge Health
- Frequency: Daily / Weekly / Monthly
- Day (for weekly: Mon–Sun dropdown; for monthly: day 1–28)
- Time: dropdown 00:00–23:00 UTC
- Recipients: tag-input for email addresses (defaults to current user's email)

Add "Reports" to settings sidebar nav.

---

### #95 — Support Chat (Intercom)

**Setup Intercom:**

1. Sign up at `intercom.com` → create a new workspace → get the app ID (`APP_ID`)
2. Free tier includes up to 1 seat (you as founder) and 200 conversations/month
3. Add to `.env`: `NEXT_PUBLIC_INTERCOM_APP_ID=your_app_id`

**Install:**
```bash
npm install @intercom/messenger-js-sdk
```

**`components/support/IntercomProvider.tsx`:**

```tsx
'use client'
import { useEffect } from 'react'
import Intercom from '@intercom/messenger-js-sdk'
import { useQuery } from '@tanstack/react-query'

export function IntercomProvider() {
  const { data: user } = useQuery(['current-user'], getCurrentUser)

  useEffect(() => {
    if (!process.env.NEXT_PUBLIC_INTERCOM_APP_ID) return

    Intercom({
      app_id: process.env.NEXT_PUBLIC_INTERCOM_APP_ID,
      ...(user ? {
        // Pre-populate Intercom with user context
        user_id: user.id,
        name: user.name,
        email: user.email,
        created_at: Math.floor(new Date(user.created_at).getTime() / 1000),
        // Company context
        company: {
          id: user.org_id,
          name: user.org_name,
          plan: user.org_plan,
          industry: user.org_industry || undefined,
          size: user.org_company_size || undefined,
        },
        // Custom attributes for support context
        total_queries: user.total_query_count || 0,
        documents_uploaded: user.document_count || 0,
      } : {})
    })

    return () => {
      // Cleanup on unmount
      Intercom('shutdown')
    }
  }, [user?.id])

  return null  // Intercom renders its own DOM
}
```

**Add to root layout:**

```tsx
// app/layout.tsx
import { IntercomProvider } from '@/components/support/IntercomProvider'

// Inside RootLayout, after ThemeProvider + CommandPaletteProvider:
<IntercomProvider />
```

Intercom renders a chat bubble in the bottom-right corner automatically. The existing `⌘K` badge is in the bottom-left — no conflict.

**Programmatic trigger** — "Contact Support" link in Help Center and error states:

```tsx
// Anywhere in the app
import Intercom from '@intercom/messenger-js-sdk'

<button onClick={() => Intercom('show')}>
  Contact Support
</button>
```

**Intercom setup in dashboard:**
- Set your availability hours
- Create auto-reply: "We typically respond within 2 hours. In the meantime, check our [Help Center](/help)."
- Tag conversations by plan tier for prioritization

**End of Day 3 checklist:**
- [ ] `llm_usage_logs` table created
- [ ] `calculate_cost()` function with model pricing table
- [ ] LLM usage logged on every `execute_task()` completion
- [ ] `FOUNDER_USER_IDS` in `.env`, `require_founder` middleware
- [ ] `GET /api/internal/llm-costs` returns per-org cost breakdown
- [ ] Internal cost dashboard shows cost table, daily trend, by-model breakdown
- [ ] Red flag on orgs exceeding cost threshold
- [ ] `GET /api/admin/rate-limits` returns org quota + per-user breakdown
- [ ] Rate limits page shows quota bar, projected overage warning, per-user table
- [ ] `scheduled_reports` table created, croniter installed
- [ ] CRUD routes for scheduled reports (create/update/delete/list)
- [ ] `calculate_next_send()` correctly computes next fire time for daily/weekly/monthly
- [ ] Inngest cron runs every 15min, sends due reports via Resend
- [ ] Report email for usage_summary renders correctly
- [ ] Scheduled reports settings page with create dialog
- [ ] Active/inactive toggle per report
- [ ] Intercom app ID in `.env`, `@intercom/messenger-js-sdk` installed
- [ ] `IntercomProvider` in root layout, passes user + company context
- [ ] Chat bubble appears in bottom-right, logged-in users auto-identified
- [ ] "Contact Support" button in Help Center triggers Intercom messenger

---

## Day 4 — Fine-Tuned Embedding Models (#106)

**Goal:** Enterprise customers get dramatically better retrieval accuracy by embedding their domain vocabulary into the model itself.

**Feature:** #106 Fine-Tuned Embedding Models

---

### #106 — Fine-Tuned Embedding Models

This is an **enterprise-only feature**. Only available to orgs on an enterprise plan. The implementation has two phases: data collection (automatic, happening from day 1), and fine-tuning execution (triggered manually or by Inngest for eligible orgs).

---

### Phase 1: Training Data Collection (Runs from Day 1 Forward)

Every time a chunk is cited in a response AND receives positive feedback (thumbs up or copy), that (query → chunk) pair is a positive training example. Collect these continuously.

**Database Migration:**

```sql
CREATE TABLE embedding_training_pairs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  query_text TEXT NOT NULL,
  positive_chunk_id UUID NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
  -- Negative examples: other chunks from same search that were NOT cited
  negative_chunk_ids UUID[] NOT NULL DEFAULT '{}',
  signal_type VARCHAR(50) NOT NULL,  -- 'copy' | 'positive_feedback' | 'high_confidence'
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_training_pairs_org ON embedding_training_pairs(org_id, created_at DESC);

-- Track fine-tuning jobs per org
CREATE TABLE embedding_fine_tune_jobs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  status VARCHAR(50) NOT NULL DEFAULT 'pending',
  -- 'pending' | 'collecting_data' | 'training' | 'evaluating' | 'deployed' | 'failed'
  base_model VARCHAR(100) NOT NULL DEFAULT 'text-embedding-3-small',
  fine_tuned_model_id VARCHAR(255),  -- Returned by OpenAI after fine-tuning
  training_pairs_count INTEGER NOT NULL DEFAULT 0,
  eval_score_before FLOAT,  -- Retrieval accuracy before fine-tuning
  eval_score_after FLOAT,   -- Retrieval accuracy after fine-tuning
  triggered_by UUID REFERENCES users(id),
  started_at TIMESTAMP,
  completed_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
```

**Backend — `app/services/training_data_collector.py`:**

```python
async def collect_training_pair(
    query: str,
    cited_chunks: list[Chunk],
    all_retrieved_chunks: list[Chunk],
    org_id: str,
    signal_type: str,
    db: AsyncSession
):
    """
    Called when a positive signal is detected (copy or thumbs up).
    Creates (query, positive_chunk, negative_chunks) training pairs.
    """
    cited_ids = {c.id for c in cited_chunks}
    negative_ids = [c.id for c in all_retrieved_chunks if c.id not in cited_ids][:5]

    for chunk in cited_chunks:
        db.add(EmbeddingTrainingPair(
            org_id=org_id,
            query_text=query[:500],
            positive_chunk_id=chunk.id,
            negative_chunk_ids=negative_ids,
            signal_type=signal_type
        ))
    await db.commit()
```

**Wire up collection:**

```python
# In PATCH /api/chat/messages/{id}/feedback (thumbs up)
if body.feedback == 'positive':
    # Fetch the search results that were used for this message
    query_log = await get_query_log_for_message(message_id)
    source_chunks = await get_source_chunks_for_message(message_id)
    all_chunks = await get_all_retrieved_chunks_for_message(message_id)
    if query_log and source_chunks:
        await collect_training_pair(
            query=query_log.query_text,
            cited_chunks=source_chunks,
            all_retrieved_chunks=all_chunks,
            org_id=current_user.org_id,
            signal_type='positive_feedback',
            db=db
        )

# In POST /api/chat/messages/{id}/copied
# Same logic — collect pair on copy signal
await collect_training_pair(..., signal_type='copy', ...)
```

Store the full list of retrieved chunks per query in `query_logs.retrieved_chunk_ids UUID[]` (add this column):
```sql
ALTER TABLE query_logs ADD COLUMN IF NOT EXISTS retrieved_chunk_ids UUID[] NOT NULL DEFAULT '{}';
```

---

### Phase 2: Fine-Tuning Execution

**Fine-tuning approach:**

Use **OpenAI's fine-tuning API for embeddings** (via `text-embedding-3-small` base) with **contrastive learning** format. Each training example is:

```json
{
  "query": "what is our vacation policy",
  "positive": "Employees are entitled to 15 days of paid vacation per year...",
  "negatives": ["Our engineering team uses React and TypeScript...", "Q3 revenue was $2.1M..."]
}
```

**Backend — `app/services/embedding_fine_tuner.py`:**

```python
from openai import AsyncOpenAI
import json, tempfile

client = AsyncOpenAI()  # Or your existing LLM client

MIN_TRAINING_PAIRS = 50  # Minimum before fine-tuning is worth it
MIN_PAIRS_FOR_ENTERPRISE = 200  # Recommended minimum for good results

async def prepare_training_file(org_id: str, db: AsyncSession) -> str:
    """Export training pairs to JSONL format for OpenAI fine-tuning."""
    pairs = await db.execute(
        select(EmbeddingTrainingPair)
        .where(EmbeddingTrainingPair.org_id == org_id)
        .order_by(EmbeddingTrainingPair.created_at.desc())
        .limit(2000)  # Use latest 2000 pairs
    )

    lines = []
    for pair in pairs.scalars():
        # Fetch chunk content
        positive_chunk = await db.get(Chunk, pair.positive_chunk_id)
        if not positive_chunk:
            continue
        negative_chunks = await db.execute(
            select(Chunk.content).where(Chunk.id.in_(pair.negative_chunk_ids))
        )
        neg_texts = [row.content[:500] for row in negative_chunks]

        lines.append(json.dumps({
            "query": pair.query_text,
            "positive": positive_chunk.content[:500],
            "negatives": neg_texts
        }))

    # Write to temp JSONL file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
        f.write('\n'.join(lines))
        return f.name

async def start_fine_tune_job(org_id: str, triggered_by: str, db: AsyncSession) -> str:
    """
    Kicks off the full fine-tuning pipeline:
    1. Prepare training data
    2. Upload to OpenAI
    3. Create fine-tuning job
    4. Return job ID
    """
    # Check eligibility
    pair_count = await db.scalar(
        select(func.count(EmbeddingTrainingPair.id))
        .where(EmbeddingTrainingPair.org_id == org_id)
    )
    if pair_count < MIN_TRAINING_PAIRS:
        raise ValueError(f"Need at least {MIN_TRAINING_PAIRS} training pairs (have {pair_count})")

    # Create job record
    job = EmbeddingFineTuneJob(
        org_id=org_id,
        triggered_by=triggered_by,
        training_pairs_count=pair_count,
        status='collecting_data',
        started_at=datetime.utcnow()
    )
    db.add(job)
    await db.commit()

    # Run via Inngest (long-running)
    await inngest.send("embeddings/fine-tune", {
        "org_id": org_id,
        "job_id": str(job.id)
    })
    return str(job.id)
```

**Inngest function — `embeddings/fine-tune`:**

```python
@inngest.create_function(
    fn_id="embeddings/fine-tune",
    trigger=inngest.TriggerEvent("embeddings/fine-tune"),
    # Long timeout — fine-tuning can take hours
    timeout="4h"
)
async def run_embedding_fine_tune(ctx, step):
    org_id = ctx.event.data["org_id"]
    job_id = ctx.event.data["job_id"]

    # Step 1: Prepare training file
    training_file_path = await step.run("prepare-data", lambda: prepare_training_file(org_id))
    await update_job_status(job_id, 'training')

    # Step 2: Upload to OpenAI
    upload_response = await step.run("upload-file", lambda: client.files.create(
        file=open(training_file_path, 'rb'),
        purpose='fine-tune'
    ))
    file_id = upload_response.id

    # Step 3: Start fine-tuning
    # NOTE: OpenAI's embedding fine-tuning uses the "custom-embeddings" endpoint
    # This is their contrastive embedding fine-tuning feature
    ft_response = await step.run("start-ft", lambda: client.fine_tuning.jobs.create(
        training_file=file_id,
        model="text-embedding-3-small",
        method={
            "type": "supervised",
            "supervised": {
                "hyperparameters": {"n_epochs": 3}
            }
        }
    ))
    fine_tune_job_id = ft_response.id

    # Step 4: Poll until complete (fine-tuning takes 30min–4hrs)
    async def poll_fine_tune():
        while True:
            status = await client.fine_tuning.jobs.retrieve(fine_tune_job_id)
            if status.status == 'succeeded':
                return status.fine_tuned_model  # e.g. "ft:text-embedding-3-small:org:abc123"
            elif status.status == 'failed':
                raise ValueError(f"Fine-tuning failed: {status.error}")
            await asyncio.sleep(60)  # Poll every minute

    fine_tuned_model_id = await step.run("poll-completion", poll_fine_tune)

    # Step 5: Evaluate — run test queries with old vs new model, compare hit rates
    eval_scores = await step.run("evaluate", lambda: evaluate_embeddings(
        org_id, fine_tuned_model_id
    ))

    # Step 6: If eval score improved, deploy
    if eval_scores['after'] > eval_scores['before']:
        await step.run("deploy", lambda: deploy_fine_tuned_model(org_id, fine_tuned_model_id))
        await update_job_status(job_id, 'deployed',
            fine_tuned_model_id=fine_tuned_model_id,
            eval_score_before=eval_scores['before'],
            eval_score_after=eval_scores['after']
        )
    else:
        await update_job_status(job_id, 'failed',
            reason='Fine-tuned model did not improve over base model'
        )
```

**Evaluation function:**

```python
async def evaluate_embeddings(org_id: str, fine_tuned_model: str) -> dict:
    """
    Take a sample of (query, positive_chunk) pairs.
    For each query, retrieve top-5 with BOTH old and new model.
    Score = % of queries where positive chunk appears in top-5.
    """
    # Sample 50 held-out pairs for evaluation
    test_pairs = await get_test_pairs(org_id, limit=50)

    def hit_at_5(model_id: str, pairs: list) -> float:
        hits = 0
        for pair in pairs:
            # Embed the query
            query_emb = get_embedding(pair.query_text, model=model_id)
            # Get top-5 similar chunks
            top_5 = vector_search_with_model(query_emb, org_id, model_id, top_k=5)
            top_5_ids = {c.id for c in top_5}
            if pair.positive_chunk_id in top_5_ids:
                hits += 1
        return hits / len(pairs)

    score_before = hit_at_5("text-embedding-3-small", test_pairs)
    score_after = hit_at_5(fine_tuned_model, test_pairs)
    return {"before": score_before, "after": score_after}
```

**Deployment — update org embedding model:**

```python
async def deploy_fine_tuned_model(org_id: str, model_id: str):
    """Store fine-tuned model ID in org metadata. All future embeddings use this model."""
    await db.execute(
        update(Organization)
        .where(Organization.id == org_id)
        .values(
            metadata=Organization.metadata.op('||')({
                "embedding_model": model_id,
                "embedding_fine_tuned_at": datetime.utcnow().isoformat()
            })
        )
    )
    await db.commit()
```

**Update all embedding calls to use org's model:**

```python
async def get_embedding(text: str, org_id: Optional[str] = None) -> list[float]:
    """Use org's fine-tuned model if deployed, otherwise use default."""
    model = "text-embedding-3-small"  # Default
    if org_id:
        org = await get_org(org_id)
        model = org.metadata.get("embedding_model", model)

    response = await openai_client.embeddings.create(
        model=model,
        input=text
    )
    return response.data[0].embedding
```

**Admin UI — `app/admin/embeddings/page.tsx`:**

```tsx
export default function EmbeddingsPage() {
  const { data: status } = useQuery(['embedding-status'], () =>
    fetch('/api/admin/embeddings/status').then(r => r.json())
  )

  return (
    <div className="max-w-2xl mx-auto px-4 py-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Custom Embedding Model</h1>
          <p className="text-sm text-muted-foreground">
            Fine-tune the search model on your organization's specific vocabulary.
          </p>
        </div>
        <Badge>{status?.plan === 'enterprise' ? 'Enterprise' : 'Upgrade Required'}</Badge>
      </div>

      {/* Training data progress */}
      <Card>
        <Text className="font-medium mb-2">Training Data Collected</Text>
        <div className="flex items-end gap-2 mb-2">
          <Metric>{status?.training_pairs || 0}</Metric>
          <Text className="text-muted-foreground mb-1">/ {MIN_PAIRS_FOR_ENTERPRISE} recommended pairs</Text>
        </div>
        <ProgressBar
          value={Math.min((status?.training_pairs || 0) / MIN_PAIRS_FOR_ENTERPRISE * 100, 100)}
          className="mb-2"
        />
        <Text className="text-xs text-muted-foreground">
          Training pairs are collected automatically when users copy or rate AI responses.
          More usage = better fine-tuning data.
        </Text>
      </Card>

      {/* Current model status */}
      <Card>
        <Text className="font-medium mb-3">Embedding Model</Text>
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <Text className="text-sm">Current model</Text>
            <Badge>{status?.current_model || 'text-embedding-3-small (default)'}</Badge>
          </div>
          {status?.fine_tuned_at && (
            <div className="flex items-center justify-between">
              <Text className="text-sm">Fine-tuned</Text>
              <Text className="text-sm text-muted-foreground">{formatDate(status.fine_tuned_at)}</Text>
            </div>
          )}
          {status?.eval_improvement && (
            <div className="flex items-center justify-between">
              <Text className="text-sm">Retrieval improvement</Text>
              <Badge color="green">+{Math.round(status.eval_improvement * 100)}% accuracy</Badge>
            </div>
          )}
        </div>
      </Card>

      {/* Fine-tune button */}
      {status?.plan === 'enterprise' && status?.training_pairs >= MIN_TRAINING_PAIRS && (
        <Button
          onClick={startFineTune}
          disabled={status?.active_job}
          className="w-full"
          size="lg"
        >
          {status?.active_job
            ? `Fine-tuning in progress... (${status.active_job.status})`
            : status?.fine_tuned_at
              ? 'Retrain with new data'
              : 'Start fine-tuning'
          }
        </Button>
      )}

      {status?.plan !== 'enterprise' && (
        <div className="border rounded-xl p-4 bg-muted/50 text-center">
          <p className="text-sm text-muted-foreground mb-2">
            Custom embedding models are available on the Enterprise plan.
          </p>
          <Link href="/settings/billing" className="text-sm text-indigo-600 hover:underline">
            Upgrade to Enterprise →
          </Link>
        </div>
      )}
    </div>
  )
}
```

**`GET /api/admin/embeddings/status`:**

```python
@router.get("/admin/embeddings/status")
async def get_embedding_status(current_user: User = Depends(get_admin_user), ...):
    org = await db.get(Organization, current_user.org_id)
    pair_count = await db.scalar(
        select(func.count(EmbeddingTrainingPair.id))
        .where(EmbeddingTrainingPair.org_id == current_user.org_id)
    )
    active_job = await db.execute(
        select(EmbeddingFineTuneJob)
        .where(
            EmbeddingFineTuneJob.org_id == current_user.org_id,
            EmbeddingFineTuneJob.status.in_(['pending', 'collecting_data', 'training', 'evaluating'])
        )
        .order_by(EmbeddingFineTuneJob.created_at.desc())
        .limit(1)
    )

    return {
        "plan": org.plan,
        "training_pairs": pair_count,
        "current_model": org.metadata.get("embedding_model", "text-embedding-3-small (default)"),
        "fine_tuned_at": org.metadata.get("embedding_fine_tuned_at"),
        "eval_improvement": org.metadata.get("embedding_eval_improvement"),
        "active_job": active_job.scalar().__dict__ if active_job.scalar() else None,
    }
```

**`POST /api/admin/embeddings/fine-tune`:**

```python
@router.post("/admin/embeddings/fine-tune")
async def trigger_fine_tune(current_user: User = Depends(get_admin_user), ...):
    org = await db.get(Organization, current_user.org_id)
    if org.plan != 'enterprise':
        raise HTTPException(403, "Fine-tuning requires Enterprise plan")
    job_id = await start_fine_tune_job(current_user.org_id, current_user.id, db)
    return {"job_id": job_id, "status": "started"}
```

**End of Day 4 checklist:**
- [ ] `embedding_training_pairs` table created
- [ ] `embedding_fine_tune_jobs` table created
- [ ] `query_logs.retrieved_chunk_ids` column added
- [ ] `collect_training_pair()` called on positive feedback (thumbs up)
- [ ] `collect_training_pair()` called on copy signal
- [ ] Training pairs accumulate silently from day 1 for all orgs
- [ ] `prepare_training_file()` exports JSONL in OpenAI contrastive format
- [ ] Inngest `embeddings/fine-tune` function with 4hr timeout
- [ ] Polling loop waits for OpenAI fine-tuning job to complete
- [ ] Evaluation compares hit@5 before vs after fine-tuning
- [ ] Model only deployed if eval score improves
- [ ] `get_embedding()` uses org's fine-tuned model when deployed
- [ ] Admin embeddings page shows training pair count + progress bar
- [ ] Fine-tune button only visible for enterprise orgs with ≥50 pairs
- [ ] Active job status shown in UI while training runs
- [ ] Eval improvement percentage shown after deployment
- [ ] Enterprise plan gate with upgrade CTA for other plans
- [ ] `GET /api/admin/embeddings/status` endpoint correct
- [ ] `POST /api/admin/embeddings/fine-tune` triggers Inngest job

---

## Database Migration Sequence

```
Day 1: No new tables — doc summary + TOC stored in existing documents.metadata JSONB
       query_logs.retrieved_chunk_ids UUID[] (for Day 4 training data)

Day 2: CREATE TABLE collections
       ALTER TABLE messages ADD copy_count, first_copied_at, last_copied_at
       ALTER TABLE query_logs ADD time_saved_minutes
       ALTER TABLE organizations ADD industry, company_size, primary_use_case, onboarding_completed_at

Day 3: CREATE TABLE llm_usage_logs
       CREATE TABLE scheduled_reports

Day 4: CREATE TABLE embedding_training_pairs
       CREATE TABLE embedding_fine_tune_jobs
```

---

## Environment Variables Checklist

```bash
# Day 3 — Internal cost dashboard
FOUNDER_USER_IDS=uuid1,uuid2,uuid3   # Comma-separated UUIDs for internal access

# Day 3 — Intercom
NEXT_PUBLIC_INTERCOM_APP_ID=your_app_id

# Day 4 — Fine-tuning (only needed when triggering fine-tune jobs)
OPENAI_API_KEY=sk-...   # Already set if using OpenAI for chat — same key
```

---

## Feature Completion Matrix

| # | Feature | Day | Priority |
|---|---|---|---|
| #7 | Keyboard Shortcuts | 1 | P1 |
| #12 | Global Command Palette (kbar) | 1 | P1 |
| #24 | Document Auto-Summary | 1 | P2 |
| #107 | Automatic Table of Contents | 1 | P2 |
| #35 | Multi-Document Collections | 2 | P2 |
| #59 | Output Quality Signals (copy tracking) | 2 | P2 |
| #73 | Time Savings Calculator | 2 | P2 |
| #97 | Org Metadata Enrichment | 2 | P2 |
| #75 | LLM Cost Dashboard (internal) | 3 | P2 |
| #78 | Rate Limit Dashboard | 3 | P2 |
| #98 | Scheduled Reports | 3 | P2 |
| #95 | Support Chat (Intercom) | 3 | P2 |
| #106 | Fine-Tuned Embedding Models | 4 | P2 |

**Total: 13 features across 4 days.**