# Stage 1 — Unified LLM Proxy (Phase 3) — Design

**Date:** 2026-04-29
**Phase:** Stage 1, subsystem 3 of `docs/superpowers/plans/2026-04-28-stage-1-meta.md`
**Source specs:** `docs/tech-task.txt` (Phase 2 — Unified LLM Proxy API), `docs/spec-addons.md` §1.1, §1.2, §clarification-on-tool-calling

## 1. Goals and non-goals

### Goals

- Expose two HTTP endpoints — `POST /v1/chat/completions` (sync + SSE) and `POST /v1/estimate` — that customer software calls instead of OpenAI / Anthropic / Gemini directly.
- Wire-shape compatible with the OpenAI Chat Completions API so that customers can keep using `openai-python`, `openai-node`, `langchain-openai`, `vercel-ai-sdk`, and similar libraries by changing only the base URL and the API key.
- Route requests to the correct upstream provider based on the requested model name; translate request/response shape per provider; pass through `tools` and `tool_choice` verbatim.
- Aggregate streaming responses server-side so downstream phases (5: usage logging, 7: guardrails) can consume the full response after the fact.
- Cancel upstream calls when the FastAPI client disconnects; emit a `ProxyResult` reflecting partial token usage.
- Offer a token + projected-cost preflight (`/v1/estimate`) that does not consume quota and does not invoke upstream completion.
- Expose two internal extension points (a `ProxyResult` value emitted on every request end, a per-chunk callback list on the streaming path) so phase 5 and phase 7 can plug in without modifying the proxy.
- Maintain a vendor pricing catalog refreshed at startup and via an admin endpoint.

### Non-goals (explicitly deferred)

- Rate limits, quota counters, `X-RateLimit-*` headers, 429 responses → phase 4.
- Persisting `usage_event` rows, internal pricing model in the `model_pricing` table, billing math → phase 5.
- Prompt/response storage, conversation grouping, PII detection, banned-terms / injection / unsafe-output rules, per-chunk blocking enforcement → phase 7.
- Sandbox-mode semantics. Platform keys with `mode=sandbox` are accepted today and treated identically to live keys. The schema flag is preserved for spec-addons §2.3 to fill in.
- Multi-key rotation per provider, circuit breakers, SLO tracking → Stage 3.
- Embeddings, image generation, audio → spec-addons §2.5 / §3.14.

## 2. Architecture

### 2.1 Module layout

Three new top-level packages under `api/app/`:

```
api/app/
  proxy/
    __init__.py
    router.py          # POST /v1/chat/completions, POST /v1/estimate
    schemas.py         # OpenAI-compatible Pydantic models
    service.py         # ProxyService: orchestrate, fan to adapter, emit ProxyResult
    registry.py        # model_id -> Provider; supported-model list
    dependencies.py    # require_api_key(scope=...) bearer dependency factory
    results.py         # ProxyResult, ChunkContext, ChunkDecision
    errors.py          # GatewayError hierarchy + OpenAI envelope mapper
    streaming.py       # SSE forwarding, aggregation, disconnect handling
    retry.py           # shared retry/timeout helper around adapter calls
    adapters/
      __init__.py
      base.py          # ProviderAdapter Protocol, StreamChunk type
      openai.py
      anthropic.py
      gemini.py
  pricing/
    __init__.py
    vendor_catalog.py  # in-memory VendorCatalog singleton
    router.py          # GET /v1/admin/pricing-catalog, POST .../refresh
    sources.py         # OpenRouterSource, LiteLLMJsonSource
  tokenizers/
    __init__.py
    openai_tiktoken.py
    anthropic_count.py
    gemini_count.py
```

Import direction is one-way: `proxy → pricing`, `proxy → tokenizers`, `proxy → security`. `pricing` and `tokenizers` do not import from `proxy`.

`app/main.py` registers `proxy.router`, `pricing.router`, and adds a lifespan hook that calls `vendor_catalog.refresh()` on startup.

