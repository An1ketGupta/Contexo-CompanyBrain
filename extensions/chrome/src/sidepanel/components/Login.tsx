import { useState } from 'react'
import { signInWithPassword } from '../lib/auth'
import type { Session } from '../lib/storage'

export default function Login({ onAuthed }: { onAuthed: (s: Session) => void }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const session = await signInWithPassword(email.trim(), password)
      onAuthed(session)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Sign-in failed.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="cb-screen">
      <div className="cb-brand">
        <div className="cb-logo-dot" />
        <h1>Company Brain</h1>
      </div>
      <p className="cb-subtle">Sign in to access your knowledge base.</p>

      <form onSubmit={submit} className="cb-form">
        <label>
          <span>Email</span>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="email"
            required
          />
        </label>
        <label>
          <span>Password</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
          />
        </label>

        {error && <div className="cb-error">{error}</div>}

        <button type="submit" className="cb-btn-primary" disabled={busy}>
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
      </form>

      <p className="cb-foot">
        Need an account?{' '}
        <a
          href="http://localhost:3000/signup"
          target="_blank"
          rel="noreferrer"
        >
          Open the web app
        </a>
      </p>
    </div>
  )
}
