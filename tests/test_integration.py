import asyncio
import logging

import pytest

from freegie.battery import BatteryReader
from freegie.ble import BLEManager, ConnectionState
from freegie.config import ChargeConfig
from freegie.engine import ChargeEngine, Phase
from freegie.protocol import (
    CMD_PD_MODE_2,
    CMD_POWER_OFF,
    CMD_POWER_ON,
    CMD_STAT,
    parse_power_state,
    parse_telemetry,
)

log = logging.getLogger(__name__)

pytestmark = pytest.mark.chargie

_BLE_SETTLE_SECONDS = 3.0


@pytest.fixture
async def ble():
    manager = BLEManager()
    yield manager
    await manager.disconnect()
    await asyncio.sleep(_BLE_SETTLE_SECONDS)


@pytest.fixture
def battery():
    return BatteryReader()


@pytest.fixture
async def connected_engine(battery):
    ble = BLEManager()
    # Use charge_max=100 so the engine never auto-cuts during setup,
    # regardless of the real battery level. Individual tests control
    # limits manually via _enforce_limit().
    config = ChargeConfig(charge_max=100, charge_min=99, pd_mode=2)
    engine = ChargeEngine(ble, battery, config)
    async with asyncio.timeout(120):
        await engine.start()
    assert engine.phase == Phase.CONTROLLING, "Engine failed to reach CONTROLLING"
    # Wait for PD to settle before yielding to test
    await _poll_until(engine, lambda t: t.amps > 5.0, "initial PD settle")
    assert engine.telemetry.amps > 5.0, "PD did not settle after engine.start()"
    # Stop background polling so tests can drive BLE commands without races
    engine._stop_polling()
    yield engine
    await engine.stop()
    await asyncio.sleep(_BLE_SETTLE_SECONDS)


async def _poll_until(engine, predicate, label, max_attempts=5, interval=3.0):
    for attempt in range(max_attempts):
        await asyncio.sleep(interval)
        async with asyncio.timeout(5):
            await engine.poll_telemetry()
        if predicate(engine.telemetry):
            return
        log.info("Waiting for %s (attempt %d): %.2fV %.2fA",
                 label, attempt + 1, engine.telemetry.volts, engine.telemetry.amps)


# --- BLE-level tests ---


async def test_scan_and_connect(ble):
    async with asyncio.timeout(30):
        device = await ble.scan()
    assert device is not None, "No Chargie device found — is one powered on nearby?"
    log.info("Found device: %s (%s)", device.name, device.address)

    async with asyncio.timeout(10):
        connected = await ble.connect(device)
    assert connected
    assert ble.state == ConnectionState.CONNECTED


async def test_full_lifecycle(ble):
    # --- Scan + connect ---
    async with asyncio.timeout(30):
        device = await ble.scan()
    assert device is not None
    async with asyncio.timeout(10):
        connected = await ble.connect(device)
    assert connected

    # --- Verify device (power toggle handshake) ---
    async with asyncio.timeout(5):
        resp = await ble.send_command(CMD_POWER_OFF)
    assert not parse_power_state(resp), "Power should be OFF after CMD_POWER_OFF"

    await asyncio.sleep(1.0)

    async with asyncio.timeout(5):
        resp = await ble.send_command(CMD_POWER_ON)
    assert parse_power_state(resp), "Power should be ON after CMD_POWER_ON"
    log.info("Device verification passed")

    # --- Query device info ---
    async with asyncio.timeout(5):
        fw_resp = await ble.send_command("AT+FWVR?")
    assert fw_resp.startswith("OK+FWVR:")
    log.info("Firmware: %s", fw_resp)

    async with asyncio.timeout(5):
        hw_resp = await ble.send_command("AT+HWVR?")
    assert hw_resp.startswith("OK+HWVR:")
    log.info("Hardware: %s", hw_resp)

    async with asyncio.timeout(5):
        capa_resp = await ble.send_command("AT+CAPA?")
    assert capa_resp.startswith("OK+CAPA:")
    log.info("Capabilities: %s", capa_resp)

    # --- Configure PD and read telemetry ---
    async with asyncio.timeout(5):
        await ble.send_command(CMD_PD_MODE_2)
    async with asyncio.timeout(5):
        resp = await ble.send_command(CMD_POWER_ON)
    assert parse_power_state(resp)

    await asyncio.sleep(3.0)

    async with asyncio.timeout(5):
        stat_resp = await ble.send_command(CMD_STAT)
    telemetry = parse_telemetry(stat_resp)
    log.info("Telemetry after PD config: %.2fV %.2fA %.2fW",
             telemetry.volts, telemetry.amps, telemetry.watts)
    assert telemetry.amps > 5.0, "PD mode 2 should negotiate higher than 5A"

    # --- Power off: PD limit should drop ---
    async with asyncio.timeout(5):
        await ble.send_command(CMD_POWER_OFF)
    await asyncio.sleep(3.0)
    async with asyncio.timeout(5):
        stat_resp = await ble.send_command(CMD_STAT)
    off_telemetry = parse_telemetry(stat_resp)
    log.info("After power off: %.2fV %.2fA", off_telemetry.volts, off_telemetry.amps)
    assert off_telemetry.amps < telemetry.amps, "PD limit should drop after power off"