### 2.2 Dependencies added

In `api/pyproject.toml`:

- Runtime: `tiktoken`.
- Test: `respx` (httpx mocking).

No provider SDKs. All upstream HTTP is via `httpx.AsyncClient`.

### 2.3 Configuration additions

In `app/config.py` (`Settings`):

- `proxy_timeout_connect_seconds: float = 5.0`
- `proxy_timeout_read_seconds: float = 60.0`
- `proxy_timeout_total_seconds: float = 120.0`
- `proxy_max_retries: int = 2`
- `proxy_retry_initial_backoff_ms: int = 250`
- `proxy_sse_keepalive_seconds: float = 15.0`
- `pricing_source_priority: list[str] = ["openrouter", "litellm"]`
- `pricing_refresh_on_startup: bool = True`
- `gemini_count_tokens_cache_ttl_seconds: int = 300`
- `anthropic_count_tokens_cache_ttl_seconds: int = 300`

## 3. Wire shape

### 3.1 `POST /v1/chat/completions` (sync and SSE)

Request body mirrors OpenAI's Chat Completions API. Required: `model`, `messages`. Pass-through fields preserved verbatim: `temperature`, `top_p`, `max_tokens`, `stop`, `stream`, `tools`, `tool_choice`, `response_format`, `seed`, `presence_penalty`, `frequency_penalty`, `logprobs`, `top_logprobs`, `n`, `user`. Unknown fields are forwarded verbatim to OpenAI; for Anthropic and Gemini, only the fields that map cleanly are forwarded — others are silently dropped (logged at debug).

Response (sync, `stream` absent or false): standard OpenAI Chat Completions response shape with `id`, `object: "chat.completion"`, `created`, `model`, `choices`, `usage`. The `id` is the gateway-generated `request_id` (UUID4, also returned in the `X-Request-ID` header), not the upstream provider's id.

Response (streaming, `stream: true`): `text/event-stream` with `data: {…}` events in OpenAI Chat Completions chunk format; final `data: [DONE]` line.

### 3.2 `POST /v1/estimate`

Request body: identical to chat completion. The endpoint never invokes upstream completion and never increments quota.

Response:

```json
{
  "model": "claude-opus-4-7",
  "provider": "anthropic",
  "input_tokens": 482,
  "input_cost_micros": 7230,
  "max_output_cost_micros": 30000,
  "max_output_tokens": 1000,
  "currency": "USD",
  "pricing_source": "openrouter",
  "pricing_fetched_at": "2026-04-29T08:00:00Z"
}
```

`max_output_cost_micros` and `max_output_tokens` are present iff the request specified `max_tokens`. `input_cost_micros` and `max_output_cost_micros` are `null` (not zero) if the catalog has no entry for the resolved `(provider, model)`. `pricing_source` is one of `"openrouter" | "litellm" | "unavailable"`.

Cost values use micros (1 / 1_000_000 of a unit) of `currency`. Always `USD` in phase 3.

### 3.3 Error envelope

All gateway errors return the OpenAI shape:

```json
{
  "error": {
    "message": "human-readable",
    "type": "gateway_timeout",
    "code": null,
    "param": null
  }
}
```

`type` values used in phase 3:

| Type | HTTP | Meaning |
|---|---|---|
| `invalid_request_error` | 400 | Malformed body, bad enum, etc. |
| `unsupported_model` | 400 | Model not in registry |
| `provider_key_missing` | 400 | Tenant has no active provider key for the resolved provider |
| `authentication_error` | 401 | Bearer token missing / invalid / expired / revoked |
| `insufficient_scope` | 403 | Token valid but lacks required scope |
| `upstream_request_error` | 502 | Upstream returned a 4xx that the gateway cannot mask (auth, model unknown, content policy) |
| `upstream_unavailable` | 502 | Upstream 5xx after retries exhausted |
| `gateway_timeout` | 504 | Upstream exceeded `proxy_timeout_total_seconds` |
| `internal_error` | 500 | Unhandled gateway exception |

