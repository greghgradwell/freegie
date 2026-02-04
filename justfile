# Freegie development commands

venv := "/home/ubuntu/.venvs/freegie-env/bin/activate"

# Start the daemon
run:
    . {{venv}} && python -m freegie daemon

# Start the daemon with debug logging
debug:
    . {{venv}} && python -m freegie daemon --log-level debug

# Show daemon status via CLI
status:
    . {{venv}} && python -m freegie status

# Start the system tray icon
tray:
    . {{venv}} && python -m freegie.tray

# Start the system tray icon with debug logging
tray-debug:
    . {{venv}} && python -m freegie.tray --log-level debug

# Run all unit tests
test:
    . {{venv}} && pytest tests/ -v

# Run hardware integration tests (requires Chargie device)
test-hw:
    . {{venv}} && pytest tests/test_integration.py -v --chargie

# Lint
lint:
    . {{venv}} && ruff check .
