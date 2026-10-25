# Common tasks. `make help` lists them. PY defaults to the virtualenv's python if present.
PY ?= $(shell test -x .venv/bin/python && echo .venv/bin/python || echo python)

.PHONY: help setup run run-offline eval custom test lint verify reports package

help:  ## list the targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*## /\t/'

setup:  ## create .venv and install requirements
	python -m venv .venv
	.venv/bin/pip install -r requirements.txt

run:  ## full split -> output.csv (agent mode when a GROQ/GEMINI key is set, else deterministic)
	$(PY) code/main.py run --split full

run-offline:  ## full split from caches and checkpoints only (no network, no keys needed)
	$(PY) code/main.py run --split full --no-llm

eval:  ## score the sample split (deterministic, agent, oracle links)
	$(PY) code/main.py eval --mode deterministic --no-llm
	$(PY) code/main.py eval --mode agent --no-llm
	$(PY) code/main.py eval --oracle-links

custom:  ## run the custom evaluation cases
	$(PY) code/main.py eval --custom --mode deterministic --no-llm

test:  ## unit tests (includes the golden-output lock)
	$(PY) -m pytest -q code/tests

lint:  ## ruff
	$(PY) -m ruff check code

verify: test lint eval  ## tests + lint + evaluation (the golden lock runs inside the tests)

reports:  ## numbers -> usage report -> render README / ARCHITECTURE / DECISIONS / evaluation_report
	$(PY) code/evaluation/report_numbers.py
	$(PY) code/evaluation/usage_report.py
	$(PY) code/docs/render.py

package:  ## build code.zip (README, requirements, code/ without runs, .env, dataset or log)
	$(PY) code/package.py
