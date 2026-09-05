import { FormEvent, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './styles.css'

type Connections = Record<'gmail' | 'github' | 'notion', boolean>

type Session = {
  guest?: boolean
  user: { email: string; name: string | null }
  connections: Connections
}

type Answer = { answer: string; sources_used: string[] }

const sourceDetails = [
  { key: 'github', name: 'GitHub', description: 'Repository activity and code context', href: '/connect/github', icon: '<>' },
  { key: 'gmail', name: 'Gmail', description: 'Read-only messages and updates', href: '/auth/login', icon: 'G' },
  { key: 'notion', name: 'Notion', description: 'Pages you explicitly authorize', href: '/connect/notion', icon: 'N' },
] as const

async function getError(response: Response): Promise<string> {
  const body = await response.json().catch(() => null)
  return body?.detail || 'Something went wrong. Please try again.'
}

function App() {
  const [session, setSession] = useState<Session | null>(null)
  const [loading, setLoading] = useState(true)
  const [question, setQuestion] = useState('')
  const [repo, setRepo] = useState('')
  const [asking, setAsking] = useState(false)
  const [answer, setAnswer] = useState<Answer | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetch('/api/me')
      .then(async (response) => {
        if (response.status === 401) return null
        if (!response.ok) throw new Error(await getError(response))
        return response.json() as Promise<Session>
      })
      .then(setSession)
      .catch((reason: Error) => setError(reason.message))
      .finally(() => setLoading(false))
  }, [])

  async function ask(event: FormEvent) {
    event.preventDefault()
    if (!question.trim()) return
    setAsking(true)
    setError(null)
    setAnswer(null)
    try {
      const response = await fetch('/api/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question, repo: repo.trim() || null }),
      })
      if (!response.ok) throw new Error(await getError(response))
      setAnswer(await response.json() as Answer)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not complete that request.')
    } finally {
      setAsking(false)
    }
  }

  if (loading) return <main className="loading">Loading your workspace…</main>

  if (!session) {
    return <main className="landing">
      <div className="glow" />
      <section className="hero-card">
        <p className="eyebrow">LIVE DATA · AGENTIC RAG</p>
        <h1>Your connected work, <span>actually useful.</span></h1>
        <p className="hero-copy">Ask a question once. The agent selects only your connected sources, retrieves live evidence, and answers with citations.</p>
        {error && <p className="error">{error}</p>}
        <a className="primary-button" href="/auth/login/github">Continue with GitHub <span>→</span></a>
        <a className="guest-button" href="/auth/guest">Continue as guest <span>→</span></a>
        <p className="fine-print">Guest mode reads public GitHub repositories only. Sign in with GitHub to connect Gmail, GitHub account data, or Notion.</p>
      </section>
    </main>
  }

  const isGuest = Boolean(session.guest)
  const firstName = isGuest ? 'Guest' : (session.user.name?.split(' ')[0] || session.user.email.split('@')[0])
  const connectedCount = Object.values(session.connections).filter(Boolean).length

  return <main className="app-shell">
    <header>
      <a className="brand" href="/">◈ <span>Live Data Agent</span></a>
      <div className="profile"><span>{firstName.slice(0, 1).toUpperCase()}</span><div><strong>{firstName}</strong><small>{isGuest ? 'Public repositories only' : session.user.email}</small></div>{isGuest && <a href="/auth/login/github">Sign in with GitHub</a>}<a href="/auth/logout">Log out</a></div>
    </header>

    <section className="welcome-row">
      <div><p className="eyebrow">{isGuest ? 'GUEST WORKSPACE' : 'YOUR CONNECTED WORKSPACE'}</p><h1>{isGuest ? 'Explore a repository, no account required.' : `Good to see you, ${firstName}.`}</h1><p>{isGuest ? 'Ask questions about public GitHub code without connecting any personal sources.' : 'Ask across your live sources without manually switching tabs.'}</p></div>
      <div className="connection-stat"><strong>{isGuest ? '0' : connectedCount}</strong><span>{isGuest ? 'personal sources<br />connected' : 'of 3 sources<br />connected'}</span></div>
    </section>

    <section className="sources" aria-label="Connected sources">
      {sourceDetails.map((source) => {
        const isConnected = session.connections[source.key]
        return <article className={`source-card ${isConnected ? 'connected' : ''}`} key={source.key}>
          <div className="source-icon">{source.icon}</div>
          <div><h2>{source.name}</h2><p>{isGuest && source.key === 'github' ? 'Public repository questions only' : source.description}</p></div>
          {isConnected ? <span className="status"><i />Connected</span> : isGuest ? <span className="guest-status">{source.key === 'github' ? 'Public access only' : 'Sign in to connect'}</span> : <a className="connect" href={source.href}>Connect →</a>}
        </article>
      })}
    </section>

    <section className="ask-panel">
      <div className="ask-heading"><div><p className="eyebrow">{isGuest ? 'PUBLIC GITHUB EXPLORER' : 'ASK YOUR SOURCES'}</p><h2>What do you need to know?</h2></div><span className="read-only">● {isGuest ? 'Public GitHub only' : 'Read-only retrieval'}</span></div>
      <form onSubmit={ask}>
        <textarea value={question} onChange={(event) => setQuestion(event.target.value)} placeholder={isGuest ? 'For example: What technologies does this repository use?' : 'For example: What changed in the project this week, and what do I need to know?'} rows={4} />
        <div className="form-footer"><input value={repo} onChange={(event) => setRepo(event.target.value)} placeholder={isGuest ? 'Public GitHub repository URL' : 'GitHub repo, if relevant (owner/repo)'} aria-label="GitHub repository" /><button className="primary-button" disabled={asking}>{asking ? 'Finding evidence…' : 'Ask agent'} <span>→</span></button></div>
      </form>
      {error && <p className="error">{error}</p>}
    </section>

    {answer && <section className="answer-panel"><div className="answer-meta"><span>AGENT ANSWER</span><div>{answer.sources_used.map((source) => <b key={source}>{source}</b>)}</div></div><article>{answer.answer}</article></section>}
  </main>
}

createRoot(document.getElementById('root')!).render(<App />)
