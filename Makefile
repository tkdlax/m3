.PHONY: help install lint typecheck test test-cov check config-validate docker-build docker-up docker-down smoke

PYTHON ?= uv run
CONFIG ?= config/strategy.example.yaml

help:
	@echo "Targets: install lint typecheck test check config-validate docker-build docker-up docker-down smoke"

install:
	uv sync --all-extras

lint:
	$(PYTHON) ruff check src tests
	$(PYTHON) ruff format --check src tests

typecheck:
	$(PYTHON) mypy src/dividend_agent

test:
	$(PYTHON) pytest

test-cov:
	$(PYTHON) pytest --cov=dividend_agent --cov-report=term-missing

check: lint typecheck test

config-validate:
	$(PYTHON) dividend-agent config validate $(CONFIG)

docker-build:
	docker compose build

docker-up:
	docker compose up -d --build

docker-down:
	docker compose down

smoke:
	bash scripts/smoke_test.sh
