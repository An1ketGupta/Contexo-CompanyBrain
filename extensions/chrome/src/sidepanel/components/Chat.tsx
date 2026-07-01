import { useCallback, useEffect, useRef, useState } from 'react'
import {
  type ChatStreamEvent,
  addPageToBrain,
  streamChat,
} from '../lib/api'
import { signOut, SessionExpired } from '../lib/auth'
import type { Session } from '../lib/storage'
import { getActiveTabInfo, scrapeActiveTab } from '../lib/scrape'

type Source = { document_name: string; chunk_id?: string; excerpt?: string }
type Message =
  | { role: 'user'; content: string }
  | { role: 'assistant'; content: string; sources: Source[]; streaming: boolean }

type AddToBrainState =
  | { kind: 'idle' }
  | { kind: 'busy' }
  | { kind: 'success'; label: string; alreadyExisted: boolean }
  | { kind: 'error'; error: string }

export default function Chat({
  session,
  onSignedOut,
  onSessionExpired,
}: {
  session: Session
  onSignedOut: () => void
  onSessionExpired: (e: SessionExpired) => void
}) {
  const [tabInfo, setTabInfo] = useState<{
    url: string
    title: string
    hostname: string
  } | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [addState, setAddState] = useState<AddToBrainState>({ kind: 'idle' })
  const [usePageContext, setUsePageContext] = useState(false)

  const abortRef = useRef<AbortController | null>(null)
  const listRef = useRef<HTMLDivElement>(null)

  // Refresh the page context whenever the side panel re-opens or the user
  // switches tabs. chrome.tabs.onActivated only fires inside windows the
  // extension can see, which is fine for our use case.
  const refreshTabInfo = useCallback(async () => {
    const r = await getActiveTabInfo()
    setTabInfo(r.ok ? r.data : null)
  }, [])

  useEffect(() => {
    void refreshTabInfo()
    const handler = () => void refreshTabInfo()
    chrome.tabs.onActivated.addListener(handler)
    chrome.tabs.onUpdated.addListener(handler)
    return () => {
      chrome.tabs.onActivated.removeListener(handler)
      chrome.tabs.onUpdated.removeListener(handler)
    }
  }, [refreshTabInfo])

  // Autoscroll the conversation as tokens stream in. `scrollTo` (not
  // scrollIntoView) so the scroll position is owned by this container and
  // doesn't fight with the page-context badge above it.
  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight })
  }, [messages])

  async function onAddToBrain() {
    setAddState({ kind: 'busy' })
    try {
      const scrape = await scrapeActiveTab()
      if (!scrape.ok) {
        setAddState({ kind: 'error', error: scrape.error })
        return
      }
      const result = await addPageToBrain(scrape.data)
      setAddState({
        kind: 'success',
        label: result.name ?? scrape.data.title,
        alreadyExisted: !!result.already_existed,
      })
      // Auto-clear the toast after a few seconds so the panel header
      // returns to its idle state without the user having to dismiss it.
      window.setTimeout(() => setAddState({ kind: 'idle' }), 3500)
    } catch (err) {
      if (err instanceof SessionExpired) {
        onSessionExpired(err)
        return
      }
      setAddState({
        kind: 'error',
        error: err instanceof Error ? err.message : 'Failed to add page.',
      })
    }
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!input.trim() || streaming) return

    // Optional "use this page" prefix — give the LLM the page title + URL as
    // a leading sentence. The model will treat it as part of the prompt; if
    // the user later opens a new conversation, the toggle resets.
    let prompt = input.trim()
    if (usePageContext && tabInfo) {
      prompt = `Context — currently viewing: ${tabInfo.title} (${tabInfo.url})\n\n${prompt}`
    }
    setInput('')
    setMessages((prev) => [
      ...prev,
      { role: 'user', content: prompt },
      { role: 'assistant', content: '', sources: [], streaming: true },
    ])
    setStreaming(true)

    const controller = new AbortController()
    abortRef.current = controller
    try {
      for await (const evt of streamChat({
        message: prompt,
        conversationId,
        signal: controller.signal,
      })) {
        applyStreamEvent(evt)
      }
    } catch (err) {
      if (err instanceof SessionExpired) {
        onSessionExpired(err)
        return
      }
      console.error('[CB] chat stream error', err)
      applyStreamEvent({
        type: 'error',
        error: err instanceof Error ? err.message : 'Chat failed.',
      })
    } finally {
      setStreaming(false)
      // Flip the streaming flag on the last assistant message so the
      // typing-cursor stops blinking once the stream finishes.
      setMessages((prev) =>
        prev.map((m, i) =>
          i === prev.length - 1 && m.role === 'assistant'
            ? { ...m, streaming: false }
            : m,
        ),
      )
      abortRef.current = null
    }
  }

  function applyStreamEvent(evt: ChatStreamEvent) {
    // `start` carries the server-assigned conversation_id for new threads;
    // capture it before mutating the message list so follow-up turns thread.
    if (evt.type === 'start' && typeof evt.conversation_id === 'string') {
      if (!conversationId) setConversationId(evt.conversation_id)
      return
    }
    setMessages((prev) => {
      const idx = prev.length - 1
      if (idx < 0 || prev[idx].role !== 'assistant') return prev
      const next = [...prev]
      const cur = next[idx] as Message & { role: 'assistant' }
      if (evt.type === 'token' && typeof evt.text === 'string') {
        next[idx] = { ...cur, content: cur.content + evt.text }
      } else if (evt.type === 'sources' && Array.isArray(evt.sources)) {
        next[idx] = { ...cur, sources: evt.sources as Source[] }
      } else if (evt.type === 'error') {
        const msg =
          (typeof evt.message === 'string' && evt.message) ||
          (typeof evt.error === 'string' && evt.error) ||
          'Something broke while generating.'
        next[idx] = {
          ...cur,
          content: cur.content || `Error: ${msg}`,
          streaming: false,
        }
      }
      return next
    })
  }

  function onCancel() {
    abortRef.current?.abort()
  }

  async function onSignOut() {
    await signOut()
    onSignedOut()
  }

  return (
    <div className="cb-app">
      <header className="cb-header">
        <div className="cb-brand">
          <div className="cb-logo-dot" />
          <span className="cb-brand-text">Nirnaya IQ</span>
        </div>
        <button
          className="cb-btn-ghost"
          onClick={onSignOut}
          title={`Signed in as ${session.user.email}`}
        >
          Sign out
        </button>
      </header>

      {tabInfo ? (
        <div className="cb-page-bar">
          <div className="cb-page-meta">
            <span className="cb-page-host">{tabInfo.hostname}</span>
            <span className="cb-page-title" title={tabInfo.title}>
              {tabInfo.title || tabInfo.url}
            </span>
          </div>
          <div className="cb-page-actions">
            <label className="cb-toggle">
              <input
                type="checkbox"
                checked={usePageContext}
                onChange={(e) => setUsePageContext(e.target.checked)}
              />
              <span>Use as context</span>
            </label>
            <button
              className="cb-btn-primary cb-btn-sm"
              onClick={onAddToBrain}
              disabled={addState.kind === 'busy'}
            >
              {addState.kind === 'busy' ? 'Adding…' : '+ Add to Brain'}
            </button>
          </div>
        </div>
      ) : (
        <div className="cb-page-bar cb-page-bar--disabled">
          <span className="cb-subtle">No readable page on this tab.</span>
        </div>
      )}

      {addState.kind === 'success' && (
        <div className="cb-toast cb-toast--ok">
          {addState.alreadyExisted ? '✓ Already in your brain — ' : '✓ Added — '}
          <strong>{addState.label}</strong>
        </div>
      )}
      {addState.kind === 'error' && (
        <div className="cb-toast cb-toast--err">⚠ {addState.error}</div>
      )}

      <div className="cb-messages" ref={listRef}>
        {messages.length === 0 && (
          <div className="cb-empty">
            <p>Ask your Nirnaya IQ anything.</p>
            <p className="cb-subtle">
              Try: <em>"Draft a Slack update about the Q4 plan"</em> or{' '}
              <em>"What's our refund policy?"</em>
            </p>
          </div>
        )}
        {messages.map((m, i) => (
          <MessageBubble key={i} message={m} />
        ))}
      </div>

      <form onSubmit={onSubmit} className="cb-composer">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              void onSubmit(e)
            }
          }}
          placeholder="Ask Nirnaya IQ…"
          rows={2}
          disabled={streaming}
        />
        {streaming ? (
          <button type="button" className="cb-btn-ghost" onClick={onCancel}>
            Stop
          </button>
        ) : (
          <button
            type="submit"
            className="cb-btn-primary"
            disabled={!input.trim()}
          >
            Send
          </button>
        )}
      </form>
    </div>
  )
}

