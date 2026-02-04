import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from freegie.config import ChargeConfig
from freegie.engine import ChargeEngine, Phase
from freegie.ble import ConnectionState

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
def ble():
    m = MagicMock()
    m.send_command = AsyncMock(side_effect=_mock_send_command)
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
    m.read_status = MagicMock(return_value="Charging")
    m.available = True
    return m


@pytest.fixture
def config():
    return ChargeConfig(charge_max=80, charge_min=75, pd_mode=2)


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
    assert status["charge_max"] == 80
    assert status["charge_min"] == 75
    assert status["is_charging"] is False
    assert status["telemetry"] is None
    assert status["device"] is None


def test_update_config(engine):
    engine.update_config(charge_max=90, pd_mode=1)
    assert engine.charge_config.charge_max == 90
    assert engine.charge_config.pd_mode == 1
    assert engine.charge_config.charge_min == 75


def test_update_config_charge_min(engine):
    engine.update_config(charge_min=70)
    assert engine.charge_config.charge_min == 70
    assert engine.charge_config.charge_max == 80


def test_update_config_rejects_invalid(engine):
    with pytest.raises(ValueError):
        engine.update_config(charge_max=999)
    assert engine.charge_config.charge_max == 80


def test_update_config_rejects_min_above_max(engine):
    with pytest.raises(ValueError):
        engine.update_config(charge_min=85)
    assert engine.charge_config.charge_min == 75


@patch("freegie.engine.save_state")
def test_update_config_persists_charge_limits(mock_save, engine):
    engine.update_config(charge_max=90)
    mock_save.assert_called_once_with(90, 75, 30)

    mock_save.reset_mock()
    engine.update_config(charge_min=60)
    mock_save.assert_called_once_with(90, 60, 30)


@patch("freegie.engine.save_state")
def test_update_config_persists_telemetry_interval(mock_save, engine):
    engine.update_config(telemetry_interval=10)
    mock_save.assert_called_once_with(80, 75, 10)


@patch("freegie.engine.save_state")
def test_update_config_skips_save_when_unchanged(mock_save, engine):
    engine.update_config(pd_mode=2)
    mock_save.assert_not_called()


# --- Enforce limit: no-op cases (pure state machine logic, no BLE) ---


@pytest.mark.asyncio
async def test_enforce_limit_stays_paused_above_min(engine, ble):
    engine._phase = Phase.PAUSED
    engine._charging = False

    await engine._enforce_limit(77)

    ble.send_command.assert_not_awaited()
    assert engine.phase == Phase.PAUSED


@pytest.mark.asyncio
async def test_enforce_limit_no_action_below_max(engine, ble):
    engine._phase = Phase.CONTROLLING
    engine._charging = True

    await engine._enforce_limit(70)

    ble.send_command.assert_not_awaited()
    assert engine.phase == Phase.CONTROLLING


@pytest.mark.asyncio
async def test_enforce_limit_skipped_during_override(engine, ble):
    engine._phase = Phase.CONTROLLING
    engine._charging = True
    engine._override = "on"

    ble.send_command = AsyncMock()

    await engine._enforce_limit(99)

    ble.send_command.assert_not_awaited()
    assert engine.phase == Phase.CONTROLLING


# --- Error handling (tests our code's response to bad data, not device behavior) ---


@pytest.mark.asyncio
async def test_power_on_relay_verifies_response(engine, ble):
    ble.send_command = AsyncMock(side_effect=[
        "OK+PIO2:0",  # PIO20 off — OK
        "OK+PIO2:0",  # PIO21 on — but reports OFF (bad!)
    ])
    with pytest.raises(ConnectionError, match="CMD_POWER_ON but device reports OFF"):
        await engine._power_on()


@pytest.mark.asyncio
async def test_power_off_verifies_response(engine, ble):
    ble.send_command = AsyncMock(return_value="OK+PIO2:1")
    with pytest.raises(ConnectionError, match="CMD_POWER_OFF but device reports ON"):
        await engine._power_off()


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


# --- Disconnect / reconnect (state machine logic) ---


@pytest.mark.asyncio
async def test_ble_disconnect_stops_polling(engine):
    engine._phase = Phase.CONTROLLING
    mock_sysfs = MagicMock()
    mock_telem = MagicMock()
    engine._sysfs_task = mock_sysfs
    engine._telemetry_task = mock_telem

    engine._handle_ble_state(ConnectionState.DISCONNECTED)

    mock_sysfs.cancel.assert_called_once()
    mock_telem.cancel.assert_called_once()
    assert engine._sysfs_task is None
    assert engine._telemetry_task is None
    assert engine.phase == Phase.RECONNECTING
    assert engine._charging is False
    engine._stop_reconnect()


def test_ble_disconnect_no_op_when_idle(engine):
    engine._phase = Phase.IDLE

    engine._handle_ble_state(ConnectionState.DISCONNECTED)

    assert engine.phase == Phase.IDLE  # stays idle, not DISCONNECTED


