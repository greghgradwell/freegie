import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from freegie.config import ChargeConfig
from freegie.engine import ChargeEngine
from freegie.server import create_app

log = logging.getLogger(__name__)


@pytest.fixture
def engine():
    ble = MagicMock()
    ble.send_command = AsyncMock(return_value="OK+PIO2:1")
    ble.scan = AsyncMock(return_value=MagicMock())
    ble.connect = AsyncMock(return_value=True)
    ble.disconnect = AsyncMock()
    ble.on_state_change = MagicMock()
    ble.device_name = "Chargie Laptops"

    battery = MagicMock()
    battery.read_percent = MagicMock(return_value=72)
    battery.available = True

    config = ChargeConfig(limit=80, allowed_drop=5, pd_mode=2)
    return ChargeEngine(ble, battery, config)


@pytest.fixture
async def client(engine):
    app = create_app(engine, static_dir=MagicMock(is_dir=MagicMock(return_value=False)))
    async with TestClient(TestServer(app)) as c:
        yield c


@pytest.mark.asyncio
async def test_status_returns_json(client):
    resp = await client.get("/api/status")
    assert resp.status == 200
    data = await resp.json()
    assert data["phase"] == "idle"
    assert data["battery_percent"] == 72
    assert data["charge_limit"] == 80


@pytest.mark.asyncio
async def test_get_settings(client):
    resp = await client.get("/api/settings")
    assert resp.status == 200
    data = await resp.json()
    assert data["charge_limit"] == 80
    assert data["allowed_drop"] == 5
    assert data["pd_mode"] == 2


@pytest.mark.asyncio
async def test_put_settings_valid(client):
    resp = await client.put("/api/settings", json={"charge_limit": 90})
    assert resp.status == 200
    data = await resp.json()
    assert data["ok"] is True

    resp = await client.get("/api/settings")
    data = await resp.json()
    assert data["charge_limit"] == 90


@pytest.mark.asyncio
async def test_put_settings_invalid_limit(client):
    resp = await client.put("/api/settings", json={"charge_limit": 999})
    assert resp.status == 400
    data = await resp.json()
    assert "error" in data


@pytest.mark.asyncio
async def test_put_settings_invalid_json(client):
    resp = await client.put(
        "/api/settings",
        data=b"not json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == 400


@pytest.mark.asyncio
async def test_scan_returns_ok(client):
    resp = await client.post("/api/scan")
    assert resp.status == 200
    data = await resp.json()
    assert data["ok"] is True


@pytest.mark.asyncio
async def test_disconnect_returns_ok(client):
    resp = await client.post("/api/disconnect")
    assert resp.status == 200
    data = await resp.json()
    assert data["ok"] is True


@pytest.mark.asyncio
async def test_websocket_initial_status(client):
    async with client.ws_connect("/ws") as ws:
        msg = await ws.receive_json()
        assert msg["type"] == "status_update"
        assert msg["data"]["phase"] == "idle"


@pytest.mark.asyncio
async def test_websocket_set_limit_valid(client):
    async with client.ws_connect("/ws") as ws:
        await ws.receive_json()  # initial status
        await ws.send_json({"type": "set_limit", "value": 90})
        msg = await ws.receive_json()
        assert msg["type"] == "status_update"
        assert msg["data"]["charge_limit"] == 90


@pytest.mark.asyncio
async def test_websocket_set_limit_invalid(client):
    async with client.ws_connect("/ws") as ws:
        await ws.receive_json()  # initial status
        await ws.send_json({"type": "set_limit", "value": 999})
        msg = await ws.receive_json()
        assert msg["type"] == "error"


@pytest.mark.asyncio
async def test_websocket_unknown_type(client):
    async with client.ws_connect("/ws") as ws:
        await ws.receive_json()  # initial status
        await ws.send_json({"type": "bogus"})
        msg = await ws.receive_json()
        assert msg["type"] == "error"
        assert "Unknown type" in msg["message"]


@pytest.mark.asyncio
async def test_websocket_invalid_json(client):
    async with client.ws_connect("/ws") as ws:
        await ws.receive_json()  # initial status
        await ws.send_str("not json{{{")
        msg = await ws.receive_json()
        assert msg["type"] == "error"
        assert "Invalid JSON" in msg["message"]
