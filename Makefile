SHELL := /bin/bash

.PHONY: help up down restart ps logs test test-backend e2e build deepseek-smoke zhipu-smoke

help:
	@echo "Targets:"
	@echo "  make up           - docker compose up -d --build"
	@echo "  make down         - docker compose down"
	@echo "  make restart      - docker compose restart"
	@echo "  make ps           - docker compose ps"
	@echo "  make logs         - docker compose logs -f nginx"
	@echo "  make test         - backend pytest"
	@echo "  make e2e          - UI Playwright e2e"
	@echo "  make build        - UI next build"
	@echo "  make deepseek-smoke - run deepseek outbound smoke (loads .env)"
	@echo "  make zhipu-smoke    - run zhipu outbound smoke (loads .env)"

up:
	docker compose -f infra/docker-compose.yml up -d --build

down:
	docker compose -f infra/docker-compose.yml down

restart:
	docker compose -f infra/docker-compose.yml restart

ps:
	docker compose -f infra/docker-compose.yml ps

logs:
	docker compose -f infra/docker-compose.yml logs -f nginx

test: test-backend

test-backend:
	backend/.venv/bin/python -m pytest -q

e2e:
	pnpm -C ui test:e2e

build:
	pnpm -C ui build

deepseek-smoke:
	set -a && source .env >/dev/null 2>&1 || true; set +a; \
	cd backend && ./.venv/bin/python scripts/deepseek_smoke.py

zhipu-smoke:
	set -a && source .env >/dev/null 2>&1 || true; set +a; \
	cd backend && ./.venv/bin/python scripts/zhipu_smoke.py
