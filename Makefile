# MAOP — local test runner helper
#
# Cross-platform Makefile supporting Linux, macOS, and Windows (Git Bash/WSL).
# Creates an isolated virtualenv, installs locked runtime deps + pytest,
# and runs the pytest suite.
#
# Usage:
#   make install   # 建 .venv + 以可编辑模式安装 py[dev] 开发依赖 (pyproject.toml)
#   make test      # run the python test suite (pytest ./py/tests)
#   make lint      # run ruff linter
#   make clean     # remove .venv and pytest cache
#
# Notes:
#   * PYTHON defaults to `python3`; override with `make PYTHON=python3.13 install`.
#   * The venv is created at the repo root (.venv) so it is not committed.
#   * Tests are run from py/ so `import maop` resolves without an editable install.
#   * `make install` 用 pip install -e "py[dev]"（pyproject.toml [project.optional-dependencies] dev）；
#     权威锁文件 py/requirements.lock 仅用于 CI 的 SBOM 与 pip-audit，不在此安装。

PYTHON  ?= python3
VENV    ?= .venv
PY_DIR  := py

# Cross-platform venv binary detection
ifeq ($(OS),Windows_NT)
  VENV_PY := $(VENV)/Scripts/python
  VENV_PI := $(VENV)/Scripts/pip
else
  VENV_PY := $(VENV)/bin/python
  VENV_PI := $(VENV)/bin/pip
endif

.PHONY: install test test-all lint clean

install:
	$(PYTHON) -m venv $(VENV)
	$(VENV_PI) install --upgrade pip
	$(VENV_PI) install -e "$(PY_DIR)[dev]"

test:
	cd $(PY_DIR) && ../$(VENV_PY) -m pytest tests/ -q --ignore=tests/contract
	cd $(PY_DIR) && ../$(VENV_PY) -m pytest tests/contract/ -q -m contract

# P3-F-01 fix: test-all target includes e2e/performance/reliability/stability
# tests that the `test` target excludes. This matches CI's independent jobs
# (test, performance) so `make test-all` locally validates what CI validates.
test-all:
	cd $(PY_DIR) && ../$(VENV_PY) -m pytest tests/ -q --ignore=tests/contract
	cd $(PY_DIR) && ../$(VENV_PY) -m pytest tests/contract/ -q -m contract
	cd $(PY_DIR) && ../$(VENV_PY) -m pytest tests/e2e/ -q --timeout=15 -n 0
	cd $(PY_DIR) && ../$(VENV_PY) -m pytest tests/performance/ -q -m slow --timeout=120
	cd $(PY_DIR) && ../$(VENV_PY) -m pytest tests/reliability/ tests/stability/ -q --timeout=120

# P3-F-02 fix: lint checks both maop/ and tests/ to match CI (ci.yml:55
# runs `ruff check maop/ tests/`). Previously only maop/ was checked, so
# test code lint errors were invisible locally.
lint:
	$(VENV_PY) -m ruff check $(PY_DIR)/maop/ $(PY_DIR)/tests/

clean:
	rm -rf $(VENV) $(PY_DIR)/.pytest_cache $(PY_DIR)/.tmp
