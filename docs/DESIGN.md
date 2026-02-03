# Freegie - Technical Design

## Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│                    User Session                      │
│                                                      │
│  ┌──────────────┐  ┌──────────────┐                 │
│  │  Tray Icon   │  │  Web Browser │                 │
│  │  (GTK/Qt)    │  │  (localhost)  │                 │
│  └──────┬───────┘  └──────┬───────┘                 │
│         │ HTTP/WS         │ HTTP/WS                  │
└─────────┼─────────────────┼─────────────────────────┘
          │                 │
          ▼                 ▼
┌─────────────────────────────────────────────────────┐
│              freegie daemon (systemd)                │
│                                                      │
│  ┌────────────┐  ┌──────────┐  ┌─────────────────┐ │
│  │  BLE       │  │  Charge  │  │  HTTP/WS Server │ │
│  │  Manager   │◄─┤  Engine  │◄─┤  (API + Static) │ │
│  └─────┬──────┘  └────┬─────┘  └─────────────────┘ │
│        │              │                              │
│        │ BLE/GATT     │ sysfs                        │
└────────┼──────────────┼─────────────────────────────┘
         ▼              ▼
   ┌──────────┐  ┌──────────────┐
   │ Chargie  │  │ /sys/class/  │
   │ Hardware │  │ power_supply │
   └──────────┘  └──────────────┘
```

## Components

### 1. BLE Manager (`freegie/ble.py`)

Handles all Bluetooth Low Energy communication using `bleak`.

**Responsibilities:**
- Scan for Chargie devices by service UUID, fallback to name
- Connect and maintain GATT connection
- Send AT commands, receive OK+ responses via notifications
- Detect disconnections, expose connection state
- Provide async interface: `send_command(cmd) -> response`

**Key design decisions:**
- Single connection at a time (Chargie hardware limitation)
- Queue-based response matching — all BLE notifications go into an `asyncio.Queue`;
  `send_command()` reads from the queue in a loop, skipping unsolicited messages
  (e.g. `OK+PDCP:0x01`) until the expected `OK+<KEY>` arrives
- `asyncio.Lock` serializes commands with 100ms spacing between writes
- Notification-based receive (not polling GATT reads)
- Characteristic UUID: `0000ffe1` for both write and notify (confirmed via live test)
- Two-pass characteristic discovery: try known UUID first, fall back to any writable/notifiable char

### 2. Charge Engine (`freegie/engine.py`)

Core charge management logic. Pure logic, no I/O (depends on BLE manager and battery reader).

**Responsibilities:**
- Read battery level from sysfs
- Decide when to toggle charging on/off based on limits
- Manage PD mode configuration
- Run the 3-second telemetry polling loop
- Track device state (capabilities, firmware, hardware version)

**State machine:**
```
IDLE -> SCANNING -> CONNECTING -> VERIFYING -> CONTROLLING <-> PAUSED
                                                    │              │
                                                    └── DISCONNECTED
                                                           │
                                                    RECONNECTING -> CONTROLLING
```
See [STATE_MACHINE.md](STATE_MACHINE.md) for the full diagram with invariants.

**Charge control logic:**
```python
if battery_percent >= charge_max:
    _power_off()     # AT+PIO20 + verify response
    state = PAUSED

if state == PAUSED and battery_percent <= charge_min:
    _power_on()      # AT+PIO21 + verify response
    state = CONTROLLING
```

### 3. HTTP/WebSocket Server (`freegie/server.py`)

Local API server using `aiohttp`. Serves both the REST API and the web UI static files.

**REST API:**

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/status` | Current state, battery %, connection, telemetry |
| GET | `/api/settings` | Current configuration |
| PUT | `/api/settings` | Update configuration (charge limit, PD mode, etc.) |
| POST | `/api/scan` | Trigger manual BLE scan |
| POST | `/api/disconnect` | Disconnect from device |
| GET | `/` | Web UI (static files) |

**WebSocket:** `ws://localhost:PORT/ws`
- Server pushes state updates on every change (battery level, connection status, telemetry)
- Client can send commands (same as REST API but real-time)

