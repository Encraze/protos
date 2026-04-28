.PHONY: help dev down logs build test test-api test-web migrate revision shell-api shell-web ps

COMPOSE := docker compose -f infra/docker-compose.yml

help:
	@grep -E '^[a-zA-Z_-]+:.*?##' Makefile | awk -F':.*?## ' '{printf "%-14s %s\n", $$1, $$2}'

dev: ## Build images and start full local stack
	$(COMPOSE) up -d --build
	@echo "API: http://localhost:8000/health"
	@echo "Web: http://localhost:8080"

down: ## Stop local stack
	$(COMPOSE) down

logs: ## Tail stack logs
	$(COMPOSE) logs -f --tail=200

ps: ## Show stack status
	$(COMPOSE) ps

build: ## Rebuild images without starting
	$(COMPOSE) build

test: test-api test-web ## Run all tests

test-api: ## Run api tests in api container
	$(COMPOSE) up -d postgres
	$(COMPOSE) run --rm api pytest -v

test-web: ## Run web tests in web container build stage
	cd web && docker build --target test -t protos-web:test .
	docker run --rm protos-web:test

migrate: ## Apply api migrations
	$(COMPOSE) up -d postgres
	$(COMPOSE) run --rm api alembic upgrade head

revision: ## Create a new migration: make revision m="description"
	$(COMPOSE) up -d postgres
	$(COMPOSE) run --rm api alembic revision --autogenerate -m "$(m)"

shell-api: ## Bash inside api container
	$(COMPOSE) exec api /bin/bash

shell-web: ## Sh inside web container
	$(COMPOSE) exec web /bin/sh
