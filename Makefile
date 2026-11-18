.PHONY: install demo test lint dashboard diff

install:
	python3 -m pip install -e ".[dev]"

demo:
	python3 -m exeat demo

test:
	python3 -m pytest --cov=exeat --cov-fail-under=90

lint:
	python3 -m ruff check src tests
	python3 -m ruff format --check src tests
	python3 -m mypy src

dashboard:
	python3 -m exeat dashboard

diff:
	python3 -m exeat diff --example escalation; test $$? -eq 1
	python3 -m exeat diff --example narrowing
