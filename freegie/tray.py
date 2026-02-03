"""System tray icon for freegie — runs as a client of the daemon."""

import json
import logging
import subprocess
import threading
import time
import webbrowser
from urllib.error import URLError
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger(__name__)

DEFAULT_DAEMON_URL = "http://127.0.0.1:7380"
POLL_INTERVAL_S = 5
ICON_SIZE = 64


def _fetch_status(base_url: str) -> dict | None:
    try:
        req = Request(f"{base_url}/api/status", headers={"Accept": "application/json"})
        with urlopen(req, timeout=3) as resp:
            return json.loads(resp.read())
    except (URLError, OSError, json.JSONDecodeError) as e:
        log.debug("Status fetch failed: %s", e)
        return None


def _build_icon(battery_percent: int | None, phase: str, is_charging: bool) -> Image.Image:
    img = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    if phase in ("disconnected", "idle"):
        fill = (180, 180, 180)  # grey
    elif phase == "paused":
        fill = (240, 180, 40)   # yellow
    elif is_charging:
        fill = (80, 200, 80)    # green
    else:
        fill = (80, 160, 240)   # blue (controlling but not charging)

    draw.rounded_rectangle([4, 12, 56, 56], radius=4, outline=fill, width=3)
    draw.rectangle([20, 6, 44, 12], fill=fill)

    if battery_percent is not None:
        fill_height = int(38 * battery_percent / 100)
        if fill_height > 0:
            draw.rectangle([8, 52 - fill_height, 52, 52], fill=fill)

    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16)
    except OSError:
        font = ImageFont.load_default()

    text = f"{battery_percent}%" if battery_percent is not None else "?"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    tx = (ICON_SIZE - tw) // 2
    ty = 16 + (40 - th) // 2

    draw.text((tx + 1, ty + 1), text, fill=(0, 0, 0, 160), font=font)
    draw.text((tx, ty), text, fill=(255, 255, 255), font=font)

    return img


def _send_notification(title: str, body: str):
    try:
        subprocess.Popen(
            ["notify-send", "-a", "Freegie", title, body],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        log.debug("notify-send not available")


def run_tray(daemon_url: str = DEFAULT_DAEMON_URL):
    import pystray

    last_phase = ""

    def build_menu():
        status = _fetch_status(daemon_url)
        if status:
            phase = status.get("phase", "unknown")
            pct = status.get("battery_percent")
            limit = status.get("charge_limit")
            charging = status.get("is_charging", False)

            status_label = f"Battery: {pct}%" if pct is not None else "Battery: --"
            phase_label = f"Phase: {phase}"
            limit_label = f"Limit: {limit}%"
            charging_label = "Charging" if charging else "Not charging"

            device = status.get("device")
            device_label = f"Device: {device['name']}" if device else "Device: not connected"
        else:
            status_label = "Daemon not reachable"
            phase_label = ""
            limit_label = ""
            charging_label = ""
            device_label = ""

        items = [
            pystray.MenuItem(status_label, None, enabled=False),
        ]
        if phase_label:
            items.extend([
                pystray.MenuItem(charging_label, None, enabled=False),
                pystray.MenuItem(phase_label, None, enabled=False),
                pystray.MenuItem(limit_label, None, enabled=False),
                pystray.MenuItem(device_label, None, enabled=False),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Open Web UI", lambda: webbrowser.open(daemon_url)),
            ])
        items.extend([
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit Tray", lambda icon, _: icon.stop()),
        ])
        return pystray.Menu(*items)

    icon = pystray.Icon(
        name="freegie",
        icon=_build_icon(None, "idle", False),
        title="Freegie",
        menu=build_menu(),
    )

    def updater():
        nonlocal last_phase
        while icon.visible:
            status = _fetch_status(daemon_url)
            if status:
                phase = status.get("phase", "unknown")
                pct = status.get("battery_percent")
                charging = status.get("is_charging", False)

                icon.icon = _build_icon(pct, phase, charging)
                icon.menu = build_menu()

                if last_phase and phase != last_phase:
                    if phase == "controlling":
                        _send_notification("Freegie", "Connected to Chargie")
                    elif phase == "disconnected":
                        _send_notification("Freegie", "Chargie disconnected")
                    elif phase == "paused":
                        _send_notification("Freegie", f"Charge limit reached ({pct}%)")

                last_phase = phase
            else:
                icon.icon = _build_icon(None, "idle", False)

            time.sleep(POLL_INTERVAL_S)

    update_thread = threading.Thread(target=updater, daemon=True)

    def on_setup(icon):
        icon.visible = True
        update_thread.start()

    log.info("Starting tray icon (polling %s)", daemon_url)
    icon.run(setup=on_setup)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Freegie tray icon")
    parser.add_argument("--url", default=DEFAULT_DAEMON_URL, help="Daemon URL")
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args()

    level = getattr(logging, args.log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)-25s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    run_tray(daemon_url=args.url)


if __name__ == "__main__":
    main()
