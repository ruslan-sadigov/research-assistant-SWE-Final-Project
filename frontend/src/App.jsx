import { useEffect, useState } from 'react'
import './App.css'

const API_URL = 'http://127.0.0.1:8000/ask'
const ALL_SOURCES = ['wiki', 'arxiv', 'web']
const HISTORY_KEY = 'research-assistant-history'
const HISTORY_LIMIT = 30

function loadHistory() {
  try {
    const raw = localStorage.getItem(HISTORY_KEY)
    return raw ? JSON.parse(raw) : []
  } catch {
    return []
  }
}

function saveHistory(history) {
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(history))
  } catch {
    // Private browsing or full storage: history just won't persist.
  }
}

function App() {
  const [question, setQuestion] = useState('')
  const [sources, setSources] = useState(ALL_SOURCES)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [result, setResult] = useState(null)
  const [history, setHistory] = useState([])
  const [activeId, setActiveId] = useState(null)

  useEffect(() => {
    setHistory(loadHistory())
  }, [])

  function toggleSource(source) {
    setSources((current) =>
      current.includes(source)
        ? current.filter((item) => item !== source)
        : [...current, source]
    )
  }

  function handleTilt(event) {
    const rect = event.currentTarget.getBoundingClientRect()
    const px = (event.clientX - rect.left) / rect.width
    const py = (event.clientY - rect.top) / rect.height
    const style = event.currentTarget.style
    style.setProperty('--rx', `${(0.5 - py) * 14}deg`)
    style.setProperty('--ry', `${(px - 0.5) * 14}deg`)
    style.setProperty('--mx', `${px * 100}%`)
    style.setProperty('--my', `${py * 100}%`)
  }

  function resetTilt(event) {
    const style = event.currentTarget.style
    style.setProperty('--rx', '0deg')
    style.setProperty('--ry', '0deg')
    style.setProperty('--mx', '50%')
    style.setProperty('--my', '15%')
  }

  function startNewChat() {
    setQuestion('')
    setResult(null)
    setError(null)
    setActiveId(null)
  }

  function loadHistoryItem(item) {
    setActiveId(item.id)
    setQuestion(item.question)
    setSources(item.sources)
    setResult(item.result)
    setError(null)
  }

  function deleteHistoryItem(id) {
    setHistory((current) => {
      const next = current.filter((item) => item.id !== id)
      saveHistory(next)
      return next
    })
    if (id === activeId) {
      startNewChat()
    }
  }

  async function handleSubmit(event) {
    event.preventDefault()
    setLoading(true)
    setError(null)
    setResult(null)

    try {
      const response = await fetch(API_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question, sources }),
      })

      if (!response.ok) {
        const body = await response.json().catch(() => null)
        throw new Error(body?.detail || `Request failed with status ${response.status}`)
      }

      const data = await response.json()
      setResult(data)

      const entry = {
        id: crypto.randomUUID(),
        question: data.question,
        sources,
        result: data,
        createdAt: Date.now(),
      }
      setHistory((current) => {
        const next = [entry, ...current].slice(0, HISTORY_LIMIT)
        saveHistory(next)
        return next
      })
      setActiveId(entry.id)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <>
      <div className="grid-floor" aria-hidden="true" />
      <div className="bg-orb bg-orb-1" aria-hidden="true" />
      <div className="bg-orb bg-orb-2" aria-hidden="true" />
      <div className="bg-orb bg-orb-3" aria-hidden="true" />

      <div className="layout">
        <aside className="history-panel">
          <h2>History</h2>
          <button type="button" className="new-chat" onClick={startNewChat}>
            + New question
          </button>

          {history.length === 0 ? (
            <p className="history-empty">Your past questions will appear here.</p>
          ) : (
            <ul className="history-list">
              {history.map((item) => (
                <li
                  key={item.id}
                  className={`history-item ${item.id === activeId ? 'active' : ''}`}
                  onClick={() => loadHistoryItem(item)}
                >
                  <span className="history-question">{item.question}</span>
                  <button
                    type="button"
                    className="history-delete"
                    aria-label="Delete this question"
                    onClick={(event) => {
                      event.stopPropagation()
                      deleteHistoryItem(item.id)
                    }}
                  >
                    &times;
                  </button>
                </li>
              ))}
            </ul>
          )}
        </aside>

        <div className="app-shell" onMouseMove={handleTilt} onMouseLeave={resetTilt}>
          <main className="app">
            <h1>Research Assistant</h1>

            <form onSubmit={handleSubmit}>
              <input
                type="text"
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                placeholder="Ask a research question..."
                required
              />

              <div className="sources">
                {ALL_SOURCES.map((source) => (
                  <label key={source}>
                    <input
                      type="checkbox"
                      checked={sources.includes(source)}
                      onChange={() => toggleSource(source)}
                    />
                    {source}
                  </label>
                ))}
              </div>

              <button type="submit" disabled={loading || sources.length === 0}>
                {loading ? (
                  <>
                    <span className="spinner" aria-hidden="true" />
                    Researching...
                  </>
                ) : (
                  'Ask'
                )}
              </button>
            </form>

            {error && <p className="error">{error}</p>}

            {result && (
              <section className="result">
                <h2>Q: {result.question}</h2>

                {result.answer ? (
                  <p>{result.answer}</p>
                ) : (
                  <p className="muted">No answer could be produced.</p>
                )}

                {result.citations.length > 0 && (
                  <>
                    <h3>References</h3>
                    <ol>
                      {result.citations.map((citation) => (
                        <li key={citation.index}>
                          ({citation.origin}) {citation.title} —{' '}
                          <a href={citation.url} target="_blank" rel="noreferrer">
                            {citation.url}
                          </a>
                        </li>
                      ))}
                    </ol>
                  </>
                )}

                {result.warnings.length > 0 && (
                  <>
                    <h3>Warnings</h3>
                    <ul className="warnings">
                      {result.warnings.map((warning, index) => (
                        <li key={index}>{warning}</li>
                      ))}
                    </ul>
                  </>
                )}
              </section>
            )}
          </main>
        </div>
      </div>
    </>
  )
}

export default App