Per phase 3 scope, no `rate_limit_exceeded` type is emitted by us; if upstream returns 429 unrelated to retries, it is mapped to `upstream_request_error`. Phase 4 introduces our own 429 path.

## 4. Request flow

### 4.1 Sync chat completion

1. FastAPI dependency `require_api_key(scope="chat:write")` resolves `Authorization: Bearer …`, calls `app.security.service.verify_api_key`, rejects on missing/invalid/expired/revoked, rejects if `"chat:write"` not in `api_key.scopes`, schedules `last_used_at` update on a fire-and-forget background task.
2. Body parsed into `ChatCompletionRequest` (Pydantic).
3. `registry.resolve(request.model)` → `Provider`. Unknown → `UnsupportedModelError` (400 with the supported list).
4. Provider key lookup: most-recently-created un-revoked `ProviderKey` for `(api_key.tenant_id, provider)`. None → `ProviderKeyMissingError` (400).
5. `vault.decrypt(provider_key.encrypted_secret)` → upstream secret string.
6. Adapter selected from registry (`{Provider.OPENAI: OpenAIAdapter(), ...}`). `await adapter.chat_completion(request, secret, timeout=policy)` runs through the shared retry helper (Q9c policy).
7. Response from adapter is OpenAI-shape; service stamps gateway `id` (= `request_id`), constructs `ProxyResult` (Section 5), awaits each registered `on_completed` callback (empty in phase 3), and returns the response with `X-Request-ID` header.

### 4.2 Streaming chat completion

When `stream: true`:

1. Steps 1–5 identical to sync.
2. Service returns a `StreamingResponse(media_type="text/event-stream")`. The response body is an async generator coordinated by an `anyio.create_task_group`:
   - **Producer**: opens `adapter.chat_completion_stream(...)` (an async generator yielding `StreamChunk`), normalizes each chunk to OpenAI delta shape, accumulates the aggregated response object and rolling token counts, pushes serialized SSE frames into an in-memory `anyio.MemoryObjectStream`.
   - **Consumer**: reads from the stream, runs each `on_chunk(ChunkContext) -> ChunkDecision` callback in registration order, forwards `data: {…}\n\n` to FastAPI. If any callback returns `abort(reason)`, the consumer cancels the task group, sends a final `data: {"error": {…}}` event, and exits.
   - **Keepalive**: a third task emits `: keepalive\n\n` every `proxy_sse_keepalive_seconds` while no real chunk has flushed in that window.
3. Disconnect handling: `request.is_disconnected()` is polled by the consumer between chunks. Disconnect → cancel the task group → producer's `httpx` stream context closes the upstream connection → adapter's async generator raises `CancelledError` cleanly.
4. After the task group exits (success, abort, disconnect, or error): service builds `ProxyResult` with whatever counts and aggregated body it has, marks `streamed=True`, sets `status` per outcome, awaits `on_completed` hooks.

### 4.3 Estimate

1. Auth + scope + body parse identical to sync.
2. Resolve model → provider.
3. Tokenize input messages:
   - **OpenAI**: `tiktoken.encoding_for_model(model)` offline, fall back to `cl100k_base` for unknown encodings.
   - **Anthropic**: `httpx.post("https://api.anthropic.com/v1/messages/count_tokens", headers={"x-api-key": tenant_key, "anthropic-version": "2023-06-01"}, json={"model": model, "messages": ..., "system": ...})`. Result cached for `anthropic_count_tokens_cache_ttl_seconds` keyed by `(model, sha256(payload_json))`.
   - **Gemini**: `httpx.post("https://generativelanguage.googleapis.com/v1beta/models/{model}:countTokens?key={tenant_key}", json={"contents": ...})`. Result cached for `gemini_count_tokens_cache_ttl_seconds` keyed by `(model, sha256(payload_json))`.
