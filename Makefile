# MAOP — local test runner helper
#
# Cross-platform Makefile supporting Linux, macOS, and Windows (Git Bash/WSL).
# Creates an isolated virtualenv, installs locked runtime deps + pytest,
# and runs the pytest suite.
#
# Usage:
#   make install   # 建 .venv + 装本地开发依赖(requirements.txt 指引子集, 避开 etcd3/torch) + pytest
#   make test      # run the python test suite (pytest ./py/tests)
#   make lint      # run ruff linter
#   make clean     # remove .venv and pytest cache
#
# Notes:
#   * PYTHON defaults to `python3`; override with `make PYTHON=python3.13 install`.
#   * The venv is created at the repo root (.venv) so it is not committed.
#   * Tests are run from py/ so `import maop` resolves without an editable install.
#   * `make install` 用 py/requirements.txt（开发指引子集，避开 etcd3/torch 重依赖）；
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

.PHONY: install test lint clean

install:
	$(PYTHON) -m venv $(VENV)
	$(VENV_PI) install --upgrade pip
	$(VENV_PI) install -r $(PY_DIR)/requirements.txt
	$(VENV_PI) install pytest pytest-asyncio pytest-xdist pytest-cov ruff

test:
	cd $(PY_DIR) && ../$(VENV_PY) -m pytest tests/ -q --ignore=tests/contract
	cd $(PY_DIR) && ../$(VENV_PY) -m pytest tests/contract/ -q -m contract

lint:
	$(VENV_PY) -m ruff check $(PY_DIR)/maop/

clean:
	rm -rf $(VENV) $(PY_DIR)/.pytest_cache $(PY_DIR)/.tmp
