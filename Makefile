.PHONY: install demo test lint dashboard diff

install:
	python3 -m pip install -e ".[dev]"

demo:
	python3 -m agentleash demo

test:
	python3 -m pytest --cov=agentleash --cov-fail-under=85

lint:
	python3 -m ruff check src tests
	python3 -m ruff format --check src tests
	python3 -m mypy src

dashboard:
	python3 -m agentleash dashboard

diff:
	python3 -m agentleash diff --example escalation; test $$? -eq 1
	python3 -m agentleash diff --example narrowing
