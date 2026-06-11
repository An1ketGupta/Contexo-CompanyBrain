/**
 * Thin chrome.storage.local wrapper. We use `local` (not `sync`) for auth
 * tokens — `sync` would replicate refresh tokens across the user's Chrome
 * profiles, which expands the blast radius if any one device is compromised.
 */
const KEY = 'cb.session.v1'

export type Session = {
  access_token: string
  refresh_token: string
  // Epoch seconds. Refresh ~60s before this.
  expires_at: number
  user: { id: string; email: string }
}

export async function loadSession(): Promise<Session | null> {
  const obj = await chrome.storage.local.get(KEY)
  const s = obj[KEY]
  if (!s || typeof s !== 'object') return null
  if (!s.access_token || !s.refresh_token || !s.expires_at) return null
  return s as Session
}

export async function saveSession(session: Session): Promise<void> {
  await chrome.storage.local.set({ [KEY]: session })
}

export async function clearSession(): Promise<void> {
  await chrome.storage.local.remove(KEY)
}
