"""CLI client for the freegie daemon."""

import json
import sys
import urllib.request
import urllib.error


def _request(url, method="GET", data=None):
    body = None
    headers = {}
    if data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.URLError as e:
        print(f"Error: cannot reach daemon at {url} ({e.reason})", file=sys.stderr)
        sys.exit(1)


def cmd_status(base_url):
    s = _request(f"{base_url}/api/status")

    phase = s.get("phase", "unknown")
    battery = s.get("battery_percent")
    charging = s.get("is_charging", False)
    charge_max = s.get("charge_max")
    charge_min = s.get("charge_min")

    print(f"Phase:     {phase}")
    print(f"Battery:   {battery}%" if battery is not None else "Battery:   --")
    print(f"Charging:  {'yes' if charging else 'no'}")
    if charge_max is not None:
        min_str = f" (min: {charge_min}%)" if charge_min is not None else ""
        print(f"Max:       {charge_max}%{min_str}")

    device = s.get("device")
    if device:
        name = device.get("name", "unknown")
        fw = device.get("firmware", "?")
        hw = device.get("hardware", "?")
        print(f"Device:    {name} (FW: {fw}, HW: {hw})")

    telemetry = s.get("telemetry")
    if telemetry:
        v = telemetry.get("volts", 0)
        a = telemetry.get("amps", 0)
        w = telemetry.get("watts", 0)
        print(f"Telemetry: {v:.2f}V  {a:.2f}A  {w:.2f}W")

    if phase == "reconnecting":
        attempt = s.get("reconnect_attempt", 0)
        delay = s.get("reconnect_delay", 0)
        print(f"Reconnect: attempt {attempt} (next in {delay}s)")


def cmd_set_max(base_url, value):
    resp = _request(f"{base_url}/api/settings", method="PUT", data={"charge_max": value})
    if resp.get("ok"):
        print(f"Charge max set to {value}%")
    else:
        print(f"Error: {resp.get('error', 'unknown')}", file=sys.stderr)
        sys.exit(1)


def cmd_set_min(base_url, value):
    resp = _request(f"{base_url}/api/settings", method="PUT", data={"charge_min": value})
    if resp.get("ok"):
        print(f"Charge min set to {value}%")
    else:
        print(f"Error: {resp.get('error', 'unknown')}", file=sys.stderr)
        sys.exit(1)


def cmd_scan(base_url):
    resp = _request(f"{base_url}/api/scan", method="POST")
    print(resp.get("message", "Scan started"))


def cmd_disconnect(base_url):
    _request(f"{base_url}/api/disconnect", method="POST")
    print("Disconnected")


def cmd_stop(base_url):
    _request(f"{base_url}/api/shutdown", method="POST")
    print("Daemon stopped")


def run_command(args):
    dispatch = {
        "status": lambda: cmd_status(args.url),
        "set-max": lambda: cmd_set_max(args.url, args.value),
        "set-min": lambda: cmd_set_min(args.url, args.value),
        "scan": lambda: cmd_scan(args.url),
        "disconnect": lambda: cmd_disconnect(args.url),
        "stop": lambda: cmd_stop(args.url),
    }
    dispatch[args.command]()
