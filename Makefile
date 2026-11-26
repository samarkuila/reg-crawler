.PHONY: dev test lint migrate seed-demo install-browser crawl probe demo

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
	$(PY) -m app.cli seed-demo

install-browser:
	$(PY) -m playwright install chromium

crawl:
	@echo "not implemented until Prompt 13"

probe:
	@echo "not implemented until Prompt 14"

demo:
	@echo "not implemented until Prompt 15"
