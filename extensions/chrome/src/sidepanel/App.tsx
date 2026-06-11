import { useEffect, useState } from 'react'
import { SessionExpired } from './lib/auth'
import { type Session, loadSession } from './lib/storage'
import Login from './components/Login'
import Chat from './components/Chat'

type AuthState =
  | { kind: 'loading' }
  | { kind: 'anon' }
  | { kind: 'authed'; session: Session }

export default function App() {
  const [state, setState] = useState<AuthState>({ kind: 'loading' })

  useEffect(() => {
    void loadSession().then((s) => {
      setState(s ? { kind: 'authed', session: s } : { kind: 'anon' })
    })

    // React to sign-out (or token revocation) from other side-panel instances
    // or background refresh failures — the storage event fires across all
    // pages of the extension at once.
    const onChange = (
      changes: { [key: string]: chrome.storage.StorageChange },
      area: chrome.storage.AreaName,
    ) => {
      if (area !== 'local') return
      if (!changes['cb.session.v1']) return
      const next = changes['cb.session.v1'].newValue as Session | undefined
      setState(next ? { kind: 'authed', session: next } : { kind: 'anon' })
    }
    chrome.storage.onChanged.addListener(onChange)
    return () => chrome.storage.onChanged.removeListener(onChange)
  }, [])

  if (state.kind === 'loading') {
    return <div className="cb-loading">Loading…</div>
  }
  if (state.kind === 'anon') {
    return <Login onAuthed={(session) => setState({ kind: 'authed', session })} />
  }
  return (
    <Chat
      session={state.session}
      onSignedOut={() => setState({ kind: 'anon' })}
      onSessionExpired={(e: SessionExpired) => {
        console.warn('[CB] session expired:', e.message)
        setState({ kind: 'anon' })
      }}
    />
  )
}
