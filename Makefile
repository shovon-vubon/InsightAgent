.DEFAULT_GOAL := help
.PHONY: help up down logs ps rebuild migrate revision downgrade seed \
        test test-backend test-frontend lint typecheck fmt check \
        build prune shell-backend shell-db

BACKEND := cd backend &&
FRONTEND := cd frontend &&

help: ## Show available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# --- stack -------------------------------------------------------------------

up: ## Start the full stack
	docker compose up -d --build

down: ## Stop the stack (volumes preserved)
	docker compose down

reset: ## Stop the stack and DESTROY all data volumes
	docker compose down -v

logs: ## Tail logs for all services
	docker compose logs -f

ps: ## Show service status
	docker compose ps

infra: ## Start only PostgreSQL and Redis (for running the API natively)
	docker compose up -d postgres redis

# --- database ----------------------------------------------------------------

migrate: ## Apply migrations to head
	$(BACKEND) uv run alembic upgrade head

revision: ## Autogenerate a migration: make revision m="add documents"
	$(BACKEND) uv run alembic revision --autogenerate -m "$(m)"

downgrade: ## Revert the most recent migration
	$(BACKEND) uv run alembic downgrade -1

seed: ## Create the bootstrap admin account from .env
	$(BACKEND) uv run python -m scripts.seed_admin

# --- quality -----------------------------------------------------------------

test: test-backend test-frontend ## Run every test suite

test-backend: ## Run backend tests (requires `make infra`)
	$(BACKEND) uv run pytest

test-frontend: ## Run frontend tests
	$(FRONTEND) npm run test

lint: ## Lint backend and frontend
	$(BACKEND) uv run ruff check .
	$(BACKEND) uv run ruff format --check .

typecheck: ## Type-check backend and frontend
	$(BACKEND) uv run mypy app tests
	$(FRONTEND) npm run typecheck

fmt: ## Auto-format and auto-fix
	$(BACKEND) uv run ruff check --fix .
	$(BACKEND) uv run ruff format .

check: lint typecheck test ## Everything CI runs

# --- misc --------------------------------------------------------------------

build: ## Build all images
	docker compose build

prune: ## Reclaim Docker disk space
	docker system prune -f

shell-backend: ## Shell into the backend container
	docker compose exec backend sh

shell-db: ## psql into the database
	docker compose exec postgres psql -U $${POSTGRES_USER:-insightagent} -d $${POSTGRES_DB:-insightagent}
