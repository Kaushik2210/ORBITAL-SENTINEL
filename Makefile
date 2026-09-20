# Thin wrapper: all logic lives in scripts/tasks.py so Windows (no `make`) works too.
# Targets are added only when the thing they run exists.
PY ?= python

.PHONY: setup lint format typecheck test check data

setup lint format typecheck test check data:
	$(PY) scripts/tasks.py $@