**Message format (WS):**
```json
{
  "type": "status_update",
  "data": {
    "battery_percent": 72,
    "is_charging": true,
    "charge_max": 83,
    "charge_min": 75,
    "phase": "controlling",
    "telemetry": {"volts": 4.24, "amps": 15.0, "watts": 63.6},
    "device": {"name": "Chargie Laptops", "firmware": "10", "hardware": "3.00"}
  }
}
```

### 4. Tray Icon (`freegie/tray.py`)

Lightweight system tray application using `pystray`. Runs in the user session, connects to the daemon via HTTP/WS.

**Features:**
- Battery icon with charge level overlay
- Color coding: green (charging), yellow (paused at limit), red (disconnected)
- Right-click menu: status summary, open web UI, quit tray (not daemon)
- Desktop notifications via `notify2` or `gi.repository.Notify`

**Key design decision:** The tray icon is a **client** of the daemon, not part of it. Closing the tray does nothing to charge management.

### 5. CLI Tool (`freegie/cli.py`)

Thin HTTP client for the daemon API.

```
freegie status          # Show connection state, battery, telemetry
freegie set-max 80      # Change charge max
freegie set-min 75      # Change charge min
freegie scan            # Trigger BLE scan
freegie disconnect      # Disconnect from device
```

## Technology Stack

| Component | Library | Why |
|---|---|---|
| BLE | `bleak` | Best Python BLE library, async, cross-platform |
| HTTP Server | `aiohttp` | Async, lightweight, WebSocket support built-in |
| Tray Icon | `pystray` | Simple, works with GTK and Qt |
| Notifications | `desktop-notifier` | Modern, async-compatible |
| Config | TOML (`tomllib` / `tomli`) | Human-friendly, stdlib in 3.11+ |
| CLI | `argparse` | Stdlib, no dependencies needed |
| Process | `systemd` | Standard Linux service management |

## Configuration

File: `/etc/freegie/config.toml` (system-wide) or `~/.config/freegie/config.toml` (user)

```toml
[charge]
charge_max = 83
charge_min = 75
pd_mode = 2            # 1=Half PD, 2=Full PD
poll_interval = 3      # seconds between sysfs checks
telemetry_interval = 30  # seconds between BLE AT+STAT queries

[daemon]
port = 7380
log_level = "info"

[tray]
notifications = true
```

## File Structure

```
freegie/
  __init__.py
  __main__.py        # Entry point: `python -m freegie`
  protocol.py        # AT command constants, response parsers, BLE UUIDs
  battery.py         # sysfs battery reader (auto-detect BAT + AC paths)
  config.py          # TOML config loader with validation
  ble.py             # BLE Manager (scan, connect, command queue)
  engine.py          # Charge Engine (state machine, limit enforcement, polling)
  server.py          # HTTP/WS API server (aiohttp)
  tray.py            # System tray icon (pystray client)
  cli.py             # CLI tool (HTTP client)
  static/            # Web UI files
tests/
  test_protocol.py   # AT response parsing
  test_battery.py    # sysfs reading with fake fs
  test_config.py     # TOML loading and validation
  test_ble.py        # scan filter, response key matching
  test_engine.py     # state machine, charge enforcement
docs/
  SPEC.md            # Product requirements
  DESIGN.md          # This file
  PLAN.md            # Implementation checklist
  REVERSE_ENGINEERING.md  # Protocol RE notes + live test confirmations
reference/
  frontend/          # Original Chargie app frontend (for adaptation)
  settings.json      # Original config format
pyproject.toml
```

## Systemd Units

**Daemon:** `/etc/systemd/system/freegie.service`
```ini
[Unit]
Description=Freegie Charge Management Daemon
After=bluetooth.target

[Service]
Type=simple
ExecStart=/usr/bin/freegie-daemon
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

**Tray (user session):** `~/.config/systemd/user/freegie-tray.service`
```ini
[Unit]
Description=Freegie Tray Icon
After=graphical-session.target

[Service]
Type=simple
ExecStart=/usr/bin/freegie-tray
Restart=on-failure

[Install]
WantedBy=graphical-session.target
```

## Security

- HTTP server binds to `127.0.0.1` only (no network exposure)
- No authentication needed (localhost only, same as original)
- BLE operations require `CAP_NET_ADMIN` or user in `bluetooth` group
- Daemon runs as dedicated `freegie` user or root (for BLE access)
