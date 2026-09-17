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
- Logs include operation, attempt, budget, exception type, and numeric HTTP status (or None), not raw exception
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


## Live arXiv diagnostic (2026-09-14)

A single source fetch for `quantum computing`, with retries disabled, received
an HTTP 301 redirect from the HTTP endpoint, followed by HTTP 429 after 16.11
seconds. The diagnostic used the application service and supplied arXiv fetcher,
with a 30-second HTTP timeout and 35-second overall cap. It did not call Gemini.

This confirms a rate-limit response for this run, not a general arXiv outage or
the exact cause of previous timeouts. The scope of the limit (client, shared IP,
or service traffic) remains unknown. Avoid repeated live retries; use the
working sources and bounded per-source deadlines while arXiv is unavailable.

Retry warnings now include `http_status`, including statuses nested inside
provider wrappers. Missing HTTP statuses are logged as `None`. Status extraction
handles cyclic exception chains and does not log exception messages or response
bodies. Offline tests cover 403, 429, 503, SDK-style error codes, wrapped failures,
and transport failures without HTTP responses.


## arXiv Atom error responses

The arXiv API documents errors as Atom entries whose identifiers point to
`arxiv.org/api/errors`. The supplied parser can turn these into ordinary sources
when the HTTP response is successful. `AIService` now rejects such results with
a generic `ProviderError` after fetching, without retrying the error feed.
The orchestrator marks that source as failed, so the error is not cached or
passed to synthesis. Detection uses the arXiv host and error path, rather than
the title: a real paper titled "Error" remains valid evidence.

Offline tests cover HTTP and HTTPS identifiers, non-retry behavior, safe error
messages, and integration through the orchestrator and researcher. The supplied
`ai/` module is unchanged. Report the parser limitation to the instructor.
See the [official error-feed documentation](https://info.arxiv.org/help/api/user-manual.html#34-errors).


## arXiv request pacing

All requests to `arxiv.org`, `www.arxiv.org`, and `export.arxiv.org` acquire an
arXiv-only slot in the retry transport. The slot spans request transmission,
response-body consumption, and response closure. Retries and redirects each
acquire their own slot. Wikipedia and web-search requests bypass this limiter.

The limiter conservatively waits three seconds after the previous attempt
finishes before starting another. It uses a nonblocking OS file lock and a
persisted timestamp under `~/.cache/research-assistant/arxiv/`. Separate service
instances and CLI processes using that same local directory share the limit.
Windows uses byte-range locking; Linux/macOS use `flock`. No dependency was added.
Keep the directory on local disk and do not delete its lock file while running.

Waiting yields to the event loop and remains inside the existing per-source
deadline. Cancellation releases the lock; a cancelled in-flight request still
records its completion time. An inaccessible limiter directory fails the source
instead of sending an unpaced request. An unreadable or non-finite persisted
time (for example, a file left empty by a process killed mid-write), or a
last-request time in the future after the clock moved back, is treated as a
request that just finished: the next request waits one full interval, logs a
warning, and rewrites the state. A process killed abruptly releases
its OS lock; the persisted start timestamp provides spacing for its successor,
but cannot establish when a remote server stopped processing the killed request.

This coordinates local processes sharing one directory, not different user
accounts, machines, or containers with separate filesystems. Team members must
still coordinate live arXiv usage: the documented limit applies across machines
under their control. State uses wall-clock time to work across processes and
reboots; system-clock jumps can affect pacing, so keep system time synchronized.
`--no-cache` bypasses evidence storage, not this limiter.

The three-second wait is independent of retry backoff and may mean that not all
configured retries fit inside the ten-second deadline. HTTP-to-HTTPS redirects
also consume a slot. Server-requested cooldowns are described below.

Offline tests cover spacing across instances, failed attempts, task cancellation,
deadlines, corrupted state, cross-process exclusion, and transport retries and
redirects. Local verification exercised Windows locking; Linux locking is to be
verified by CI. See [arXiv API rate limits](https://info.arxiv.org/help/api/tou.html#rate-limits).


## Retry-After and shared cooldowns

For retryable HTTP failures, the service reads `Retry-After` as nonnegative whole
seconds or a timezone-aware HTTP date. It waits for the larger of that delay and
its exponential backoff. The backoff cap does not shorten a server-requested
wait. Missing, malformed, or non-finite values fall back to ordinary backoff;
past dates add no extra delay. Permanent errors remain non-retryable, and the
last attempt does not sleep or start an extra retry.

The wait remains inside the source deadline, so a long cooldown can produce a
timeout without another request. For arXiv, the active request slot additionally
persists a `retry-after-until` timestamp before releasing its OS lock, including
on the last failed attempt. New service instances or CLI processes sharing the
limiter directory must respect both that timestamp and the usual three-second
spacing. Cancelling a waiter does not erase the cooldown. An unreadable
persisted cooldown cannot be recovered, so it is removed and the next request
waits one full three-second interval instead; a server that still wants a
longer pause answers with a new Retry-After, which is persisted again.

This header handling covers HTTPX responses used by the source fetchers; it does
not claim to interpret every provider SDK's rate-limit metadata. Our previous
live 429 diagnostic did not capture Retry-After, so we do not know whether arXiv
sent one. These changes were verified offline without additional live requests.

Tests cover seconds, HTTP dates, malformed and past values, delays exceeding the
backoff cap, cancellation at the deadline, and shared cooldown persistence after
retry exhaustion. See [HTTP Retry-After semantics](https://www.rfc-editor.org/rfc/rfc9110.html#section-10.2.3).
