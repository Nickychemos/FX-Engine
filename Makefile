.PHONY: up down logs test run fmt lint

up:
	docker compose up --build -d

down:
	docker compose down -v

logs:
	docker compose logs -f api

test:
	./venv/bin/pytest -q

run:
	./venv/bin/uvicorn app.main:app --reload

fmt:
	./venv/bin/ruff format app tests

lint:
	./venv/bin/ruff check app tests

demo:
	./venv/bin/python -m scripts.demo
