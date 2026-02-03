"""Charge engine — state machine and control logic for Chargie devices."""

import asyncio
import logging
from enum import Enum, auto

from freegie.battery import BatteryReader
from freegie.ble import BLEManager, ConnectionState
from freegie.config import ChargeConfig
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


class ChargeEngine:
    def __init__(self, ble: BLEManager, battery: BatteryReader, charge_config: ChargeConfig):
        self._ble = ble
        self._battery = battery
        self._config = charge_config

        self._phase = Phase.IDLE
        self._telemetry: Telemetry | None = None
        self._device_info: DeviceInfo | None = None
        self._charging: bool = False
        self._poll_task: asyncio.Task | None = None
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
    def charge_config(self) -> ChargeConfig:
        return self._config

    def on_update(self, callback):
        self._on_update.append(callback)

    def update_config(self, limit: int | None = None, allowed_drop: int | None = None, pd_mode: int | None = None):
        self._config = ChargeConfig(
            limit=limit if limit is not None else self._config.limit,
            allowed_drop=allowed_drop if allowed_drop is not None else self._config.allowed_drop,
            pd_mode=pd_mode if pd_mode is not None else self._config.pd_mode,
            poll_interval=self._config.poll_interval,
        )
        log.info("Config updated: limit=%d, drop=%d, pd=%d",
                 self._config.limit, self._config.allowed_drop, self._config.pd_mode)
        self._notify()

    async def start(self):
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
        await self._configure_pd_mode()

        self._set_phase(Phase.CONTROLLING)
        self._start_polling()

    async def stop(self):
        self._stop_polling()
        await self._ble.disconnect()
        self._set_phase(Phase.IDLE)

    async def _verify_device(self) -> bool:
        try:
            resp = await self._ble.send_command(CMD_POWER_OFF)
            power_on = parse_power_state(resp)
            if power_on:
                log.error("PIO2 should be OFF after AT+PIO20, got ON")
                return False

            await asyncio.sleep(1.0)

            resp = await self._ble.send_command(CMD_POWER_ON)
            power_on = parse_power_state(resp)
            if not power_on:
                log.error("PIO2 should be ON after AT+PIO21, got OFF")
                return False

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

    async def _configure_pd_mode(self):
        try:
            await self._ble.send_command(CMD_ISPD)
        except (TimeoutError, ConnectionError) as e:
            log.debug("ISPD query failed: %s", e)

        cmd = CMD_PD_MODE_2 if self._config.pd_mode == 2 else CMD_PD_MODE_1
        try:
            await self._ble.send_command(cmd)
            await self._ble.send_command(CMD_POWER_ON)
            log.info("PD mode set to %d", self._config.pd_mode)
        except (TimeoutError, ConnectionError) as e:
            log.warning("Failed to set PD mode: %s", e)

    def _start_polling(self):
        if self._poll_task is not None:
            return
        self._poll_task = asyncio.create_task(self._poll_loop())

    def _stop_polling(self):
        if self._poll_task is not None:
            self._poll_task.cancel()
            self._poll_task = None

    async def _poll_loop(self):
        log.info("Telemetry polling started (every %ds)", self._config.poll_interval)
        try:
            while True:
                await self._poll_once()
                await asyncio.sleep(self._config.poll_interval)
        except asyncio.CancelledError:
            log.info("Polling stopped")
        except ConnectionError:
            log.warning("Lost connection during polling")
            self._set_phase(Phase.DISCONNECTED)

    async def _poll_once(self):
        try:
            stat_raw = await self._ble.send_command(CMD_STAT)
            self._telemetry = parse_telemetry(stat_raw)
        except TimeoutError:
            log.debug("STAT timeout (non-fatal)")

        percent = self._battery.read_percent()
        if percent is None:
            return

        await self._enforce_limit(percent)
        self._notify()

    async def _enforce_limit(self, percent: int):
        limit = self._config.limit
        drop = self._config.allowed_drop

        if self._phase == Phase.CONTROLLING and percent >= limit:
            log.info("Battery at %d%% >= limit %d%%, cutting power", percent, limit)
            await self._ble.send_command(CMD_POWER_OFF)
            self._charging = False
            self._set_phase(Phase.PAUSED)

        elif self._phase == Phase.PAUSED and percent <= (limit - drop):
            log.info("Battery at %d%% <= %d%% (limit-drop), restoring power", percent, limit - drop)
            await self._ble.send_command(CMD_POWER_ON)
            self._charging = True
            self._set_phase(Phase.CONTROLLING)

    def _set_phase(self, new_phase: Phase):
        old = self._phase
        self._phase = new_phase
        if old != new_phase:
            log.info("Engine phase: %s -> %s", old.name, new_phase.name)
            self._notify()

    def _handle_ble_state(self, ble_state: ConnectionState):
        if ble_state == ConnectionState.DISCONNECTED:
            self._stop_polling()
            if self._phase not in (Phase.IDLE, Phase.DISCONNECTED):
                self._set_phase(Phase.DISCONNECTED)

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

        return {
            "phase": self._phase.name.lower(),
            "battery_percent": self._battery.read_percent(),
            "is_charging": self._charging,
            "charge_limit": self._config.limit,
            "allowed_drop": self._config.allowed_drop,
            "telemetry": telemetry,
            "device": device,
        }
