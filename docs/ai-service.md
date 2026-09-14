# AI service retries

`AIService` implements the shared service interface without modifying `ai/`.
All source calls go through the supplied public fetchers, forwarding the
shared HTTP client and `Settings.max_sources_per_query`. Only DuckDuckGo ignores
the client internally; Tavily and Serper use it.

## Retry policy

- `retry_max_attempts` includes the initial attempt. Delays start at
  `retry_initial_delay_seconds`, double, and stop increasing at
  `retry_max_delay_seconds`. There is no sleep after the final attempt.
- Retry transport errors, timeouts, HTTP 408/429, and HTTP 5xx responses.
- Do not retry other HTTP error statuses, invalid input, missing dependencies,
  recognized missing-credential/provider configuration failures, or arbitrary
  programming exceptions.
- Provider wrappers preserve causes where available. Classification inspects
  those causes and SDK status attributes without importing optional SDKs.
  Unknown `ProviderError` failures receive bounded retries. Known untyped
  configuration messages from the supplied module are recognized; this is not
  a universal classifier for every third-party exception.
- Logs include operation, attempt, budget, and exception type, not raw exception
  text, prompts, credentials, or request URLs/bodies.

## HTTP and fetch retries

The shared client uses a custom HTTPX transport that retries each complete
request, including body-read failures. This covers Wikipedia summary failures
before the supplied fetcher catches and skips them. Failed responses are closed
before waiting, and search POST bodies are replayed for retries.

An exhausted transport retry raises `HTTPRetryExhausted`. Its presence in the
exception cause chain prevents the outer source wrapper from multiplying the
HTTP retry budget. Wikipedia can still return partial/empty results after
skipping an exhausted summary request, as defined by the supplied module.

The transport buffers responses and is intended for these small JSON/XML
fetches and search POSTs, not arbitrary mutating POSTs or streaming responses.
HTTP-level retries require a client from `open_source_client()`; an arbitrary
injected client does not automatically gain transport retries. Tests inject
`httpx.MockTransport` using the optional `transport_factory` constructor argument.

## Synthesis and cancellation

Each synthesis attempt calls public `ai.synthesize` through `asyncio.to_thread`,
allowing the event loop to continue while the synchronous provider runs.
Transient synthesis errors use the same bounded backoff policy.

Cancellation propagates during calls or backoff without starting more retries.
Cancelling synthesis does not terminate a running SDK thread or guarantee that
the provider request was not processed. SDK-internal retries and timeouts remain
those of the supplied adapters; the service budget counts calls to `ai.synthesize`,
not hidden SDK HTTP attempts. DuckDuckGo likewise owns its internal requests.

Source deadlines are owned by the orchestrator, rather than duplicated here.
The HTTP client's timeout applies to network operations, not the entire sequence
of requests and backoffs. Integration with orchestration and live credentials
must be verified separately.

## Verification

Run `uv run python -m pytest tests/test_ai_service.py -v` for offline service
tests. They cover routing, limits, shared-client forwarding, HTTP status and
body failures, Wikipedia summaries, POST replay, exhaustion, capped backoff,
permanent errors, safe logs, synthesis retries, worker-thread execution, and
cancellation. No live provider SDKs or credentials are needed for these tests.


## Source API compatibility

The shared client follows redirects, with a maximum of five redirects per
request. This handles the supplied arXiv fetcher's HTTP endpoint redirecting to
HTTPS. Redirected requests still use the retry transport and remain inside the
orchestrator's per-source deadline. Redirect loops fail without outer retries.

Requests identify the application as `ResearchAssistant/0.1.0`, with the project
GitHub URL in the User-Agent. This applies to Wikipedia search and summaries as
well as other fetchers using the shared client. The supplied `ai/` files remain
unchanged.

Offline regression tests exercise the actual Wikipedia and arXiv fetchers with
mock HTTP responses: identifying headers, HTTP-to-HTTPS redirection, query
preservation, retry after a redirected 503, and redirect limits. Existing tests
verify that 403 responses are not retried.

These tests establish client behavior, not live API availability. A descriptive
User-Agent addresses Wikimedia's identification requirement but does not prove
that it caused the reported 403; live verification remains outstanding.
