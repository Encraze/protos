# Roadmap Gap Analysis — Additions to `tech-task.txt`

**Date:** 2026-04-28
**Status:** Awaiting user review
**Owner:** ihor.roid@aretihealth.com

## Context

The original `tech-task.txt` defines a two-stage roadmap (Stage 1 MVP, Stage 2 Expansion) for a multi-provider LLM gateway with spend control and guardrails. Target ICP: mid-size SMB-to-enterprise companies that build AI capabilities into their products (not FAANG, not consumer).

This document captures **net-new items** identified as missing from the original roadmap. Items already present in the original spec are not duplicated here — those should be implemented per the original spec. A reference list of "already-covered" items appears at the end so readers don't propose them again.

Three positioning approaches were considered for how to slot additions into the roadmap (single Stage 2 dump / Gateway-to-Platform / GTM-aligned). We picked Gateway-to-Platform but deliberately scoped Stage 3 to gateway gaps only — broader platform-expansion themes (agentic layer, RAG platform, deployment sovereignty, ecosystem marketplace) are out of scope for this document and tracked under "Deferred" below.

## Spec clarification (no implementation work)

**Function / tool calling is transparent pass-through.** The `tools` and `tool_choice` fields are forwarded verbatim to the upstream provider. The gateway has no opinion on tool selection or execution; tool calls are handled natively in the caller's runtime. Add one line to the *Unified LLM Proxy API* section of the original spec to make this explicit.

---

## Stage 1 (MVP) — additions

### 1.1 Streaming responses (SSE)

Server-sent events for token-by-token output. Required for any chat UI built on top of the proxy.

Behaviour:
- Detect `stream: true` in the request body and switch to SSE response.
- Stream chunks as they arrive from the upstream provider.
- Aggregate the full response server-side for usage logging and post-hoc guardrail evaluation.
- For `block`-mode guardrails that operate on partial output (PII, banned terms), evaluate per-chunk; on violation, terminate the stream and emit a final SSE event indicating the block reason.
- On client disconnect, cancel the upstream request and log partial token usage for accurate billing.

Fits under: *Stage 1 → Core Product Engine → Unified LLM Proxy API*.

### 1.2 Token-count / cost pre-flight endpoint

`POST /v1/estimate` accepts a request body identical to a normal completion call and returns:
- Estimated input tokens (using the appropriate provider tokenizer).
- Projected cost based on the target model's per-token pricing and any provider prompt-cache discounts where applicable.
- Does NOT execute the request and does NOT count against quotas.

Lets clients show "this will cost ~$X" in their UIs and apply client-side budget checks before sending.

Fits under: *Stage 1 → Core Product Engine → Unified LLM Proxy API*.

---

## Stage 2 — additions

### 2.1 Standard rate-limit response headers

On every gateway response, include:
- `X-RateLimit-Limit`
- `X-RateLimit-Remaining`
- `X-RateLimit-Reset`
- `Retry-After` (on 429)

Industry standard. Trivial to add now, painful to retrofit later (clients build retry logic against whatever you ship first).

Fits under: *Stage 1 → Core Product Engine → Rate Limits / Quotas* (delivered in Stage 2 once rate limits are in place).

### 2.2 Public status page

A status page at `status.<product-domain>` showing:
- Gateway uptime (per region).
- Per-provider health (OpenAI, Anthropic, Gemini, others as added).
- Active and historical incidents.
- Subscribable RSS / email / webhook updates.

Customers' end users will ask "is the AI down or are you down?" — the tenant needs a public artifact to point at when triaging incidents.

Fits under: new section under *Stage 2*.

### 2.3 Sandbox / test-mode keys

Tenants can issue API keys with `mode: sandbox`. Sandbox keys:
- Hit a sandbox upstream (or return deterministic mock responses) at zero cost.
- Do not count toward billing or quotas.
- Are tagged in logs as `env: sandbox` and segregated in the dashboard.

Lets developers integrate without burning budget. Common pattern from Stripe / Twilio. Reduces the "I'm afraid to test in prod" friction during onboarding.

Fits under: *Stage 1 → Core Product Engine → Tenant-issued API Keys* (extended in Stage 2).

