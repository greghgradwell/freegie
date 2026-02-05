.PHONY: install install-dev install-tray install-systemd install-desktop uninstall clean test

VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
PYTHON_BIN ?= python3.10

# Development setup
install-dev: $(VENV)
	$(PIP) install -e ".[dev,tray]"

$(VENV):
	$(PYTHON_BIN) -m venv $(VENV)

# Production install
install:
	pip install .

install-tray:
	pip install ".[tray]"

# Generate service files from templates
systemd/freegie.service: systemd/freegie.service.in
	sed -e 's|@USER@|$(USER)|g' -e 's|@PYTHON@|$(CURDIR)/$(VENV)/bin/python|g' -e 's|@PORT_ARG@||g' $< > $@

systemd/freegie-tray.service: systemd/freegie-tray.service.in
	sed -e 's|@PYTHON@|$(CURDIR)/$(VENV)/bin/python|g' $< > $@

freegie-tray.desktop: freegie-tray.desktop.in
	sed -e 's|@FREEGIE_TRAY@|$(CURDIR)/$(VENV)/bin/freegie-tray|g' $< > $@

# Systemd service (system-level daemon)
install-systemd: systemd/freegie.service
	sudo cp systemd/freegie.service /etc/systemd/system/
	sudo systemctl daemon-reload
	sudo systemctl enable freegie
	@echo "Run 'sudo systemctl start freegie' to start the daemon"

# Systemd tray (user-level)
install-tray-systemd: systemd/freegie-tray.service
	mkdir -p ~/.config/systemd/user
	cp systemd/freegie-tray.service ~/.config/systemd/user/
	systemctl --user daemon-reload
	systemctl --user enable freegie-tray
	@echo "Run 'systemctl --user start freegie-tray' to start the tray icon"

# Desktop entry for app launcher
install-desktop: freegie-tray.desktop
	mkdir -p ~/.local/share/applications
	cp freegie-tray.desktop ~/.local/share/applications/
	@echo "Freegie tray added to application launcher"

uninstall:
	-sudo systemctl stop freegie
	-sudo systemctl disable freegie
	-sudo rm /etc/systemd/system/freegie.service
	-systemctl --user stop freegie-tray
	-systemctl --user disable freegie-tray
	-rm ~/.config/systemd/user/freegie-tray.service
	-sudo systemctl daemon-reload
	-systemctl --user daemon-reload
	-rm ~/.local/share/applications/freegie-tray.desktop
	pip uninstall -y freegie

clean:
	rm -f systemd/freegie.service systemd/freegie-tray.service freegie-tray.desktop

test:
	$(PYTHON) -m pytest tests/ -v
