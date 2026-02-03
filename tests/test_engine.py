import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from freegie.config import ChargeConfig
from freegie.engine import ChargeEngine, Phase
from freegie.protocol import CMD_POWER_OFF, CMD_POWER_ON

log = logging.getLogger(__name__)


@pytest.fixture
def ble():
    m = MagicMock()
    m.send_command = AsyncMock(return_value="OK+PIO2:1")
    m.scan = AsyncMock(return_value=MagicMock())
    m.connect = AsyncMock(return_value=True)
    m.disconnect = AsyncMock()
    m.on_state_change = MagicMock()
    m.device_name = "Chargie Laptops"
    return m


@pytest.fixture
def battery():
    m = MagicMock()
    m.read_percent = MagicMock(return_value=72)
    m.available = True
    return m


@pytest.fixture
def config():
    return ChargeConfig(limit=80, allowed_drop=5, pd_mode=2)


@pytest.fixture
def engine(ble, battery, config):
    return ChargeEngine(ble, battery, config)


def test_initial_phase(engine):
    assert engine.phase == Phase.IDLE


def test_battery_percent(engine, battery):
    assert engine.battery_percent == 72


def test_status_snapshot(engine):
    status = engine.status()
    assert status["phase"] == "idle"
    assert status["battery_percent"] == 72
    assert status["charge_limit"] == 80
    assert status["is_charging"] is False
    assert status["telemetry"] is None
    assert status["device"] is None


def test_update_config(engine):
    engine.update_config(limit=90, pd_mode=1)
    assert engine.charge_config.limit == 90
    assert engine.charge_config.pd_mode == 1
    assert engine.charge_config.allowed_drop == 5


def test_update_config_rejects_invalid(engine):
    with pytest.raises(ValueError):
        engine.update_config(limit=999)
    assert engine.charge_config.limit == 80



@pytest.mark.asyncio
async def test_enforce_limit_cuts_power_at_limit(engine, ble):
    engine._phase = Phase.CONTROLLING
    engine._charging = True

    # Battery at 80% with limit of 80 -> should cut
    await engine._enforce_limit(80)

    ble.send_command.assert_awaited_with(CMD_POWER_OFF)
    assert engine.phase == Phase.PAUSED
    assert engine.is_charging is False
    log.info("Power cut at limit: phase=%s", engine.phase.name)


@pytest.mark.asyncio
async def test_enforce_limit_restores_power_below_drop(engine, ble):
    engine._phase = Phase.PAUSED
    engine._charging = False

    # Battery at 75% with limit=80, drop=5 -> 75 <= 75 -> restore
    await engine._enforce_limit(75)

    ble.send_command.assert_awaited_with(CMD_POWER_ON)
    assert engine.phase == Phase.CONTROLLING
    assert engine.is_charging is True
    log.info("Power restored below drop: phase=%s", engine.phase.name)


@pytest.mark.asyncio
async def test_enforce_limit_stays_paused_above_drop(engine, ble):
    engine._phase = Phase.PAUSED
    engine._charging = False

    # Battery at 77% with limit=80, drop=5 -> 77 > 75 -> stay paused
    await engine._enforce_limit(77)

    ble.send_command.assert_not_awaited()
    assert engine.phase == Phase.PAUSED


@pytest.mark.asyncio
async def test_enforce_limit_no_action_below_limit(engine, ble):
    engine._phase = Phase.CONTROLLING
    engine._charging = True

    # Battery at 70% with limit=80 -> no action
    await engine._enforce_limit(70)

    ble.send_command.assert_not_awaited()
    assert engine.phase == Phase.CONTROLLING



@pytest.mark.asyncio
async def test_verify_device_success(engine, ble):
    ble.send_command = AsyncMock(side_effect=[
        "OK+PIO2:0",  # AT+PIO20 -> power off confirmed
        "OK+PIO2:1",  # AT+PIO21 -> power on confirmed
    ])
    result = await engine._verify_device()
    assert result is True


@pytest.mark.asyncio
async def test_verify_device_fails_on_stuck_power(engine, ble):
    ble.send_command = AsyncMock(side_effect=[
        "OK+PIO2:1",  # AT+PIO20 -> power still on (bad!)
    ])
    result = await engine._verify_device()
    assert result is False


@pytest.mark.asyncio
async def test_verify_device_fails_on_timeout(engine, ble):
    ble.send_command = AsyncMock(side_effect=TimeoutError("no response"))
    result = await engine._verify_device()
    assert result is False



def test_ble_disconnect_stops_polling(engine):
    engine._phase = Phase.CONTROLLING
    mock_task = MagicMock()
    engine._poll_task = mock_task

    from freegie.ble import ConnectionState
    engine._handle_ble_state(ConnectionState.DISCONNECTED)

    assert engine.phase == Phase.DISCONNECTED
    mock_task.cancel.assert_called_once()
    assert engine._poll_task is None


def test_ble_disconnect_no_op_when_idle(engine):
    engine._phase = Phase.IDLE

    from freegie.ble import ConnectionState
    engine._handle_ble_state(ConnectionState.DISCONNECTED)

    assert engine.phase == Phase.IDLE  # stays idle, not DISCONNECTED
