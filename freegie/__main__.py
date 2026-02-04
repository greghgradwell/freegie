"""Freegie entry point: python -m freegie"""

import asyncio
import logging
import signal
from pathlib import Path

from aiohttp import web

from freegie.battery import BatteryReader
from freegie.ble import BLEManager
from freegie.config import load_config, load_state
from freegie.engine import ChargeEngine
from freegie.server import create_app

log = logging.getLogger("freegie")

_DEFAULT_URL = "http://127.0.0.1:7380"

_shutting_down = False


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Freegie charge management")
    parser.set_defaults(config=None, port=None, log_level=None)
    sub = parser.add_subparsers(dest="command")

    # daemon subcommand (also the default when no subcommand given)
    daemon_parser = sub.add_parser("daemon", help="Run the daemon (default)")
    daemon_parser.add_argument("-c", "--config", type=Path, help="Path to config.toml")
    daemon_parser.add_argument("-p", "--port", type=int, help="Override HTTP port")
    daemon_parser.add_argument("--log-level", default=None, help="Log level")

    # CLI subcommands
    status_parser = sub.add_parser("status", help="Show daemon status")
    status_parser.add_argument("--url", default=_DEFAULT_URL, help="Daemon URL")

    set_max_parser = sub.add_parser("set-max", help="Set charge max")
    set_max_parser.add_argument("value", type=int, help="Max percentage (20-100)")
    set_max_parser.add_argument("--url", default=_DEFAULT_URL, help="Daemon URL")

    set_min_parser = sub.add_parser("set-min", help="Set charge min")
    set_min_parser.add_argument("value", type=int, help="Min percentage (20-100)")
    set_min_parser.add_argument("--url", default=_DEFAULT_URL, help="Daemon URL")

    scan_parser = sub.add_parser("scan", help="Start BLE scan")
    scan_parser.add_argument("--url", default=_DEFAULT_URL, help="Daemon URL")

    disconnect_parser = sub.add_parser("disconnect", help="Disconnect from device")
    disconnect_parser.add_argument("--url", default=_DEFAULT_URL, help="Daemon URL")

    stop_parser = sub.add_parser("stop", help="Stop the daemon")
    stop_parser.add_argument("--url", default=_DEFAULT_URL, help="Daemon URL")

    args = parser.parse_args()

    if args.command is None or args.command == "daemon":
        _run_daemon(args)
    else:
        from freegie.cli import run_command
        run_command(args)


def _run_daemon(args):
    config = load_config(args.config)
    load_state(config)

    if args.port:
        config.daemon.port = args.port
    if args.log_level:
        config.daemon.log_level = args.log_level

    level = getattr(logging, config.daemon.log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)-25s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    log.info("Freegie v0.1.0 starting")
    log.info("Charge max: %d%%, charge min: %d%%, PD mode: %d",
             config.charge.charge_max, config.charge.charge_min, config.charge.pd_mode)

    ble = BLEManager()
    battery = BatteryReader()
    engine = ChargeEngine(ble, battery, config.charge)

    asyncio.run(_run(engine, config.daemon.port))


async def _run(engine: ChargeEngine, port: int):
    global _shutting_down
    _shutting_down = False

    loop = asyncio.get_event_loop()
    stop_event = asyncio.Event()
    start_task = asyncio.create_task(engine.start())

    app = create_app(engine, stop_event=stop_event, start_task=start_task)

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(
            sig,
            lambda s=sig: asyncio.create_task(_shutdown(engine, stop_event, start_task, s)),
        )

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    log.info("HTTP server listening on http://127.0.0.1:%d", port)

    try:
        await stop_event.wait()
    finally:
        await runner.cleanup()


async def _shutdown(engine, stop_event, start_task, sig=None):
    global _shutting_down
    if _shutting_down:
        return
    _shutting_down = True

    if sig:
        log.info("Received %s, shutting down...", sig.name)
    else:
        log.info("Shutdown requested via API")

    start_task.cancel()
    try:
        await asyncio.wait_for(engine.stop(), timeout=5.0)
    except Exception as e:
        log.warning("Shutdown incomplete: %s", e)
    stop_event.set()


if __name__ == "__main__":
    main()
