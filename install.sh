#!/bin/bash
set -e

# Freegie install script
# Usage: ./install.sh [--with-tray] [--no-systemd] [--uninstall]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

NO_SYSTEMD=false
WITH_TRAY=false
UNINSTALL=false
PORT=""

# Track if user explicitly set PYTHON_BIN
USER_SET_PYTHON="${PYTHON_BIN:+true}"
PYTHON_BIN="${PYTHON_BIN:-python3.10}"

for arg in "$@"; do
    case $arg in
        --no-systemd) NO_SYSTEMD=true ;;
        --with-tray) WITH_TRAY=true ;;
        --uninstall) UNINSTALL=true ;;
        --port=*)
            PORT="${arg#*=}"
            if ! [[ "$PORT" =~ ^[0-9]+$ ]] || [ "$PORT" -lt 1 ] || [ "$PORT" -gt 65535 ]; then
                echo "ERROR: Invalid port number: $PORT (must be 1-65535)"
                exit 1
            fi
            ;;
        --help|-h)
            echo "Usage: ./install.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --with-tray    Install system tray icon (requires system packages)"
            echo "  --port=PORT    Set daemon port (default: 7380)"
            echo "  --no-systemd   Skip systemd service installation"
            echo "  --uninstall    Remove freegie (service, tray, venv)"
            echo "  --help         Show this help message"
            echo ""
            echo "Environment variables:"
            echo "  PYTHON_BIN    Python binary to use (default: python3.10)"
            echo ""
            echo "System tray requirements (--with-tray):"
            echo "  sudo apt install gir1.2-ayatanaappindicator3-0.1 python3-gi"
            exit 0
            ;;
    esac
done

# --- Uninstall ---
if [ "$UNINSTALL" = true ]; then
    echo "==> Uninstalling freegie..."

    # Stop and remove systemd service
    if systemctl is-active --quiet freegie 2>/dev/null; then
        echo "    Stopping freegie service..."
        sudo systemctl stop freegie
    fi
    if systemctl is-enabled --quiet freegie 2>/dev/null; then
        echo "    Disabling freegie service..."
        sudo systemctl disable freegie
    fi
    if [ -f /etc/systemd/system/freegie.service ]; then
        echo "    Removing systemd service file..."
        sudo rm /etc/systemd/system/freegie.service
        sudo systemctl daemon-reload
    fi

    # Kill tray processes
    if pgrep -f freegie-tray > /dev/null 2>&1; then
        echo "    Stopping tray icon..."
        pkill -9 -f freegie-tray || true
    fi

    # Remove desktop entry
    if [ -f ~/.local/share/applications/freegie-tray.desktop ]; then
        echo "    Removing desktop entry..."
        rm ~/.local/share/applications/freegie-tray.desktop
    fi

    # Remove generated files
    echo "    Removing generated files..."
    rm -f "$SCRIPT_DIR/systemd/freegie.service"
    rm -f "$SCRIPT_DIR/systemd/freegie-tray.service"
    rm -f "$SCRIPT_DIR/freegie-tray.desktop"

    # Remove venv
    if [ -d "$VENV_DIR" ]; then
        echo "    Removing virtual environment..."
        rm -rf "$VENV_DIR"
    fi

    echo ""
    echo "==> Uninstall complete!"
    echo ""
    echo "Note: Config files in ~/.config/freegie/ were preserved."
    echo "To remove them: rm -rf ~/.config/freegie"
    exit 0
fi

# --- Install ---

# Check system packages for tray
if [ "$WITH_TRAY" = true ]; then
    echo "==> Checking system packages for tray..."
    MISSING_PKGS=""

    if ! python3 -c "import gi" 2>/dev/null; then
        MISSING_PKGS="python3-gi"
    fi

    if ! python3 -c "import gi; gi.require_version('AyatanaAppIndicator3', '0.1')" 2>/dev/null; then
        MISSING_PKGS="$MISSING_PKGS gir1.2-ayatanaappindicator3-0.1"
    fi

    if [ -n "$MISSING_PKGS" ]; then
        echo "ERROR: Missing system packages for tray:$MISSING_PKGS"
        echo ""
        echo "Install them with:"
        echo "  sudo apt install$MISSING_PKGS"
        echo ""
        echo "Then re-run this script."
        exit 1
    fi
    echo "    System packages OK"

    # System gi module requires matching Python version (typically 3.10 on Ubuntu 22.04)
    # Override PYTHON_BIN unless user explicitly set it
    if [ "$USER_SET_PYTHON" != "true" ]; then
        PYTHON_BIN="python3.10"
        echo "    Using Python 3.10 (required for system gi module)"
    fi
fi

echo "==> Creating virtual environment..."
if [ "$WITH_TRAY" = true ]; then
    $PYTHON_BIN -m venv --system-site-packages "$VENV_DIR"
else
    $PYTHON_BIN -m venv "$VENV_DIR"
fi

echo "==> Upgrading pip..."
"$VENV_DIR/bin/pip" install --upgrade pip

echo "==> Installing freegie..."
if [ "$WITH_TRAY" = true ]; then
    "$VENV_DIR/bin/pip" install -e "$SCRIPT_DIR[tray]"
else
    "$VENV_DIR/bin/pip" install -e "$SCRIPT_DIR"
fi

if [ "$NO_SYSTEMD" = false ]; then
    echo "==> Generating systemd service file..."
    PORT_ARG=""
    if [ -n "$PORT" ]; then
        PORT_ARG=" daemon --port $PORT"
    fi
    sed -e "s|@USER@|$USER|g" -e "s|@PYTHON@|$VENV_DIR/bin/python|g" -e "s|@PORT_ARG@|$PORT_ARG|g" \
        "$SCRIPT_DIR/systemd/freegie.service.in" > "$SCRIPT_DIR/systemd/freegie.service"

    echo "==> Installing systemd service (requires sudo)..."
    sudo cp "$SCRIPT_DIR/systemd/freegie.service" /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable freegie
    sudo systemctl start freegie

    echo "==> Checking service status..."
    systemctl status freegie --no-pager
fi

if [ "$WITH_TRAY" = true ]; then
    echo "==> Generating desktop entry..."
    sed -e "s|@FREEGIE_TRAY@|$VENV_DIR/bin/freegie-tray|g" \
        "$SCRIPT_DIR/freegie-tray.desktop.in" > "$SCRIPT_DIR/freegie-tray.desktop"

    echo "==> Installing desktop entry..."
    mkdir -p ~/.local/share/applications
    cp "$SCRIPT_DIR/freegie-tray.desktop" ~/.local/share/applications/
fi

echo ""
echo "==> Installation complete!"
echo ""
echo "Commands:"
echo "  systemctl status freegie     # Check daemon status"
echo "  journalctl -u freegie -f     # Follow daemon logs"
echo "  Web UI: http://127.0.0.1:7380"
if [ "$WITH_TRAY" = true ]; then
    echo "  freegie-tray                 # Run tray icon (or find 'Freegie Tray' in app launcher)"
else
    echo ""
    echo "To add the system tray icon later, re-run with --with-tray"
fi
echo ""
echo "To uninstall: ./install.sh --uninstall"
