.PHONY: dev test lint migrate seed-demo crawl probe demo

PY ?= python

dev:
	$(PY) -m uvicorn app.main:app --reload --port 8000

test:
	$(PY) -m pytest

lint:
	$(PY) -m ruff check .

migrate:
	$(PY) -m alembic upgrade head

seed-demo:
	@echo "not implemented until Prompt 8"

crawl:
	@echo "not implemented until Prompt 13"

probe:
	@echo "not implemented until Prompt 14"

demo:
	@echo "not implemented until Prompt 15"