4. `vendor_catalog.get(provider, model)` returns `VendorRate | None`.
5. Compute cost; build response per Section 3.2.

No `ProxyResult` is emitted by `/v1/estimate` (no upstream completion happened, so no usage event would be relevant). Hooks do not fire.

## 5. ProxyResult and hooks

### 5.1 `ProxyResult`

```python
@dataclass(frozen=True)
class ProxyResult:
    request_id: str
    tenant_id: UUID
    api_key_id: UUID
    provider: Provider
    model: str
    status: UsageStatus            # success | provider_error | gateway_error | blocked
    streamed: bool
    started_at: datetime
    completed_at: datetime
    latency_ms: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    error_code: str | None         # gateway error type when status != success
    request_body: dict             # full request as received (post-validation)
    response_body: dict | None     # full response (aggregated for streams), None on pre-stream failure
```

Constructed at request end for every code path: success, provider error, gateway timeout, validation failure (only the cases that reached the service layer — pre-auth failures do not produce a `ProxyResult`), and client disconnect with partial bytes.

### 5.2 Hook registration

`ProxyService` exposes:

```python
on_completed: list[Callable[[ProxyResult], Awaitable[None]]]
on_chunk: list[Callable[[ChunkContext], Awaitable[ChunkDecision]]]
```

Both are plain lists, mutated at app construction time (in `create_app()` or via a future register-hook helper). Phase 3 ships with both lists empty. Phase 5 appends a usage-event writer to `on_completed`. Phase 7 appends a guardrail evaluator to both.

`ChunkContext` carries the in-progress aggregated response, the latest delta, and the cumulative token counts. `ChunkDecision = Literal["continue"] | AbortReason` where `AbortReason` is a small typed object (kind, message, error_code).

Hooks awaited sequentially in registration order. A hook raising an exception is logged at error level but does not crash the request — phase 3 isolates the proxy from misbehaving subscribers.

## 6. Provider adapters

### 6.1 Contract

```python
class ProviderAdapter(Protocol):
    provider: ClassVar[Provider]

    async def chat_completion(
        self,
        request: ChatCompletionRequest,
        upstream_secret: str,
        *,
        timeout: TimeoutPolicy,
        request_id: str,
    ) -> ChatCompletionResponse: ...

    def chat_completion_stream(
        self,
        request: ChatCompletionRequest,
        upstream_secret: str,
        *,
        timeout: TimeoutPolicy,
        request_id: str,
    ) -> AsyncIterator[StreamChunk]: ...
```

`StreamChunk` is the OpenAI SSE delta shape.

Each adapter:

- Builds upstream URL, headers, and JSON body in the provider's native format.
- Translates OpenAI request → provider request (see 6.2–6.4).
- Parses upstream response (or SSE chunks) and translates back to OpenAI shape.
- Maps upstream errors to `GatewayError` subclasses.

### 6.2 OpenAI adapter

Minimal translation: `https://api.openai.com/v1/chat/completions`, request body forwarded near-verbatim (gateway-only fields stripped: none in phase 3). SSE chunks pass through with delta shape preserved. Errors mapped from OpenAI's `{error: {…}}` envelope by HTTP status.

### 6.3 Anthropic adapter

Endpoint: `https://api.anthropic.com/v1/messages`. Headers: `x-api-key`, `anthropic-version: 2023-06-01`.

Request mapping:

- OpenAI `messages` with `role: system` → Anthropic top-level `system: string`. Multiple system messages concatenated with `\n\n`.
- OpenAI `messages` with `role: user|assistant` → Anthropic `messages` array. Tool-result messages (`role: tool`) map to Anthropic content blocks of type `tool_result`.
- OpenAI `tools` → Anthropic `tools` (schema is similar; `type: "function"` wrapper stripped).
- OpenAI `tool_choice: "auto"|"none"|{type:"function", function:{name}}` → Anthropic `tool_choice: {type: "auto"|"none"|"tool", name?}`.
- `max_tokens` is required by Anthropic; default to model's context window minus input if absent.
- `stop` → `stop_sequences`.
- `temperature`, `top_p` forwarded.

