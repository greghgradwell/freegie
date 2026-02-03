"""Freegie daemon entry point: python -m freegie"""

import asyncio
import logging
import signal
from pathlib import Path

from aiohttp import web

from freegie.battery import BatteryReader
from freegie.ble import BLEManager
from freegie.config import load_config
from freegie.engine import ChargeEngine
from freegie.server import create_app

log = logging.getLogger("freegie")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Freegie charge management daemon")
    parser.add_argument("-c", "--config", type=Path, help="Path to config.toml")
    parser.add_argument("-p", "--port", type=int, help="Override HTTP port")
    parser.add_argument("--log-level", default=None, help="Log level (debug, info, warning, error)")
    args = parser.parse_args()

    config = load_config(args.config)

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
    log.info("Charge limit: %d%%, allowed drop: %d%%, PD mode: %d",
             config.charge.limit, config.charge.allowed_drop, config.charge.pd_mode)

    ble = BLEManager()
    battery = BatteryReader()
    engine = ChargeEngine(ble, battery, config.charge)
    app = create_app(engine)

    asyncio.run(_run(app, engine, config.daemon.port))


async def _run(app: web.Application, engine: ChargeEngine, port: int):
    loop = asyncio.get_event_loop()
    stop_event = asyncio.Event()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(_shutdown(engine, stop_event, s)))

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    log.info("HTTP server listening on http://127.0.0.1:%d", port)

    asyncio.create_task(engine.start())

    try:
        await stop_event.wait()
    finally:
        await runner.cleanup()


async def _shutdown(engine: ChargeEngine, stop_event: asyncio.Event, sig: signal.Signals):
    log.info("Received %s, shutting down...", sig.name)
    await engine.stop()
    stop_event.set()


if __name__ == "__main__":
    main()
