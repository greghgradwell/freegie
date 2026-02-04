"""HTTP and WebSocket server for the freegie daemon."""

import asyncio
import json
import logging
from pathlib import Path

from aiohttp import web

from freegie.engine import ChargeEngine

log = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent / "static"

_engine_key = web.AppKey("engine", ChargeEngine)
_ws_clients_key = web.AppKey("ws_clients", set)
_static_dir_key = web.AppKey("static_dir", Path)
_stop_event_key = web.AppKey("stop_event", asyncio.Event)
_start_task_key = web.AppKey("start_task", asyncio.Task)


def create_app(
    engine: ChargeEngine,
    stop_event: asyncio.Event | None = None,
    start_task: asyncio.Task | None = None,
    static_dir: Path = _STATIC_DIR,
) -> web.Application:
    app = web.Application()
    app[_engine_key] = engine
    app[_ws_clients_key] = set()
    app[_static_dir_key] = static_dir
    app.on_shutdown.append(_on_shutdown)

    if stop_event is not None:
        app[_stop_event_key] = stop_event
    if start_task is not None:
        app[_start_task_key] = start_task

    app.router.add_get("/api/status", handle_status)
    app.router.add_get("/api/settings", handle_get_settings)
    app.router.add_put("/api/settings", handle_put_settings)
    app.router.add_post("/api/scan", handle_scan)
    app.router.add_post("/api/disconnect", handle_disconnect)
    app.router.add_post("/api/override", handle_override)
    app.router.add_post("/api/poll", handle_poll)
    app.router.add_post("/api/shutdown", handle_shutdown)
    app.router.add_get("/ws", handle_websocket)

    if static_dir.is_dir():
        app.router.add_static("/static", static_dir, show_index=False)
        app.router.add_get("/", handle_index)

    engine.on_update(lambda: asyncio.ensure_future(_broadcast(app)))

    return app


async def handle_status(request: web.Request) -> web.Response:
    engine: ChargeEngine = request.app[_engine_key]
    return web.json_response(engine.status())


async def handle_get_settings(request: web.Request) -> web.Response:
    engine: ChargeEngine = request.app[_engine_key]
    cfg = engine.charge_config
    return web.json_response({
        "charge_max": cfg.charge_max,
        "charge_min": cfg.charge_min,
        "pd_mode": cfg.pd_mode,
        "telemetry_interval": cfg.telemetry_interval,
    })


async def handle_put_settings(request: web.Request) -> web.Response:
    engine: ChargeEngine = request.app[_engine_key]
    try:
        data = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    try:
        engine.update_config(
            charge_max=data.get("charge_max"),
            charge_min=data.get("charge_min"),
            pd_mode=data.get("pd_mode"),
            telemetry_interval=data.get("telemetry_interval"),
        )
    except (ValueError, TypeError) as e:
        return web.json_response({"error": str(e)}, status=400)
    return web.json_response({"ok": True})


async def handle_scan(request: web.Request) -> web.Response:
    engine: ChargeEngine = request.app[_engine_key]
    asyncio.create_task(engine.start())
    return web.json_response({"ok": True, "message": "Scan started"})


async def handle_disconnect(request: web.Request) -> web.Response:
    engine: ChargeEngine = request.app[_engine_key]
    await engine.stop()
    return web.json_response({"ok": True})


async def handle_override(request: web.Request) -> web.Response:
    engine: ChargeEngine = request.app[_engine_key]
    try:
        data = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    try:
        await engine.set_override(data.get("mode"))
    except (ValueError, ConnectionError) as e:
        return web.json_response({"error": str(e)}, status=400)
    return web.json_response({"ok": True})


async def handle_poll(request: web.Request) -> web.Response:
    engine: ChargeEngine = request.app[_engine_key]
    try:
        await engine.poll_telemetry()
    except (ConnectionError, TimeoutError) as e:
        return web.json_response({"error": str(e)}, status=400)
    return web.json_response({"ok": True, "data": engine.status()})


async def handle_shutdown(request: web.Request) -> web.Response:
    stop_event = request.app.get(_stop_event_key)
    start_task = request.app.get(_start_task_key)
    engine = request.app[_engine_key]
    resp = web.json_response({"ok": True})
    await resp.prepare(request)
    await resp.write_eof()
    asyncio.create_task(_trigger_shutdown(engine, stop_event, start_task))
    return resp


async def _trigger_shutdown(engine, stop_event, start_task):
    log.info("Shutdown requested via API")
    if start_task is not None:
        start_task.cancel()
    try:
        await asyncio.wait_for(engine.stop(), timeout=5.0)
    except Exception as e:
        log.warning("Shutdown incomplete: %s", e)
    if stop_event is not None:
        stop_event.set()


async def _on_shutdown(app: web.Application):
    for ws in set(app[_ws_clients_key]):
        try:
            await ws.close()
        except Exception:
            pass


async def handle_index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(request.app[_static_dir_key] / "index.html")


async def handle_websocket(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    request.app[_ws_clients_key].add(ws)
    log.info("WebSocket client connected (%d total)", len(request.app[_ws_clients_key]))

    engine: ChargeEngine = request.app[_engine_key]

    await ws.send_json({"type": "status_update", "data": engine.status()})

    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                await _handle_ws_message(engine, ws, msg.data)
            elif msg.type == web.WSMsgType.ERROR:
                log.warning("WebSocket error: %s", ws.exception())
    finally:
        request.app[_ws_clients_key].discard(ws)
        log.info("WebSocket client disconnected (%d remaining)", len(request.app[_ws_clients_key]))

    return ws


async def _handle_ws_message(engine: ChargeEngine, ws: web.WebSocketResponse, raw: str):
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        await ws.send_json({"type": "error", "message": "Invalid JSON"})
        return

    msg_type = data.get("type")

    if msg_type == "set_max":
        try:
            engine.update_config(charge_max=data.get("value"))
        except (ValueError, TypeError) as e:
            await ws.send_json({"type": "error", "message": str(e)})
            return
        await ws.send_json({"type": "status_update", "data": engine.status()})

    elif msg_type == "set_min":
        try:
            engine.update_config(charge_min=data.get("value"))
        except (ValueError, TypeError) as e:
            await ws.send_json({"type": "error", "message": str(e)})
            return
        await ws.send_json({"type": "status_update", "data": engine.status()})

    elif msg_type == "scan":
        asyncio.create_task(engine.start())
        await ws.send_json({"type": "status_update", "data": engine.status()})

    elif msg_type == "disconnect":
        await engine.stop()
        await ws.send_json({"type": "status_update", "data": engine.status()})

    elif msg_type == "override":
        try:
            await engine.set_override(data.get("value"))
        except (ValueError, ConnectionError) as e:
            await ws.send_json({"type": "error", "message": str(e)})
            return
        await ws.send_json({"type": "status_update", "data": engine.status()})

    else:
        await ws.send_json({"type": "error", "message": f"Unknown type: {msg_type}"})


async def _broadcast(app: web.Application):
    engine: ChargeEngine = app[_engine_key]
    payload = {"type": "status_update", "data": engine.status()}

    dead = set()
    for ws in app[_ws_clients_key]:
        try:
            await ws.send_json(payload)
        except (ConnectionError, RuntimeError):
            dead.add(ws)

    app[_ws_clients_key] -= dead
