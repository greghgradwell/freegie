# Freegie development commands

venv := "/home/ubuntu/.venvs/freegie-env/bin/activate"

# Start the daemon
run:
    . {{venv}} && python -m freegie

# Start the daemon with debug logging
debug:
    . {{venv}} && python -m freegie --log-level debug

# Run all unit tests
test:
    . {{venv}} && pytest tests/ -v

# Run hardware integration tests (requires Chargie device)
test-hw:
    . {{venv}} && pytest tests/test_integration.py -v --chargie

# Lint
lint:
    . {{venv}} && ruff check .
