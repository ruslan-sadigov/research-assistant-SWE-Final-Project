import { useState } from 'react'
import './App.css'

const API_URL = 'http://127.0.0.1:8000/ask'
const ALL_SOURCES = ['wiki', 'arxiv', 'web']

function App() {
  const [question, setQuestion] = useState('')
  const [sources, setSources] = useState(ALL_SOURCES)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [result, setResult] = useState(null)

  function toggleSource(source) {
    setSources((current) =>
      current.includes(source)
        ? current.filter((item) => item !== source)
        : [...current, source]
    )
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

      setResult(await response.json())
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
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
  )
}

export default App