Response mapping:

- Anthropic `content[]` blocks → OpenAI `choices[0].message.content` (text blocks concatenated) and `choices[0].message.tool_calls` (tool_use blocks).
- `stop_reason` → `finish_reason`: `end_turn`→`stop`, `max_tokens`→`length`, `tool_use`→`tool_calls`, `stop_sequence`→`stop`.
- `usage.input_tokens` / `usage.output_tokens` → OpenAI `usage.prompt_tokens` / `usage.completion_tokens`.

SSE: Anthropic emits typed events (`message_start`, `content_block_start`, `content_block_delta`, `content_block_stop`, `message_delta`, `message_stop`). Adapter consumes these and emits OpenAI-shape `data: {…}` chunks per delta + final `[DONE]`.

### 6.4 Gemini adapter

Endpoint: `https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={secret}` (or `:streamGenerateContent` for streaming).

Request mapping:

- OpenAI `messages` → Gemini `contents` array with `role: "user"|"model"` (system messages → Gemini `system_instruction`).
- OpenAI `tools` → Gemini `tools[].functionDeclarations`.
- `tool_choice` → Gemini `toolConfig.functionCallingConfig.mode`: `auto`→`AUTO`, `none`→`NONE`, function-named → `ANY` with allowed list.
- `temperature`, `top_p`, `max_tokens`, `stop` → corresponding `generationConfig` fields.

Response mapping:

- Gemini `candidates[0].content.parts[]` → OpenAI message: text parts concatenated to content; `functionCall` parts → `tool_calls`.
- `finishReason`: `STOP`→`stop`, `MAX_TOKENS`→`length`, `SAFETY`→`content_filter`, `RECITATION`→`content_filter`.
- `usageMetadata.promptTokenCount` / `candidatesTokenCount` → `usage`.

SSE: Gemini's stream endpoint emits one JSON object per chunk, separated by `\n\n` and prefixed `data: `. Adapter parses each, normalizes to OpenAI delta shape.

## 7. Auth and scopes

### 7.1 Bearer dependency

`app/proxy/dependencies.py::require_api_key(scope: str)` is a factory:

```python
def require_api_key(scope: str) -> Callable[..., Awaitable[ApiKey]]:
    async def _dep(
        request: Request,
        db: Annotated[AsyncSession, Depends(get_session)],
        background: BackgroundTasks,
    ) -> ApiKey:
        token = _extract_bearer(request)
        if token is None:
            raise AuthenticationError("missing bearer token")
        api_key = await verify_api_key(db, token)
        if api_key is None:
            raise AuthenticationError("invalid bearer token")
        if scope not in api_key.scopes:
            raise InsufficientScopeError(required=scope)
        background.add_task(_touch_last_used, api_key.id)
        return api_key
    return _dep
```

`_touch_last_used` opens its own session, updates `last_used_at`, commits. Failures logged but never propagate to the request.

### 7.2 Scope vocabulary

Phase 3 defines exactly one scope: `chat:write`. Required by both `/v1/chat/completions` and `/v1/estimate`.

Existing `ApiKeyCreate` schema default changes from `scopes: []` to `scopes: ["chat:write"]`. Caller-supplied scopes still honored verbatim. Migration backfills `["chat:write"]` onto every existing `ApiKey` row whose `scopes` is empty (none expected in real tenants — greenfield).

Future scopes reserved for later phases: `usage:read`, `keys:manage`, `pricing:admin`, `chat:estimate` (if estimate-only persona ever materializes).

## 8. Pricing catalog

### 8.1 Object

```python
@dataclass
class VendorRate:
    input_micros_per_token: int
    output_micros_per_token: int

@dataclass
class CatalogSnapshot:
    rates: dict[tuple[Provider, str], VendorRate]
    source: Literal["openrouter", "litellm", "unavailable"]
    fetched_at: datetime | None
    version: str | None
```

