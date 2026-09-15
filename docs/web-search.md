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

## Verification (2026-09-16)

After the local credential was corrected, the agent's web-only live check
returned three results in 1.35 seconds with no warning. It made no Gemini call.
The earlier 2026-09-15 check returned HTTP 401; that authentication blocker is
resolved.

The user then verified the real CLI with the solar-panel question above:
Tavily returned HTTP 200 and three sources in 0.530 seconds; Gemini returned
HTTP 200 and the CLI printed an answer with three web references.

A subsequent user-run combined query, `Photovoltaic effect`, succeeded with all
three providers. See [live integration results](live-integration.md).

Offline tests exercise the actual supplied Tavily adapter with mock HTTP:
request payload, result mapping, missing-URL filtering, transient retries,
and non-retry of 401 responses. No keys are included in tests or documentation.