@pytest.mark.asyncio
async def test_ble_disconnect_resets_charging(engine):
    engine._phase = Phase.CONTROLLING
    engine._charging = True

    engine._handle_ble_state(ConnectionState.DISCONNECTED)

    assert engine._charging is False
    engine._stop_reconnect()


@pytest.mark.asyncio
async def test_auto_reconnect_starts_on_disconnect(engine):
    engine._phase = Phase.CONTROLLING

    engine._handle_ble_state(ConnectionState.DISCONNECTED)

    assert engine.phase == Phase.RECONNECTING
    assert engine._reconnect_task is not None
    engine._stop_reconnect()


def test_auto_reconnect_disabled(engine, config):
    config.auto_reconnect = False
    engine._config = config
    engine._phase = Phase.CONTROLLING

    engine._handle_ble_state(ConnectionState.DISCONNECTED)

    assert engine.phase == Phase.DISCONNECTED
    assert engine._reconnect_task is None


@pytest.mark.asyncio
async def test_stop_cancels_reconnect(engine):
    engine._phase = Phase.RECONNECTING
    engine._reconnect_task = MagicMock()

    await engine.stop()

    engine._reconnect_task is None
    assert engine.phase == Phase.IDLE
    assert engine._charging is False


@pytest.mark.asyncio
async def test_stop_resets_charging(engine):
    engine._charging = True
    await engine.stop()
    assert engine._charging is False


@pytest.mark.asyncio
async def test_stop_prevents_reconnect_on_ble_callback(engine):
    engine._phase = Phase.CONTROLLING
    engine._config.auto_reconnect = True

    await engine.stop()
    assert engine.phase == Phase.IDLE

    engine._handle_ble_state(ConnectionState.DISCONNECTED)

    assert engine.phase == Phase.IDLE
    assert engine._reconnect_task is None


@pytest.mark.asyncio
async def test_manual_scan_cancels_reconnect(engine):
    engine._phase = Phase.RECONNECTING
    mock_task = MagicMock()
    engine._reconnect_task = mock_task

    await engine.start()

    mock_task.cancel.assert_called_once()
    assert engine._reconnect_task is None


def test_status_includes_reconnect_fields(engine):
    engine._phase = Phase.RECONNECTING
    engine._reconnect_attempt = 3
    engine._reconnect_delay = 20

    status = engine.status()

    assert status["phase"] == "reconnecting"
    assert status["reconnect_attempt"] == 3
    assert status["reconnect_delay"] == 20


def test_status_includes_telemetry_interval(engine):
    status = engine.status()
    assert status["telemetry_interval"] == 30


@pytest.mark.asyncio
async def test_confirm_sysfs_charging_success(engine, battery):
    battery.read_status = MagicMock(return_value="Charging")
    await engine._confirm_sysfs_charging(True, timeout=2.0)


@pytest.mark.asyncio
async def test_confirm_sysfs_charging_timeout(engine, battery, caplog):
    battery.read_status = MagicMock(return_value="Discharging")
    with caplog.at_level(logging.WARNING):
        await engine._confirm_sysfs_charging(True, timeout=1.0)
    assert "sysfs status did not confirm" in caplog.text


# --- Override: validation and state (no BLE) ---


@pytest.mark.asyncio
async def test_set_override_auto_clears(engine, ble):
    engine._phase = Phase.CONTROLLING
    engine._charging = True
    engine._override = "on"

    await engine.set_override("auto")

    assert engine.override is None


@pytest.mark.asyncio
async def test_override_rejected_when_idle(engine):
    assert engine.phase == Phase.IDLE

    with pytest.raises(ValueError, match="not connected"):
        await engine.set_override("on")


@pytest.mark.asyncio
async def test_override_invalid_mode(engine):
    with pytest.raises(ValueError, match="Override mode must be"):
        await engine.set_override("bogus")


@pytest.mark.asyncio
async def test_override_cleared_on_disconnect(engine):
    engine._phase = Phase.CONTROLLING
    engine._override = "on"

    engine._handle_ble_state(ConnectionState.DISCONNECTED)

    assert engine.override is None
    engine._stop_reconnect()


@pytest.mark.asyncio
async def test_override_cleared_on_stop(engine):
    engine._override = "on"

    await engine.stop()

    assert engine.override is None


def test_status_includes_override(engine):
    status = engine.status()
    assert "override" in status
    assert status["override"] is None


# --- Manual telemetry poll: validation only ---


@pytest.mark.asyncio
async def test_confirm_pd_rejects_base_usb_voltage(engine, ble):
    ble.send_command = AsyncMock(return_value="OK+STAT:0.00/5.00")
    result = await engine._confirm_pd_active(timeout=2.0)
    assert result is False


@pytest.mark.asyncio
async def test_poll_telemetry_rejects_when_idle(engine):
    with pytest.raises(ConnectionError, match="not connected"):
        await engine.poll_telemetry()
