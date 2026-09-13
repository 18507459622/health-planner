# health-planner dev commands
#
# Windows needs make (Git Bash / WSL / scoop install make all work).
# Without make, just copy any command below into PowerShell.

VENV := backend/.venv
ifeq ($(OS),Windows_NT)
PY := $(CURDIR)/$(VENV)/Scripts/python.exe
else
PY := $(CURDIR)/$(VENV)/bin/python
endif

.PHONY: help install install-dev test test-verbose test-gaps run clean

help:
	@echo "make install       install runtime deps"
	@echo "make install-dev   install runtime + test deps"
	@echo "make test          run all tests"
	@echo "make test-gaps     list known-gap cases (= the fix backlog)"
	@echo "make run           start backend"

install:
	$(PY) -m pip install -r backend/requirements.txt

install-dev:
	$(PY) -m pip install -r backend/requirements-dev.txt

test:
	$(PY) -m pytest backend

test-verbose:
	$(PY) -m pytest backend -v

# Known-gap backlog: these cases assert CURRENT behaviour.
# Every time one gets fixed, drop its @pytest.mark.gap.
test-gaps:
	$(PY) -m pytest backend -m gap -v

run:
	cd backend && $(PY) -m uvicorn server:app --reload

clean:
	-find . -type d -name __pycache__ -prune -exec rm -rf {} +
	-find . -type d -name .pytest_cache -prune -exec rm -rf {} +
