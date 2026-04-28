# Protos — LLM Gateway

Secure multi-provider LLM gateway with spend control and guardrails.

## Layout

- `api/` — FastAPI backend (Python 3.12, modular monolith).
- `web/` — Vue 3 console (TypeScript, Vite, Tailwind).
- `infra/` — docker-compose for local stack.
- `docs/` — specs and roadmap.

## Quickstart

Requires Docker + Docker Compose v2 and `make`. No host-side Python or Node toolchain required.

```bash
make dev      # build images, start postgres+redis+minio+api+web
make logs     # tail logs
make test     # api + web tests
make down     # stop stack
```

| URL                              | Service          |
|----------------------------------|------------------|
| http://localhost:8000/health     | API health       |
| http://localhost:8080            | Web console      |
| http://localhost:9001            | MinIO console    |
| postgres://protos:protos@localhost:5432/protos | Postgres |

## Day-to-day

```bash
make migrate                  # apply migrations
make revision m="add foo"     # autogenerate a new migration
make test-api                 # pytest in api container
make test-web                 # vitest
make shell-api                # bash inside api container
```

## Docs

- `docs/tech-task.txt` — original product spec.
- `docs/spec-addons.md` — gap-analysis additions.
- `docs/design-system.md` — UI tokens, components, patterns.
- `docs/superpowers/plans/2026-04-28-stage-1-meta.md` — Stage 1 roadmap.