`VendorCatalog` singleton holds the current snapshot, exposes `get(provider, model) -> VendorRate | None`, `snapshot() -> CatalogSnapshot`, and `async refresh() -> CatalogSnapshot`.

### 8.2 Refresh

`refresh()` iterates `pricing_source_priority`, instantiates each source, awaits its `fetch()`. First success is atomically swapped into the singleton. All sources failing keeps the prior snapshot if any, otherwise sets `source="unavailable"` and `rates={}`. Logged at warning level.

Sources:

- `OpenRouterSource.fetch()`: GET `https://openrouter.ai/api/v1/models`, parse `data[]`. Each entry has `id`, `pricing.prompt`, `pricing.completion` (decimal strings, dollars per token). Map `top_provider.id` (or model-prefix when missing) to our `Provider` enum; drop unmapped models. Convert dollars → micros (1 micros = 1e-6 USD) using `Decimal` to avoid float drift: `int(Decimal(price_str) * Decimal(1_000_000))`.
- `LiteLLMJsonSource.fetch()`: GET `https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window_backup.json`. Parse JSON. For each model entry with `litellm_provider in {"openai", "anthropic", "gemini"}` (or aliases), build `(Provider, model_id)` key and `VendorRate` from `input_cost_per_token` / `output_cost_per_token` (already in dollars per token).

### 8.3 Admin endpoints

`app/pricing/router.py`:

- `GET /v1/admin/pricing-catalog` → 200 with snapshot (model rates listed, ordered by provider then model).
- `POST /v1/admin/pricing-catalog/refresh` → 200 with new snapshot, or 503 if all sources failed and no prior snapshot exists.

