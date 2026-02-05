# Freegie development commands

venv := env("FREEGIE_VENV", ".venv/bin/activate")

# --- Service (systemd) ---

# Start the service
start:
    sudo systemctl start freegie

# Stop the service
stop:
    sudo systemctl stop freegie

# Restart the service
restart:
    sudo systemctl restart freegie

# Show service status
status:
    systemctl status freegie

# Follow service logs
logs:
    journalctl -u freegie -f

# --- Daemon (direct, no systemd) ---

# Run the daemon directly
daemon-run:
    . {{venv}} && python -m freegie daemon

# Run the daemon with debug logging
daemon-debug:
    . {{venv}} && python -m freegie daemon --log-level debug

# Query the running daemon via CLI
daemon-status:
    . {{venv}} && python -m freegie status

# --- Tray ---

# Start the system tray icon
tray:
    . {{venv}} && python -m freegie.tray

# Start the system tray icon with debug logging
tray-debug:
    . {{venv}} && python -m freegie.tray --log-level debug

# --- Development ---

# Run all unit tests
test:
    . {{venv}} && pytest tests/ -v

# Run hardware integration tests (requires Chargie device)
test-hw:
    . {{venv}} && pytest tests/test_integration.py -v --chargie

# Lint
lint:
    . {{venv}} && ruff check .