function renderInline(text: string): React.ReactNode {
  const segments = text.split(/(\*\*[^*]+\*\*)/g)
  return segments.map((seg, i) =>
    seg.startsWith('**') && seg.endsWith('**')
      ? <strong key={i}>{seg.slice(2, -2)}</strong>
      : seg,
  )
}

function renderMarkdown(text: string): React.ReactNode {
  const lines = text.split('\n')
  const nodes: React.ReactNode[] = []
  let listItems: React.ReactNode[] = []
  let k = 0

  function flushList() {
    if (listItems.length > 0) {
      nodes.push(<ul key={k++} className="cb-md-ul">{listItems}</ul>)
      listItems = []
    }
  }

  for (const line of lines) {
    const listMatch = line.match(/^[\*\-]\s+(.+)/)
    if (listMatch) {
      listItems.push(<li key={listItems.length}>{renderInline(listMatch[1])}</li>)
      continue
    }
    flushList()
    if (line.trim() === '') continue
    if (line.startsWith('### ') || line.startsWith('## ') || line.startsWith('# ')) {
      const content = line.replace(/^#{1,3}\s+/, '')
      nodes.push(<p key={k++} className="cb-md-h">{renderInline(content)}</p>)
    } else {
      nodes.push(<p key={k++} className="cb-md-p">{renderInline(line)}</p>)
    }
  }
  flushList()
  return nodes
}

function MessageBubble({ message }: { message: Message }) {
  if (message.role === 'user') {
    return (
      <div className="cb-msg cb-msg--user">
        <div className="cb-msg-bubble cb-msg-bubble--user">{message.content}</div>
      </div>
    )
  }
  return (
    <div className="cb-msg cb-msg--asst">
      <div className="cb-msg-bubble cb-msg-bubble--asst">
        {message.content
          ? renderMarkdown(message.content)
          : message.streaming
            ? <span className="cb-subtle">…</span>
            : null}
        {message.streaming && <span className="cb-cursor">▍</span>}
      </div>
    </div>
  )
}