Both protected by cookie auth (`require_user`) AND admin-role check (a new dependency `require_admin_membership` that reads the user's first membership and rejects unless `role == "admin"`). No bearer-token access. Admin operations belong in the console.

## 9. Retry, timeout, cancellation

### 9.1 Timeout policy

Per upstream call, `httpx.Timeout(connect=5, read=60, write=60, pool=5)` and an `anyio.fail_after(proxy_timeout_total_seconds)` wrapping the entire adapter invocation including retries. Streaming requests use the same timeouts but the read timeout applies to "no bytes received in 60s" rather than total stream duration.

### 9.2 Retry policy

Wrapper helper `proxy.retry.with_retries(adapter_call, policy)`:

- Retry on: `httpx.ConnectError`, `httpx.ReadTimeout` (pre-stream only), `httpx.RemoteProtocolError`, upstream HTTP `502 | 503 | 504`, upstream HTTP `429` only when a `Retry-After` header is present and parseable.
- Do not retry on: any 4xx other than the bounded 429 case, content-policy refusals, model-not-found, auth errors, validation errors.
- Max attempts: `1 + proxy_max_retries` (default 1 + 2 = 3 total attempts).
- Backoff: exponential with jitter. Sleep before retry N: `min(retry_after_seconds, base * 2^(N-1)) ± jitter`, where `base = proxy_retry_initial_backoff_ms / 1000`, jitter is uniform [0, 0.1×base].
- Streaming: retry only if no bytes have been forwarded to the FastAPI client yet. Once any chunk has flushed, mid-stream upstream failure is surfaced as a final SSE `error` event, no retry.
- Total elapsed time obeys `proxy_timeout_total_seconds`.

### 9.3 Cancellation

Sync request: client disconnect during sync awaits is handled by Starlette canceling the request task, which propagates into httpx and closes the upstream connection. `ProxyResult` is built in the request handler's `finally`; on cancellation the result has `status=success` if the upstream completed before disconnect (rare but possible) or `status=gateway_error` with `error_code="client_disconnected"` otherwise.

Streaming request: covered in Section 4.2. Always emits a `ProxyResult` with whatever was buffered.

## 10. Testing

Unit + integration tests against the existing real-Postgres test DB. httpx upstream calls mocked with `respx`.

### 10.1 Coverage matrix

**Happy paths (each must have at least one test):**

| Endpoint | Provider | Variant |
|---|---|---|
| `/v1/chat/completions` | OpenAI | sync, no tools |
| `/v1/chat/completions` | OpenAI | sync, with tools |
| `/v1/chat/completions` | OpenAI | streaming |
| `/v1/chat/completions` | Anthropic | sync |
| `/v1/chat/completions` | Anthropic | streaming |
| `/v1/chat/completions` | Gemini | sync |
| `/v1/chat/completions` | Gemini | streaming |
| `/v1/estimate` | OpenAI | with `max_tokens` |
| `/v1/estimate` | Anthropic | without `max_tokens` |
| `/v1/estimate` | Gemini | with `max_tokens` (mocked countTokens) |
| `GET /v1/admin/pricing-catalog` | n/a | empty catalog |
| `POST /v1/admin/pricing-catalog/refresh` | n/a | OpenRouter mocked success |

**Critical negative paths:**

- Bearer missing → 401 `authentication_error`.
- Bearer malformed → 401.
- Bearer for a revoked key → 401.
- Bearer for an expired key → 401.
- Bearer valid but `chat:write` not in scopes → 403 `insufficient_scope`.
- Unknown model → 400 `unsupported_model`, response body lists supported models.
- Tenant has no provider key for resolved provider → 400 `provider_key_missing`.
- Upstream returns 500 once → retried, succeeds on attempt 2.
- Upstream returns 500 three times → 502 `upstream_unavailable`.
- Upstream returns 401 → 502 `upstream_request_error`, no retry.
- Upstream returns 429 with `Retry-After: 1` → retried after 1s.
- Streaming upstream 500 before first byte → retried, eventually 502.
- Streaming upstream 500 mid-stream → final SSE `error` event, no retry.
- Streaming client disconnect mid-stream → upstream cancelled (verified via respx call count), `ProxyResult` emitted with `streamed=True`, partial token counts.
- Pricing source unreachable on startup → app boots, `/v1/estimate` returns `cost: null`, `pricing_source: "unavailable"`.
- OpenRouter unreachable → falls back to LiteLLM JSON.
- Both pricing sources unreachable → catalog stays empty.
- `on_completed` hook raises → request still succeeds; error logged.
- `on_chunk` hook returns abort → stream terminated with final error event; `ProxyResult.status = blocked`.

### 10.2 Fixtures

- `proxy_client` — extends `authed_client` with a seeded provider key per provider (encrypted via the test vault).
- `respx_openai`, `respx_anthropic`, `respx_gemini` — mocks for each upstream base URL.
- `frozen_catalog` — pre-populates `VendorCatalog` with a known fixture so estimate tests are deterministic without hitting OpenRouter.

## 11. Operational notes

- Every request logs (structlog) at info: `request_id`, `tenant_id`, `api_key_id`, `provider`, `model`, `streamed`, `latency_ms`, `prompt_tokens`, `completion_tokens`, `status`, `error_code`. Bodies not logged in phase 3 (phase 7 introduces optional prompt/response storage with retention).
- `X-Request-ID` returned on every response (success and error).
- Pricing-catalog refresh logs at info on success (source, rate count, version), at warning on fallback, at error on full failure.

## 12. Open items

(none for phase 3; decisions locked above)

## 13. Definition of done

- All 13 happy-path tests pass against a real Postgres test DB and respx-mocked upstreams.
- All critical negative paths pass.
- `mypy --strict` clean across `app/proxy/`, `app/pricing/`, `app/tokenizers/`.
- `ruff` clean across same.
- `make test` passes locally and inside the docker-compose stack.
- A live `curl` against the docker-compose-running api with a seeded tenant + provider key returns a real OpenAI completion through the gateway. (Manual smoke; not an automated test because it would depend on a real OpenAI key.)
