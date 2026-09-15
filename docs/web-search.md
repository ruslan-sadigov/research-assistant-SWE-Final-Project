# Web-search setup and verification

The default provider is Tavily. The supplied adapter calls its search endpoint
through the shared HTTPX client; no Tavily SDK or new dependency is required.
Calls still go through `ai.fetch_web`. The supplied `ai/` files are unchanged.

## Configure locally

Create an account at [Tavily](https://app.tavily.com), then copy an API key from
the dashboard. Set these entries in the existing local `.env` file:

```dotenv
WEB_SEARCH_PROVIDER=tavily
TAVILY_API_KEY=your-tavily-key
```

Replace the placeholder with the actual key, normally beginning with `tvly-`.
Keep the Gemini settings already configured for synthesis. Do not commit `.env`
or share keys in chat. Existing shell environment values override `.env`.

Tavily currently offers 1,000 free credits per month without a credit card;
basic search costs one credit per request. Repeated live requests and retries
can consume quota. Check the dashboard for remaining usage and the
[current pricing](https://docs.tavily.com/documentation/api-credits).

## Check the CLI

```powershell
uv run python -m researcher ask "How do solar panels convert sunlight into electricity?" --sources web --no-cache
```

Look for `source=web status=ok` and a positive result count. A subsequent Gemini
503 is a synthesis failure, not a failed web search. Once Gemini is available,
the same command should print an answer and references.

To check combined collection:

```powershell
uv run python -m researcher ask "Photovoltaic effect" --sources wiki,arxiv,web
```

Repeat without `--no-cache` to check cache hits. Cached evidence skips new source
requests, but Gemini synthesis still runs for each invocation.

## Current verification

On 2026-09-15, one live web-only collection with retries disabled returned
HTTP 401 and zero results in 0.54 seconds. No Gemini request was made. The local
key did not match the prefix used in Tavily's documented examples; its contents
were not printed or copied into project files. Replace it with a key from the
Tavily dashboard and repeat verification before marking live setup complete.

Offline tests exercise the actual supplied Tavily adapter with mock HTTP:
request payload, result mapping, missing-URL filtering, transient retries,
and non-retry of 401 responses. These establish application behavior but do
not verify that a real credential is accepted by the service.