# --- Power control: real device behavior ---
# All tests below use connected_engine which stops background polling,
# so only the test drives BLE commands (no concurrent races).


async def test_power_off_cuts_charge(connected_engine):
    engine = connected_engine
    assert engine.is_charging is True

    # Read baseline telemetry (PD mode 2 = 20A)
    async with asyncio.timeout(5):
        await engine.poll_telemetry()
    baseline_amps = engine.telemetry.amps
    log.info("Baseline: %.2fA", baseline_amps)

    async with asyncio.timeout(5):
        await engine._power_off()
    assert engine.is_charging is False

    await asyncio.sleep(3.0)

    async with asyncio.timeout(5):
        await engine.poll_telemetry()
    log.info("After power off: %.2fV %.2fA", engine.telemetry.volts, engine.telemetry.amps)
    assert engine.telemetry.amps < baseline_amps, "PD limit should drop after power off"


async def test_restore_power_delivers_charge(connected_engine):
    engine = connected_engine

    # Read baseline telemetry (PD mode 2 = 20A)
    async with asyncio.timeout(5):
        await engine.poll_telemetry()
    baseline_amps = engine.telemetry.amps
    log.info("Baseline: %.2fA", baseline_amps)

    # Cut power first
    async with asyncio.timeout(5):
        await engine._power_off()
    engine._phase = Phase.PAUSED
    await asyncio.sleep(2.0)

    # Restore power (the sequence under test)
    async with asyncio.timeout(30):
        await engine._restore_power()
    engine._phase = Phase.CONTROLLING

    await _poll_until(engine, lambda t: t.amps >= baseline_amps, "PD renegotiation")

    log.info("After restore: %.2fV %.2fA",
             engine.telemetry.volts, engine.telemetry.amps)
    assert engine.telemetry.amps >= baseline_amps, "PD limit should be restored after _restore_power"


async def test_override_on_delivers_charge(connected_engine):
    engine = connected_engine

    # Cut power first
    async with asyncio.timeout(5):
        await engine._power_off()
    engine._phase = Phase.PAUSED
    await asyncio.sleep(2.0)

    # Override ON
    async with asyncio.timeout(30):
        await engine.set_override("on")
    assert engine.override == "on"
    assert engine.phase == Phase.CONTROLLING

    await _poll_until(engine, lambda t: t.amps > 5.0, "PD renegotiation")

    log.info("After override on: %.2fV %.2fA",
             engine.telemetry.volts, engine.telemetry.amps)
    assert engine.telemetry.amps > 5.0, "PD should be renegotiated after override on"


async def test_override_off_stops_charge(connected_engine):
    engine = connected_engine
    assert engine.is_charging is True

    async with asyncio.timeout(10):
        await engine.set_override("off")
    assert engine.override == "off"
    assert engine.phase == Phase.PAUSED

    await asyncio.sleep(3.0)

    async with asyncio.timeout(5):
        await engine.poll_telemetry()
    log.info("After override off: %.2fV %.2fA", engine.telemetry.volts, engine.telemetry.amps)
    assert engine.telemetry.amps <= 5.0, "PD limit should drop after override off"


async def test_poll_telemetry_returns_real_data(connected_engine):
    engine = connected_engine

    async with asyncio.timeout(5):
        await engine.poll_telemetry()

    assert engine.telemetry is not None
    assert engine.telemetry.volts >= 0
    assert engine.telemetry.amps >= 0
    log.info("Telemetry: %.2fV %.2fA %.2fW",
             engine.telemetry.volts, engine.telemetry.amps, engine.telemetry.watts)


async def test_verify_device_with_real_hardware(battery):
    ble = BLEManager()
    config = ChargeConfig(charge_max=80, charge_min=75, pd_mode=2)
    engine = ChargeEngine(ble, battery, config)

    async with asyncio.timeout(30):
        device = await ble.scan()
    assert device is not None
    async with asyncio.timeout(10):
        connected = await ble.connect(device)
    assert connected

    try:
        async with asyncio.timeout(10):
            result = await engine._verify_device()
        assert result is True, "Device verification should pass on real hardware"
        log.info("Device verification passed")
    finally:
        await ble.disconnect()
        await asyncio.sleep(_BLE_SETTLE_SECONDS)