### 2.4 Request tagging

Clients pass arbitrary key-value pairs in a header (e.g., `X-Tags: project=checkout,user_id=123`) or in a dedicated request-body field. The gateway:
- Stores tags on the usage-log row.
- Exposes tags as filterable / groupable dimensions in the dashboard.
- Powers the Stage 3 showback / chargeback feature.

Fits under: *Stage 1 → Visibility / Business Value → Usage Logging + Dashboard* (extended in Stage 2).

### 2.5 Embeddings endpoint

Unified embeddings endpoint across providers (OpenAI, Cohere, Gemini text-embedding, etc.). Same proxy pattern as completions: pass-through, normalize the response shape, log usage, bill per provider pricing.

Fits under: *Stage 2 → Product Expansion → More Integrations* (was deferred from MVP per scope discussion).

---

## Stage 3 — Enterprise-grade gateway depth

This is a NEW STAGE inserted between the original Stage 2 (Expansion) and any future strategic stages. Stage 3 hardens the gateway for production use at mid-size customers and adds the enterprise-readiness gateway features that customers will demand by month 9-12 of go-to-market.

### 3.1 Multi-key rotation per provider

Tenants can register N upstream API keys for the same provider (e.g., five OpenAI keys). The gateway:
- Round-robins requests across the pool.
- Detects per-key quota / rate-limit responses (HTTP 429 + provider-specific signals) and rotates affected keys out of the pool until the limit window resets.
- Surfaces per-key health and utilisation in the admin UI.

Critical for high-throughput customers who exceed single-key TPM / RPM limits.

### 3.2 Circuit breakers + SLO / error-budget tracking

Two related controls:
- **Circuit breakers** per upstream provider: open after N consecutive failures, half-open probe to test recovery.
- **SLO definitions** per tenant (e.g., 99.5% success, p95 latency < 2s). Error-budget consumption visible in the dashboard with alerting when burn rate becomes unsustainable.

### 3.3 Showback / chargeback

Builds on Stage 2 request tagging. Tenants define cost-attribution rules (e.g., `project` tag → spend bucket). The dashboard shows spend grouped by team / project / end-customer. Exports (CSV, line-item JSON) for the tenant's internal billing systems. Enterprise finance teams demand this; without it, customers can't justify their LLM spend internally.

### 3.4 Optimization recommendations engine

Background analyzer over usage logs that surfaces actionable suggestions in the dashboard:
- "X% of your requests would hit semantic cache with similarity threshold 0.95 → ~$Y/month savings."
- "Model A is 80% cheaper than Model B for these queries with similar quality on your eval set."
- "This prompt prefix is repeated 60% of the time → enable provider prompt caching."

### 3.5 GDPR right-to-be-forgotten workflow

Distinct from the generic Stage 2 retention controls. End-user (data subject) requests deletion → tenant admin executes a hard-delete operation that:
- Removes prompts / responses / conversations associated with a subject identifier.
- Removes derived analytics rows that contain personal data.
- Records a deletion certificate in the audit log (proof of compliance).
- Cannot be reversed.

### 3.6 Audit log SIEM export

Push audit-log events to customer SIEMs:
- Splunk HEC
- Datadog Logs
- ELK
- Microsoft Sentinel

Configurable filters (which event types). Standard CEF or JSON format. Required for any customer with a security operations function.

### 3.7 BYOK (customer-managed encryption keys)

Tenant configures their own KMS key (AWS KMS / Azure Key Vault / GCP KMS) for encryption-at-rest of:
- Stored prompts and responses.
- Stored API keys (both tenant-issued and upstream provider keys).

Envelope encryption pattern: data keys encrypted with the tenant's KMS key. If the tenant revokes the KMS key, the gateway loses access to that tenant's data — this is the security guarantee the enterprise requirement asks for.

### 3.8 Compliance evidence automation

Auto-generated evidence packs for SOC2 / ISO audits:
- Access logs (who accessed what, when).
- Configuration history (policy changes, retention setting changes).
- Encryption status reports.
- Backup verification reports.

Exportable as PDF / CSV bundles, scoped to a date range.

