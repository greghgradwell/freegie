import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp.test_utils import TestClient, TestServer

from freegie.config import ChargeConfig
from freegie.engine import ChargeEngine, Phase
from freegie.server import create_app

@pytest.fixture(autouse=True)
def _mock_save_state():
    with patch("freegie.engine.save_state"):
        yield

log = logging.getLogger(__name__)


def _mock_send_command(cmd):
    responses = {
        "AT+PIO20": "OK+PIO2:0",
        "AT+PIO21": "OK+PIO2:1",
        "AT+PDMO1": "OK+PDMO:1",
        "AT+PDMO2": "OK+PDMO:2",
        "AT+STAT?": "OK+STAT:2.00/15.00",
        "AT+ISPD?": "OK+ISPD:1",
        "AT+CAPA?": "OK+CAPA:7",
        "AT+FWVR?": "OK+FWVR:1.0",
        "AT+HWVR?": "OK+HWVR:2.0",
    }
    return responses.get(cmd, "OK")


@pytest.fixture
def engine():
    ble = MagicMock()
    ble.send_command = AsyncMock(side_effect=_mock_send_command)
    ble.scan = AsyncMock(return_value=MagicMock())
    ble.connect = AsyncMock(return_value=True)
    ble.disconnect = AsyncMock()
    ble.on_state_change = MagicMock()
    ble.device_name = "Chargie Laptops"

    battery = MagicMock()
    battery.read_percent = MagicMock(return_value=72)
    battery.read_status = MagicMock(return_value="Charging")
    battery.available = True

    config = ChargeConfig(charge_max=80, charge_min=75, pd_mode=2)
    return ChargeEngine(ble, battery, config)


@pytest.fixture
async def client(engine):
    app = create_app(engine, static_dir=MagicMock(is_dir=MagicMock(return_value=False)))
    async with TestClient(TestServer(app)) as c:
        yield c


@pytest.fixture
async def client_with_stop_event(engine):
    stop_event = asyncio.Event()
    app = create_app(engine, stop_event=stop_event, static_dir=MagicMock(is_dir=MagicMock(return_value=False)))
    async with TestClient(TestServer(app)) as c:
        yield c, stop_event


@pytest.mark.asyncio
async def test_status_returns_json(client):
    resp = await client.get("/api/status")
    assert resp.status == 200
    data = await resp.json()
    assert data["phase"] == "idle"
    assert data["battery_percent"] == 72
    assert data["charge_max"] == 80
    assert data["charge_min"] == 75


@pytest.mark.asyncio
async def test_get_settings(client):
    resp = await client.get("/api/settings")
    assert resp.status == 200
    data = await resp.json()
    assert data["charge_max"] == 80
    assert data["charge_min"] == 75
    assert data["pd_mode"] == 2
    assert data["telemetry_interval"] == 30


@pytest.mark.asyncio
async def test_put_settings_valid(client):
    resp = await client.put("/api/settings", json={"charge_max": 90})
    assert resp.status == 200
    data = await resp.json()
    assert data["ok"] is True

    resp = await client.get("/api/settings")
    data = await resp.json()
    assert data["charge_max"] == 90


@pytest.mark.asyncio
async def test_put_settings_charge_min(client):
    resp = await client.put("/api/settings", json={"charge_min": 70})
    assert resp.status == 200
    data = await resp.json()
    assert data["ok"] is True

    resp = await client.get("/api/settings")
    data = await resp.json()
    assert data["charge_min"] == 70


@pytest.mark.asyncio
async def test_put_settings_invalid_max(client):
    resp = await client.put("/api/settings", json={"charge_max": 999})
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
async def test_websocket_set_max_valid(client):
    async with client.ws_connect("/ws") as ws:
        await ws.receive_json()  # initial status
        await ws.send_json({"type": "set_max", "value": 90})
        msg = await ws.receive_json()
        assert msg["type"] == "status_update"
        assert msg["data"]["charge_max"] == 90


@pytest.mark.asyncio
async def test_websocket_set_max_invalid(client):
    async with client.ws_connect("/ws") as ws:
        await ws.receive_json()  # initial status
        await ws.send_json({"type": "set_max", "value": 999})
        msg = await ws.receive_json()
        assert msg["type"] == "error"


@pytest.mark.asyncio
async def test_websocket_set_min_valid(client):
    async with client.ws_connect("/ws") as ws:
        await ws.receive_json()  # initial status
        await ws.send_json({"type": "set_min", "value": 70})
        msg = await ws.receive_json()
        assert msg["type"] == "status_update"
        assert msg["data"]["charge_min"] == 70


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


# --- Override tests ---


@pytest.mark.asyncio
async def test_override_on_via_api(client, engine):
    engine._phase = Phase.CONTROLLING
    engine._charging = True

    resp = await client.post("/api/override", json={"mode": "on"})
    assert resp.status == 200
    data = await resp.json()
    assert data["ok"] is True


@pytest.mark.asyncio
async def test_override_invalid_mode_api(client):
    resp = await client.post("/api/override", json={"mode": "bogus"})
    assert resp.status == 400
    data = await resp.json()
    assert "error" in data


@pytest.mark.asyncio
async def test_override_not_connected_api(client):
    resp = await client.post("/api/override", json={"mode": "on"})
    assert resp.status == 400
    data = await resp.json()
    assert "not connected" in data["error"]


@pytest.mark.asyncio
async def test_websocket_override(client, engine):
    engine._phase = Phase.CONTROLLING
    engine._charging = True

    async with client.ws_connect("/ws") as ws:
        await ws.receive_json()  # initial status
        await ws.send_json({"type": "override", "value": "on"})
        msg = await ws.receive_json()
        assert msg["type"] == "status_update"
        assert msg["data"]["override"] == "on"


# --- Poll tests ---


@pytest.mark.asyncio
async def test_poll_returns_ok_when_connected(client, engine):
    engine._phase = Phase.CONTROLLING
    engine._ble.send_command = AsyncMock(return_value="OK+STAT:4.24/15.00")

    resp = await client.post("/api/poll")
    assert resp.status == 200
    data = await resp.json()
    assert data["ok"] is True
    assert data["data"]["telemetry"]["volts"] == pytest.approx(15.00)


@pytest.mark.asyncio
async def test_poll_rejects_when_idle(client):
    resp = await client.post("/api/poll")
    assert resp.status == 400
    data = await resp.json()
    assert "not connected" in data["error"]


# --- Shutdown tests ---


@pytest.mark.asyncio
async def test_shutdown_returns_ok(client):
    resp = await client.post("/api/shutdown")
    assert resp.status == 200
    data = await resp.json()
    assert data["ok"] is True


@pytest.mark.asyncio
async def test_shutdown_sets_stop_event(client_with_stop_event):
    client, stop_event = client_with_stop_event
    assert not stop_event.is_set()
    resp = await client.post("/api/shutdown")
    assert resp.status == 200
    await asyncio.sleep(0.1)
    assert stop_event.is_set()
