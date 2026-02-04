"""Charge engine — state machine and control logic for Chargie devices."""

import asyncio
import logging
from enum import Enum, auto

from freegie.battery import BatteryReader
from freegie.ble import BLEManager, ConnectionState
from freegie.config import ChargeConfig, save_state
from freegie.protocol import (
    CMD_CAPA,
    CMD_FWVR,
    CMD_HWVR,
    CMD_ISPD,
    CMD_PD_MODE_1,
    CMD_PD_MODE_2,
    CMD_POWER_OFF,
    CMD_POWER_ON,
    CMD_STAT,
    DeviceInfo,
    Telemetry,
    parse_capabilities,
    parse_firmware,
    parse_hardware,
    parse_power_state,
    parse_telemetry,
)

log = logging.getLogger(__name__)


class Phase(Enum):
    IDLE = auto()
    SCANNING = auto()
    CONNECTING = auto()
    VERIFYING = auto()
    CONTROLLING = auto()
    PAUSED = auto()       # Charge limit reached, power cut
    DISCONNECTED = auto()
    RECONNECTING = auto()


class ChargeEngine:
    def __init__(self, ble: BLEManager, battery: BatteryReader, charge_config: ChargeConfig):
        self._ble = ble
        self._battery = battery
        self._config = charge_config

        self._phase = Phase.IDLE
        self._telemetry: Telemetry | None = None
        self._device_info: DeviceInfo | None = None
        self._charging: bool = False
        self._override: str | None = None  # None = auto, "on", "off"
        self._sysfs_task: asyncio.Task | None = None
        self._telemetry_task: asyncio.Task | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._reconnect_attempt: int = 0
        self._reconnect_delay: int = 0
        self._stopped: bool = False
        self._on_update: list = []

        self._ble.on_state_change(self._handle_ble_state)

    @property
    def phase(self) -> Phase:
        return self._phase

    @property
    def telemetry(self) -> Telemetry | None:
        return self._telemetry

    @property
    def device_info(self) -> DeviceInfo | None:
        return self._device_info

    @property
    def is_charging(self) -> bool:
        return self._charging

    @property
    def battery_percent(self) -> int | None:
        return self._battery.read_percent()

    @property
    def override(self) -> str | None:
        return self._override

    @property
    def charge_config(self) -> ChargeConfig:
        return self._config

    def on_update(self, callback):
        self._on_update.append(callback)

    def update_config(
        self,
        charge_max: int | None = None,
        charge_min: int | None = None,
        pd_mode: int | None = None,
        telemetry_interval: int | None = None,
    ):
        old_max = self._config.charge_max
        old_min = self._config.charge_min
        old_telemetry = self._config.telemetry_interval
        self._config = ChargeConfig(
            charge_max=charge_max if charge_max is not None else self._config.charge_max,
            charge_min=charge_min if charge_min is not None else self._config.charge_min,
            pd_mode=pd_mode if pd_mode is not None else self._config.pd_mode,
            poll_interval=self._config.poll_interval,
            telemetry_interval=telemetry_interval if telemetry_interval is not None else self._config.telemetry_interval,
        )
        log.info("Config updated: max=%d, min=%d, pd=%d, telemetry_interval=%d",
                 self._config.charge_max, self._config.charge_min,
                 self._config.pd_mode, self._config.telemetry_interval)
        changed = (self._config.charge_max != old_max
                   or self._config.charge_min != old_min
                   or self._config.telemetry_interval != old_telemetry)
        if changed:
            save_state(self._config.charge_max, self._config.charge_min,
                       self._config.telemetry_interval)
        self._notify()

    async def set_override(self, mode: str):
        if mode not in ("on", "off", "auto"):
            raise ValueError(f"Override mode must be 'on', 'off', or 'auto', got {mode!r}")
        if mode == "auto":
            self._override = None
            log.info("Override cleared, returning to auto control")
            percent = self._battery.read_percent()
            if percent is not None:
                await self._enforce_limit(percent)
        else:
            if self._phase not in (Phase.CONTROLLING, Phase.PAUSED):
                raise ValueError("Cannot override: not connected to device")
            if mode == "on":
                self._override = "on"
                log.info("Override: forcing charge ON")
                await self._power_on()
                self._set_phase(Phase.CONTROLLING)
            else:
                self._override = "off"
                log.info("Override: forcing charge OFF")
                await self._power_off()
                self._set_phase(Phase.PAUSED)
        self._notify()

    # --- Power helpers ---

    async def _power_on(self):
        for attempt in range(3):
            # Clean slate: power off first
            resp = await self._ble.send_command(CMD_POWER_OFF)
            if parse_power_state(resp):
                raise ConnectionError("CMD_POWER_OFF but device reports ON")
            self._charging = False
            await asyncio.sleep(1.0)

            # Relay on
            resp = await self._ble.send_command(CMD_POWER_ON)
            if not parse_power_state(resp):
                raise ConnectionError("CMD_POWER_ON but device reports OFF")
            self._charging = True
            await asyncio.sleep(2.0)

            # Configure PD
            try:
                await self._ble.send_command(CMD_ISPD)
            except (TimeoutError, ConnectionError) as e:
                log.debug("ISPD query failed: %s", e)
            cmd = CMD_PD_MODE_2 if self._config.pd_mode == 2 else CMD_PD_MODE_1
            await self._ble.send_command(cmd)
            resp = await self._ble.send_command(CMD_POWER_ON)
            if not parse_power_state(resp):
                raise ConnectionError("CMD_POWER_ON but device reports OFF")

            # Confirm PD is active
            if await self._confirm_pd_active():
                log.info("Power on with PD mode %d (attempt %d)",
                         self._config.pd_mode, attempt + 1)
                return
            log.warning("PD negotiation attempt %d failed, retrying", attempt + 1)
        raise ConnectionError("PD negotiation failed after 3 attempts")

    async def _confirm_pd_active(self, timeout: float = 10.0) -> bool:
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            try:
                stat_raw = await self._ble.send_command(CMD_STAT)
                telemetry = parse_telemetry(stat_raw)
                if telemetry.volts > 0:
                    self._telemetry = telemetry
                    return True
            except (TimeoutError, ConnectionError) as e:
                log.debug("STAT poll during PD confirm failed: %s", e)
            await asyncio.sleep(1.0)
        return False

    async def _power_off(self):
        resp = await self._ble.send_command(CMD_POWER_OFF)
        if parse_power_state(resp):
            raise ConnectionError("CMD_POWER_OFF but device reports ON")
        self._charging = False

    async def _confirm_sysfs_charging(self, expected_charging: bool, timeout: float = 10.0):
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            status = self._battery.read_status()
            is_charging = status == "Charging"
            if is_charging == expected_charging:
                return
            await asyncio.sleep(1.0)
        log.warning("sysfs status did not confirm charging=%s within %.0fs", expected_charging, timeout)

    # --- Lifecycle ---

    async def start(self):
        self._stopped = False
        self._stop_reconnect()
        self._set_phase(Phase.SCANNING)
        device = await self._ble.scan()
        if device is None:
            self._set_phase(Phase.IDLE)
            return

        self._set_phase(Phase.CONNECTING)
        connected = await self._ble.connect(device)
        if not connected:
            self._set_phase(Phase.IDLE)
            return

        self._set_phase(Phase.VERIFYING)
        verified = await self._verify_device()
        if not verified:
            log.error("Device verification failed")
            await self._ble.disconnect()
            self._set_phase(Phase.IDLE)
            return

        await self._query_device_info()

        try:
            await self._power_on()
        except (TimeoutError, ConnectionError) as e:
            log.error("PD mode configuration failed: %s", e)
            await self._ble.disconnect()
            self._set_phase(Phase.IDLE)
            return

        self._set_phase(Phase.CONTROLLING)
        self._start_polling()

    async def stop(self):
        self._stopped = True
        self._stop_polling()
        self._stop_reconnect()
        self._charging = False
        self._override = None
        self._set_phase(Phase.IDLE)
        await self._ble.disconnect()

    async def _verify_device(self) -> bool:
        try:
            resp = await self._ble.send_command(CMD_POWER_OFF)
            if parse_power_state(resp):
                raise ConnectionError("CMD_POWER_OFF but device reports ON")
            await asyncio.sleep(1.0)
            resp = await self._ble.send_command(CMD_POWER_ON)
            if not parse_power_state(resp):
                raise ConnectionError("CMD_POWER_ON but device reports OFF")
            log.info("Device verification passed")
            return True
        except (TimeoutError, ConnectionError) as e:
            log.error("Verification failed: %s", e)
            return False

    async def _query_device_info(self):
        try:
            capa_raw = await self._ble.send_command(CMD_CAPA)
            fw_raw = await self._ble.send_command(CMD_FWVR)
            hw_raw = await self._ble.send_command(CMD_HWVR)

            self._device_info = DeviceInfo(
                firmware=parse_firmware(fw_raw),
                hardware=parse_hardware(hw_raw),
                capabilities=parse_capabilities(capa_raw),
            )
            log.info("Device: FW=%s HW=%s PD=%s",
                     self._device_info.firmware,
                     self._device_info.hardware,
                     self._device_info.capabilities.pd)
        except (TimeoutError, ConnectionError) as e:
            log.warning("Failed to query device info: %s", e)

    # --- Reconnection ---

    def _start_reconnect(self):
        if self._reconnect_task is not None:
            return
        self._reconnect_attempt = 0
        self._reconnect_delay = 0
        self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    def _stop_reconnect(self):
        if self._reconnect_task is not None:
            self._reconnect_task.cancel()
            self._reconnect_task = None
            self._reconnect_attempt = 0
            self._reconnect_delay = 0

    async def _reconnect_loop(self):
        delays = [5, 10, 20, 40, 60]
        log.info("Auto-reconnect started")
        try:
            while True:
                delay = delays[min(self._reconnect_attempt, len(delays) - 1)]
                self._reconnect_attempt += 1
                self._reconnect_delay = delay
                self._notify()
                log.info("Reconnect attempt %d in %ds", self._reconnect_attempt, delay)

                await asyncio.sleep(delay)

                device = await self._ble.scan()
                if device is None:
                    continue

                connected = await self._ble.connect(device)
                if not connected:
                    continue

                verified = await self._verify_device()
                if not verified:
                    await self._ble.disconnect()
                    continue

                await self._query_device_info()

                try:
                    await self._power_on()
                except (TimeoutError, ConnectionError) as e:
                    log.warning("PD mode failed on reconnect: %s", e)
                    await self._ble.disconnect()
                    continue

                self._set_phase(Phase.CONTROLLING)
                self._start_polling()
                log.info("Reconnected successfully on attempt %d", self._reconnect_attempt)
                self._reconnect_task = None
                self._reconnect_attempt = 0
                self._reconnect_delay = 0
                return
        except asyncio.CancelledError:
            log.info("Reconnect cancelled")

    # --- Polling ---

    def _start_polling(self):
        if self._sysfs_task is None:
            self._sysfs_task = asyncio.create_task(self._sysfs_loop())
        if self._telemetry_task is None:
            self._telemetry_task = asyncio.create_task(self._telemetry_loop())

    def _stop_polling(self):
        if self._sysfs_task is not None:
            self._sysfs_task.cancel()
            self._sysfs_task = None
        if self._telemetry_task is not None:
            self._telemetry_task.cancel()
            self._telemetry_task = None

    async def _sysfs_loop(self):
        log.info("sysfs polling started (every %ds)", self._config.poll_interval)
        try:
            while True:
                percent = self._battery.read_percent()
                if percent is not None:
                    await self._enforce_limit(percent)
                self._notify()
                await asyncio.sleep(self._config.poll_interval)
        except asyncio.CancelledError:
            log.info("sysfs polling stopped")

    async def _telemetry_loop(self):
        log.info("BLE telemetry polling started (every %ds)", self._config.telemetry_interval)
        try:
            while True:
                try:
                    stat_raw = await self._ble.send_command(CMD_STAT)
                    self._telemetry = parse_telemetry(stat_raw)
                except TimeoutError:
                    log.debug("STAT timeout (non-fatal)")
                self._notify()
                await asyncio.sleep(self._config.telemetry_interval)
        except asyncio.CancelledError:
            log.info("BLE telemetry polling stopped")
        except ConnectionError:
            log.warning("Lost connection during telemetry polling")
            self._set_phase(Phase.DISCONNECTED)

    async def poll_telemetry(self):
        if self._phase not in (Phase.CONTROLLING, Phase.PAUSED):
            raise ConnectionError("Cannot poll: not connected to device")
        stat_raw = await self._ble.send_command(CMD_STAT)
        self._telemetry = parse_telemetry(stat_raw)
        self._notify()

    # --- Charge control ---

    async def _enforce_limit(self, percent: int):
        if self._override is not None:
            return
        if self._phase == Phase.CONTROLLING and percent >= self._config.charge_max:
            log.info("Battery at %d%% >= max %d%%, cutting power", percent, self._config.charge_max)
            await self._power_off()
            self._set_phase(Phase.PAUSED)
            asyncio.create_task(self._confirm_sysfs_charging(False))

        elif self._phase == Phase.PAUSED and percent <= self._config.charge_min:
            log.info("Battery at %d%% <= min %d%%, restoring power", percent, self._config.charge_min)
            await self._power_on()
            self._set_phase(Phase.CONTROLLING)
            asyncio.create_task(self._confirm_sysfs_charging(True))

        elif self._phase == Phase.CONTROLLING and not self._charging:
            log.info("CONTROLLING but not charging — safety net, restoring power")
            await self._power_on()
            asyncio.create_task(self._confirm_sysfs_charging(True))

    # --- State management ---

    def _set_phase(self, new_phase: Phase):
        old = self._phase
        self._phase = new_phase
        if old != new_phase:
            log.info("Engine phase: %s -> %s", old.name, new_phase.name)
            self._notify()

    def _handle_ble_state(self, ble_state: ConnectionState):
        if self._stopped:
            return
        if ble_state == ConnectionState.DISCONNECTED:
            self._stop_polling()
            self._charging = False
            self._override = None
            if self._phase not in (Phase.IDLE, Phase.DISCONNECTED, Phase.RECONNECTING):
                self._set_phase(Phase.DISCONNECTED)
                if self._config.auto_reconnect:
                    self._set_phase(Phase.RECONNECTING)
                    self._start_reconnect()

    def _notify(self):
        for cb in self._on_update:
            cb()

    def status(self) -> dict:
        telemetry = None
        if self._telemetry:
            telemetry = {
                "volts": self._telemetry.volts,
                "amps": self._telemetry.amps,
                "watts": self._telemetry.watts,
            }

        device = None
        if self._device_info:
            device = {
                "name": self._ble.device_name,
                "firmware": self._device_info.firmware,
                "hardware": self._device_info.hardware,
                "capabilities": {
                    "pd": self._device_info.capabilities.pd,
                    "fet2": self._device_info.capabilities.fet2,
                    "auto": self._device_info.capabilities.auto,
                },
            }

        result = {
            "phase": self._phase.name.lower(),
            "battery_percent": self._battery.read_percent(),
            "is_charging": self._charging,
            "override": self._override,
            "charge_max": self._config.charge_max,
            "charge_min": self._config.charge_min,
            "pd_mode": self._config.pd_mode,
            "telemetry_interval": self._config.telemetry_interval,
            "telemetry": telemetry,
            "device": device,
        }

        if self._phase == Phase.RECONNECTING:
            result["reconnect_attempt"] = self._reconnect_attempt
            result["reconnect_delay"] = self._reconnect_delay

        return result
