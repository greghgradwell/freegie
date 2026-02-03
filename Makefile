.PHONY: install install-dev install-tray install-systemd uninstall test

VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

# Development setup
install-dev: $(VENV)
	$(PIP) install -e ".[dev,tray]"

$(VENV):
	python3 -m venv $(VENV)

# Production install
install:
	pip install .

install-tray:
	pip install ".[tray]"

# Systemd service (system-level daemon)
install-systemd:
	sudo cp systemd/freegie.service /etc/systemd/system/
	sudo systemctl daemon-reload
	sudo systemctl enable freegie
	@echo "Run 'sudo systemctl start freegie' to start the daemon"

# Systemd tray (user-level)
install-tray-systemd:
	mkdir -p ~/.config/systemd/user
	cp systemd/freegie-tray.service ~/.config/systemd/user/
	systemctl --user daemon-reload
	systemctl --user enable freegie-tray
	@echo "Run 'systemctl --user start freegie-tray' to start the tray icon"

uninstall:
	-sudo systemctl stop freegie
	-sudo systemctl disable freegie
	-sudo rm /etc/systemd/system/freegie.service
	-systemctl --user stop freegie-tray
	-systemctl --user disable freegie-tray
	-rm ~/.config/systemd/user/freegie-tray.service
	-sudo systemctl daemon-reload
	-systemctl --user daemon-reload
	pip uninstall -y freegie

test:
	$(PYTHON) -m pytest tests/ -v
