"""Wrapper to run pytest with isolation_venv removed from sys.path."""
import sys

# Remove isolation_venv from sys.path so Anaconda's packages take priority
sys.path = [p for p in sys.path if "isolation_venv" not in p.lower()]

# Now run pytest
if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main(sys.argv[1:]))