### 3.9 Data lineage tracking

Per-request, record the full data path:
- **Source:** tenant, key, source IP, source region.
- **Path:** gateway region, upstream provider, upstream region.
- **Storage:** where the prompt / response landed (region, retention bucket).
- **Egress:** any downstream system that pulled this record (SIEM forwarder, evidence pack, support access).

Required for customers operating under data-residency or sectoral data-handling rules.

### 3.10 End-customer feedback capture API

`POST /v1/feedback/{request_id}` accepts thumbs up/down plus an optional reason from the tenant's end users. Fed into the eval / quality dashboard. Provides real signal for the original Stage 2 prompt-management approval workflows (which otherwise rely on synthetic evals).

### 3.11 CLI tool

First-class CLI (`{product}-cli`) for:
- Auth (login, key management).
- One-shot proxy calls (`{product} chat -m claude-opus-4-7 "..."`).
- Log tail / search.
- Quota / spend check.
- Local config file (`.{product}rc`).

Distinct from SDKs (which are libraries for embedding in apps). The CLI is for ops / debugging / quick experiments.

### 3.12 Request replay endpoint

`POST /v1/requests/{request_id}/replay` — re-executes a logged request through *current* policies (guardrails, routing, models). Useful for:
- "What would happen if I changed this rule?" debugging.
- Regression testing after policy updates.
- Customer support investigating a complaint.

Replay results are tagged in logs so they don't pollute production analytics.

### 3.13 IDE integrations

First-party extensions for VS Code, Cursor, Claude Code, JetBrains:
- Browse logs.
- Tail live requests.
- Quick spend check.
- Test API keys in editor.

The ICP's developers live in these tools — meeting them where they work reduces integration friction.

### 3.14 Multimodal expansion

Extending the proxy to non-text modalities, all following the same pattern (pass-through, normalize, log, bill, apply guardrails where applicable):
- **Vision input** — images in prompts.
- **Image generation proxy** — DALL-E, Imagen, Stable Diffusion.
- **TTS / STT proxy** — Whisper, OpenAI TTS, ElevenLabs.

---

## Deferred (out of scope for this gap analysis)

Considered for Stage 3 but deliberately moved out. These represent a category jump from "gateway" to "platform" and warrant a separate strategic conversation:

- **Agentic layer** — MCP (Model Context Protocol) hub, agent orchestration runtime with state / retries / tracing, tool registry.
- **Knowledge / RAG platform** — managed vector store, doc ingestion pipeline, RAG eval suite. Distinct from the original spec's "RAG Connectors" which are read-only integrations.
- **Deployment sovereignty** — self-hosted model proxy (Ollama, vLLM, llama.cpp), on-prem / air-gapped distribution, VPC PrivateLink.
- **Ecosystem** — white-label / reseller portal, community marketplace for guardrails, prompt templates, policies.

## Already covered by the original spec

For implementer reference — items raised during gap analysis but already present in the original `tech-task.txt`. No separate work is required:

- Cross-provider failover → original Stage 2 *"Advanced Routing: fallback providers"*.
- Provider prompt-cache pass-through, semantic caching, cache analytics → original Stage 2 *"Caching Layer"* (worth making "semantic" explicit during implementation).
- Cost anomaly detection → original Stage 2 *"Anomaly Detection"*.
- Multi-region for DR → original Stage 2 *"Data Residency: EU / US regional hosting"*.
- A/B prompt testing, shadow mode, prompt regression suites → original Stage 2 *"Prompt Management: versions, compare outputs, rollback, approvals"*.
- HIPAA readiness → adjacent to original Stage 2 *"SOC2 / ISO readiness"*.
- Forecasting → original Stage 2 *"Forecasting: expected monthly cost"*.
- OpenTelemetry tracing → original Stage 1 Foundation *"Observability"* (worth specifying OTel as the standard during implementation).
- OpenAPI / Postman docs → original Stage 1 Polish *"API docs"*.
- Hard budget enforcement → arguably under original Stage 1 *"monthly caps"* + *"overage alerts"*. Worth tightening during implementation: enforce a hard stop, not just an alert.
