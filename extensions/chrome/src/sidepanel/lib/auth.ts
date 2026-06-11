/**
 * Direct Supabase REST auth. No @supabase/supabase-js — it'd add ~80 KB
 * to the side panel bundle just to wrap two fetch calls and run an
 * EventSource we don't need (the side panel has its own session lifecycle
 * driven by chrome.storage events).
 *
 * Token refresh strategy:
 *   - On API client init, if `expires_at` is within REFRESH_LEEWAY_SECONDS,
 *     refresh once and retry.
 *   - On 401 from FastAPI, refresh and retry once.
 *   - Refresh failures (refresh_token revoked / expired) clear the session
 *     and bubble a typed `SessionExpired` so the UI can swap to the login
 *     screen instead of looping.
 */
import { type Session, clearSession, loadSession, saveSession } from './storage'

const SUPABASE_URL = (import.meta.env.VITE_SUPABASE_URL ?? '').replace(/\/$/, '')
const SUPABASE_ANON_KEY = import.meta.env.VITE_SUPABASE_ANON_KEY ?? ''
const REFRESH_LEEWAY_SECONDS = 60

export class SessionExpired extends Error {
  constructor() {
    super('Session expired. Please sign in again.')
    this.name = 'SessionExpired'
  }
}

type SupabaseTokenResponse = {
  access_token: string
  refresh_token: string
  expires_in: number
  expires_at?: number
  user: { id: string; email: string }
}

function assertConfigured(): void {
  if (!SUPABASE_URL || !SUPABASE_ANON_KEY) {
    throw new Error(
      'Extension is missing VITE_SUPABASE_URL / VITE_SUPABASE_ANON_KEY. ' +
        'Copy .env.example to .env and fill in the values.',
    )
  }
}

export async function signInWithPassword(
  email: string,
  password: string,
): Promise<Session> {
  assertConfigured()
  const res = await fetch(`${SUPABASE_URL}/auth/v1/token?grant_type=password`, {
    method: 'POST',
    headers: {
      apikey: SUPABASE_ANON_KEY,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ email, password }),
  })
  if (!res.ok) {
    const detail = await safeReadError(res)
    throw new Error(detail || `Sign-in failed (${res.status})`)
  }
  const json = (await res.json()) as SupabaseTokenResponse
  const session = tokenResponseToSession(json)
  await saveSession(session)
  return session
}

export async function signOut(): Promise<void> {
  const s = await loadSession()
  if (s) {
    // Best-effort revocation. The user's real exit signal is that we cleared
    // local state; if the Supabase call fails (network), they're still
    // signed out as far as the extension is concerned.
    try {
      await fetch(`${SUPABASE_URL}/auth/v1/logout`, {
        method: 'POST',
        headers: {
          apikey: SUPABASE_ANON_KEY,
          Authorization: `Bearer ${s.access_token}`,
        },
      })
    } catch {
      /* noop */
    }
  }
  await clearSession()
}

export async function getValidSession(): Promise<Session> {
  const s = await loadSession()
  if (!s) throw new SessionExpired()
  const now = Math.floor(Date.now() / 1000)
  if (s.expires_at - now > REFRESH_LEEWAY_SECONDS) return s
  return refreshSession(s)
}

async function refreshSession(prev: Session): Promise<Session> {
  assertConfigured()
  const res = await fetch(
    `${SUPABASE_URL}/auth/v1/token?grant_type=refresh_token`,
    {
      method: 'POST',
      headers: {
        apikey: SUPABASE_ANON_KEY,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ refresh_token: prev.refresh_token }),
    },
  )
  if (!res.ok) {
    await clearSession()
    throw new SessionExpired()
  }
  const json = (await res.json()) as SupabaseTokenResponse
  const session = tokenResponseToSession(json)
  await saveSession(session)
  return session
}

function tokenResponseToSession(json: SupabaseTokenResponse): Session {
  const issuedAt = Math.floor(Date.now() / 1000)
  return {
    access_token: json.access_token,
    refresh_token: json.refresh_token,
    expires_at: json.expires_at ?? issuedAt + json.expires_in,
    user: { id: json.user.id, email: json.user.email },
  }
}

async function safeReadError(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { error_description?: string; msg?: string; error?: string }
    return body.error_description || body.msg || body.error || ''
  } catch {
    return ''
  }
}
