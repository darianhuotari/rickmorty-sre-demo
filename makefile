# Python virtualenv
VENV ?= .venv
PYTHON = $(VENV)/Scripts/python.exe
PIP = $(VENV)/Scripts/pip.exe

# Fallbacks for POSIX systems
ifeq ($(OS),Windows_NT)
    ACTIVATE = $(VENV)/Scripts/activate
else
    PYTHON = $(VENV)/bin/python
    PIP = $(VENV)/bin/pip
    ACTIVATE = source $(VENV)/bin/activate
endif

.PHONY: help venv install dev run lint format test coverage clean

help:
	@echo "Common commands:"
	@echo "  make venv      - create virtualenv"
	@echo "  make install   - install runtime deps"
	@echo "  make dev       - install runtime + dev deps"
	@echo "  make run       - start FastAPI app (uvicorn)"
	@echo "  make lint      - run black (check only) & flake8"
	@echo "  make format    - auto-format with black"
	@echo "  make test      - run pytest with coverage (min 80%)"
	@echo "  make coverage  - generate HTML coverage report"
	@echo "  make clean     - remove caches and venv"

venv:
	python -m venv $(VENV)

install: venv
	$(PIP) install -r requirements.txt

dev: install
	$(PIP) install -r requirements-dev.txt
	$(PIP) install -e .

run:
	$(PYTHON) -m uvicorn app.main:app --reload --port 8000

lint:
	$(PYTHON) -m pip install black flake8 --quiet
	$(PYTHON) -m black --check app tests
	$(PYTHON) -m flake8 app tests

format:
	$(PYTHON) -m black app tests

test:
	$(PYTHON) -m pytest --cov=app --cov-report=term-missing --cov-fail-under=80 -q

coverage:
	$(PYTHON) -m pytest --cov=app --cov-report=html

clean:
	rm -rf __pycache__ .pytest_cache .mypy_cache .coverage htmlcov
	rm -rf $(VENV)
