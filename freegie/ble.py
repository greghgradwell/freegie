"""BLE manager for Chargie devices using bleak."""

import asyncio
import logging
from enum import Enum, auto

from bleak import BleakClient, BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

from freegie.protocol import SCAN_SERVICE_UUIDS

log = logging.getLogger(__name__)

_CHAR_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"

_COMMAND_SPACING_S = 0.1
_CONNECT_TIMEOUT_S = 15.0
_RESPONSE_TIMEOUT_S = 5.0
_SCAN_TIMEOUT_S = 20.0


class ConnectionState(Enum):
    DISCONNECTED = auto()
    SCANNING = auto()
    CONNECTING = auto()
    CONNECTED = auto()


class BLEManager:
    def __init__(self):
        self._client: BleakClient | None = None
        self._device: BLEDevice | None = None
        self._state = ConnectionState.DISCONNECTED
        self._response_queue: asyncio.Queue[str] = asyncio.Queue()
        self._send_lock = asyncio.Lock()
        self._on_state_change: list = []
        self._on_unsolicited: list = []
        self._write_char: BleakGATTCharacteristic | None = None

    @property
    def state(self) -> ConnectionState:
        return self._state

    @property
    def device_name(self) -> str | None:
        if self._device is None:
            return None
        return self._device.name

    @property
    def device_address(self) -> str | None:
        if self._device is None:
            return None
        return self._device.address

    def on_state_change(self, callback):
        self._on_state_change.append(callback)

    def on_unsolicited(self, callback):
        self._on_unsolicited.append(callback)

    def _set_state(self, new_state: ConnectionState):
        old = self._state
        self._state = new_state
        if old != new_state:
            log.info("BLE state: %s -> %s", old.name, new_state.name)
            for cb in self._on_state_change:
                cb(new_state)

    async def scan(self, timeout: float = _SCAN_TIMEOUT_S) -> BLEDevice | None:
        self._set_state(ConnectionState.SCANNING)
        log.info("Scanning for Chargie devices (timeout=%.0fs)...", timeout)

        device = await BleakScanner.find_device_by_filter(
            filterfunc=self._scan_filter,
            timeout=timeout,
        )

        if device is None:
            log.warning("No Chargie device found")
            self._set_state(ConnectionState.DISCONNECTED)
            return None

        log.info("Found: %s (%s)", device.name, device.address)
        self._device = device
        return device

    @staticmethod
    def _scan_filter(device: BLEDevice, adv: AdvertisementData) -> bool:
        for uuid in SCAN_SERVICE_UUIDS:
            if uuid in adv.service_uuids:
                return True
        return False

    async def connect(self, device: BLEDevice | None = None) -> bool:
        target = device or self._device
        if target is None:
            log.error("No device to connect to — run scan() first")
            return False

        self._device = target
        self._set_state(ConnectionState.CONNECTING)
        log.info("Connecting to %s (%s)...", target.name, target.address)

        client = BleakClient(
            target,
            disconnected_callback=self._on_disconnect,
        )

        try:
            async with asyncio.timeout(_CONNECT_TIMEOUT_S):
                await client.connect()
        except asyncio.TimeoutError:
            log.error("Connection timed out after %.0fs", _CONNECT_TIMEOUT_S)
            try:
                await client.disconnect()
            except Exception:
                pass
            self._set_state(ConnectionState.DISCONNECTED)
            return False

        if not client.is_connected:
            log.error("Connection failed")
            self._set_state(ConnectionState.DISCONNECTED)
            return False

        self._write_char = self._find_write_char(client)
        if self._write_char is None:
            log.error("Write characteristic not found — disconnecting")
            await client.disconnect()
            self._set_state(ConnectionState.DISCONNECTED)
            return False

        notify_char = self._find_notify_char(client)
        if notify_char is None:
            log.error("Notify characteristic not found — disconnecting")
            await client.disconnect()
            self._set_state(ConnectionState.DISCONNECTED)
            return False

        while not self._response_queue.empty():
            self._response_queue.get_nowait()

        await client.start_notify(notify_char, self._on_notification)

        self._client = client
        self._set_state(ConnectionState.CONNECTED)
        log.info("Connected and notifications started")
        return True

    async def disconnect(self):
        if self._client and self._client.is_connected:
            await self._client.disconnect()
        self._client = None
        self._write_char = None
        self._set_state(ConnectionState.DISCONNECTED)

    def _on_disconnect(self, client: BleakClient):
        log.warning("BLE disconnected")
        self._client = None
        self._write_char = None
        self._set_state(ConnectionState.DISCONNECTED)

    @staticmethod
    def _find_write_char(client: BleakClient) -> BleakGATTCharacteristic | None:
        for service in client.services:
            for char in service.characteristics:
                if char.uuid == _CHAR_UUID and "write" in char.properties:
                    return char
        return None

    @staticmethod
    def _find_notify_char(client: BleakClient) -> BleakGATTCharacteristic | None:
        for service in client.services:
            for char in service.characteristics:
                if char.uuid == _CHAR_UUID and "notify" in char.properties:
                    return char
        return None

    def _on_notification(self, char: BleakGATTCharacteristic, data: bytearray):
        text = data.decode("utf-8", errors="replace").strip()
        log.debug("BLE RX: %s", text)
        self._response_queue.put_nowait(text)

    async def send_command(self, command: str, timeout: float = _RESPONSE_TIMEOUT_S) -> str:
        if self._client is None or not self._client.is_connected:
            raise ConnectionError("Not connected to device")
        if self._write_char is None:
            raise ConnectionError("No write characteristic available")

        expected_key = _expected_response_key(command)

        async with self._send_lock:
            payload = command.encode("utf-8")
            log.debug("BLE TX: %s", command)
            await self._client.write_gatt_char(self._write_char, payload)

            deadline = asyncio.get_event_loop().time() + timeout
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    raise TimeoutError(f"No response to {command}")

                try:
                    response = await asyncio.wait_for(
                        self._response_queue.get(), timeout=remaining
                    )
                except asyncio.TimeoutError:
                    log.warning("Timeout waiting for response to %s", command)
                    raise TimeoutError(f"No response to {command}") from None

                if response.startswith("OK+") and _response_key(response) == expected_key:
                    log.debug("BLE response: %s -> %s", command, response)
                    await asyncio.sleep(_COMMAND_SPACING_S)
                    return response

                log.debug("BLE unsolicited: %s (waiting for %s)", response, expected_key)
                for cb in self._on_unsolicited:
                    cb(response)


_STRIP_LAST_DIGIT = {"PIO20", "PIO21", "PDMO1", "PDMO2"}


def _expected_response_key(command: str) -> str:
    body = command.removeprefix("AT+").rstrip("?")
    if body in _STRIP_LAST_DIGIT:
        body = body[:-1]
    return body


def _response_key(response: str) -> str:
    body = response[3:]
    if ":" in body:
        return body.split(":", 1)[0]
    return body
