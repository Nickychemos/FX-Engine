.PHONY: up down db logs test run fmt lint demo

up:
	docker compose up --build -d

down:
	docker compose down -v

db:
	docker compose up -d db
	@echo "waiting for postgres to be ready..."
	@until docker compose exec -T db pg_isready -U fx >/dev/null 2>&1; do sleep 1; done
	@echo "postgres is ready"

logs:
	docker compose logs -f api

test: db
	./venv/bin/pytest -q

run:
	./venv/bin/uvicorn app.main:app --reload

fmt:
	./venv/bin/ruff format app tests

lint:
	./venv/bin/ruff check app tests

demo: db
	./venv/bin/python -m scripts.demo
