# Freegie - Product Specification

## Problem

The official Chargie Linux app has fundamental reliability issues:

1. **Window close kills the daemon** — Closing the GUI window terminates the BLE connection and stops charge management. There is no minimize-to-tray. Users must leave a window open 24/7.
2. **Unreliable BLE connection** — Frequent disconnections, slow reconnection, and aggressive scanning cycles that sometimes fail to find a device that's right there.
3. **Monolithic architecture** — GUI and daemon are coupled in a single Nuitka-compiled binary. Can't run headless, can't fix one without the other.

## Solution

**Freegie** is an open-source charge management daemon for Chargie hardware on Linux. It separates concerns into three layers:

- **Daemon** (systemd service) — BLE connection, charge control logic, always running
- **Tray icon** (user session) — Status indicator, notifications, quick controls
- **Web UI** (local HTTP) — Configuration dashboard, telemetry display

## Users

- **Primary:** Linux laptop users with Chargie hardware who want reliable charge limiting
- **Secondary:** Power users who want CLI/API access to their Chargie device

## Core MVP Requirements

### Must Have (v0.1)

1. **BLE Connection** — Scan, connect, and maintain connection to Chargie device
2. **Charge Limiting** — Stop charging at configured percentage by toggling PIO2
3. **Tray Icon** — System tray icon showing connection status and battery level
4. **Desktop Notifications** — Notify on: charge limit reached, device connected/disconnected
5. **Systemd Integration** — Run as a system service, start on boot, survive logout
6. **Config File** — JSON or TOML config for charge limit, PD mode, allowed charge drop

### Should Have (v0.2)

7. **Web UI** — Local web dashboard for status monitoring and configuration
8. **PD Mode Control** — Switch between Basic 5V / Full PD / Half PD
9. **Auto-reconnection** — Detect disconnection and reconnect without user intervention
10. **CLI Tool** — `freegie status`, `freegie set-limit 80`, etc.

### Nice to Have (Full Vision)

11. **Scheduler** — Time-based charge schedules (charge to 100% overnight, limit to 80% during day)
12. **Buffered Discharge Mode** — Cycle between charge/discharge for battery longevity
13. **Voltage Monitoring** — Real-time power telemetry graphs
14. **Multi-device Support** — Manage multiple Chargie devices
15. **D-Bus Interface** — For desktop environment integration

## BLE Protocol Reference

### Device Identity

- **BLE Name:** `Chargie Laptops`
- **Primary Service UUID:** `0000ffd6-0000-1000-8000-00805f9b34fb`
- **Alternate Service UUID:** `0000ffaa-0000-1000-8000-00805f9b34fb`

### AT Command Set

| Command | Response | Purpose |
|---|---|---|
| `AT+CAPA?` | `OK+CAPA:<bitmask>` | Device capabilities (PD, FET2, AUTO) |
| `AT+ISPD?` | `OK+ISPD:<value>` | PD active status |
| `AT+FWVR?` | `OK+FWVR:<version>` | Firmware version |
| `AT+HWVR?` | `OK+HWVR:<version>` | Hardware version |
| `AT+AUTO?` | `OK+AUTO:<value>` | Auto mode status |
| `AT+STAT` | `OK+STAT:<volts>/<amps>` | Power telemetry |
| `AT+STAT?` | `OK+STAT:<volts>/<amps>` | Power telemetry (query variant) |
| `AT+PIO20` | `OK+PIO2:0` | Cut power (stop charging) |
| `AT+PIO21` | `OK+PIO2:1` | Restore power (resume charging) |
| `AT+PDMO1` | `OK+PDMO:1` | PD Mode: Basic 5V |
| `AT+PDMO2` | `OK+PDMO:2` | PD Mode: Full PD negotiation |
| `AT+HALF0` | - | Disable half-power mode |
| `AT+HALF1` | - | Enable half-power (Linux/macOS only) |
| `AT+FET2ON` | - | FET2 switch ON |
| `AT+FET2OFF` | - | FET2 switch OFF |
| `AT+AAUT0` | - | Disable auto mode |

### Connection Sequence

1. Scan for devices advertising `0000ffd6` or `0000ffaa` service UUIDs
2. Fallback: scan for device name `Chargie Laptops` after 15s
3. Connect via GATT
4. Verify device by toggling power:
   - Send `AT+PIO20` (cut power), confirm battery switches to discharging
   - Send `AT+PIO21` (restore power), confirm battery switches to charging
5. Query capabilities: `AT+CAPA?` -> `AT+FWVR?` -> `AT+HWVR?` -> `AT+ISPD?`
6. Configure PD mode based on user setting
7. Start telemetry polling: `AT+STAT` every 3 seconds

### Charge Control Logic

- Battery at or above `charge_limit` -> `AT+PIO20` (cut power)
- Battery drops by `allowed_charge_drop` below limit -> `AT+PIO21` (restore power)
- Battery level read from `/sys/class/power_supply/BAT0/capacity`
- AC status read from `/sys/class/power_supply/AC/online`

### Capabilities Bitmask (CAPA)

Known value: `1047965` — indicates PD=True, FET2=False, AUTO=True.
Exact bit positions TBD (needs further testing with different hardware).

## Success Criteria

- Charge limiting works unattended for 24+ hours without intervention
- Survives BLE disconnection and reconnects automatically
- Closing any UI component does not affect charge management
- CPU usage under 1% during steady-state telemetry polling
