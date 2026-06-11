/**
 * Thin FastAPI client.
 *
 * Every call goes through `authedFetch`, which:
 *   1. Resolves the current session (refreshing it if close to expiry).
 *   2. Attaches `Authorization: Bearer <jwt>`.
 *   3. On 401, refreshes once and retries — handles the case where the JWT
 *      expired between the leeway check and the actual request.
 */
import { SessionExpired, getValidSession } from './auth'
import { type Session, clearSession } from './storage'

const API_URL = (import.meta.env.VITE_API_URL ?? '').replace(/\/$/, '')

if (!API_URL) {
  // Configured at build time, so this fires immediately in dev if you
  // forgot the .env. Helps avoid silently sending requests to "" + "/...".
  console.warn('[CB] VITE_API_URL is empty — set it in .env')
}

export type FromUrlResponse = {
  doc_id: string
  status: string
  already_existed?: boolean
  name?: string
}

async function authedFetch(
  path: string,
  init: RequestInit = {},
  attempt = 0,
): Promise<Response> {
  let session: Session
  try {
    session = await getValidSession()
  } catch (e) {
    if (e instanceof SessionExpired) throw e
    throw e
  }

  const headers = new Headers(init.headers)
  headers.set('Authorization', `Bearer ${session.access_token}`)
  if (init.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }

  const res = await fetch(`${API_URL}${path}`, { ...init, headers })
  if (res.status === 401 && attempt === 0) {
    // Force the next call through the refresh path.
    await clearSession()
    throw new SessionExpired()
  }
  return res
}

export async function addPageToBrain(payload: {
  url: string
  title: string
  hostname: string
  content: string
}): Promise<FromUrlResponse> {
  const res = await authedFetch('/documents/from-url', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const detail = await readErrorDetail(res)
    throw new Error(detail || `Add to Brain failed (${res.status})`)
  }
  return (await res.json()) as FromUrlResponse
}

// SSE event payloads from /chat/stream. Backend emits: start, searching,
// searched, sources, token, done, error (plus a few orchestrator-internal
// kinds we don't render). Extra fields on each type are ignored — TS narrows
// on `type` and we destructure only what the UI needs.
export type ChatStreamEvent =
  | { type: 'start'; conversation_id: string }
  | { type: 'searching'; query?: string }
  | { type: 'searched'; query?: string; count?: number }
  | { type: 'sources'; sources: Array<{ doc_name: string; chunk_id?: string; excerpt?: string }> }
  | { type: 'token'; text: string }
  | { type: 'done'; message_id: string; tool_calls?: number }
  | { type: 'error'; message?: string; code?: string; error?: string }
  | { type: string; [key: string]: unknown }

export async function* streamChat(args: {
  message: string
  conversationId: string | null
  signal?: AbortSignal
}): AsyncGenerator<ChatStreamEvent> {
  const res = await authedFetch('/chat/stream', {
    method: 'POST',
    body: JSON.stringify({
      message: args.message,
      conversation_id: args.conversationId,
    }),
    signal: args.signal,
  })
  if (!res.ok || !res.body) {
    throw new Error(`Chat stream failed (${res.status})`)
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  // Standard SSE parser: events are separated by blank lines, fields by
  // newlines, `data:` lines accumulate then JSON.parse on dispatch.
  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })

    let sep: number
    while ((sep = buf.indexOf('\n\n')) !== -1) {
      const raw = buf.slice(0, sep)
      buf = buf.slice(sep + 2)
      const dataLines = raw
        .split('\n')
        .filter((l) => l.startsWith('data:'))
        .map((l) => l.slice(5).trimStart())
      if (dataLines.length === 0) continue
      const dataText = dataLines.join('\n')
      if (dataText === '[DONE]') return
      try {
        const evt = JSON.parse(dataText) as ChatStreamEvent
        yield evt
      } catch (err) {
        console.warn('[CB] could not parse SSE chunk', err, dataText)
      }
    }
  }
}

async function readErrorDetail(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: string }
    return body.detail || ''
  } catch {
    return ''
  }
}
