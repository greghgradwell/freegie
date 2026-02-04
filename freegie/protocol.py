"""AT command protocol for Chargie BLE devices."""

from dataclasses import dataclass

SERVICE_UUID_PRIMARY = "0000ffd6-0000-1000-8000-00805f9b34fb"
SERVICE_UUID_ALT = "0000ffaa-0000-1000-8000-00805f9b34fb"
SCAN_SERVICE_UUIDS = [SERVICE_UUID_PRIMARY, SERVICE_UUID_ALT]

CMD_STAT = "AT+STAT?"
CMD_CAPA = "AT+CAPA?"
CMD_FWVR = "AT+FWVR?"
CMD_HWVR = "AT+HWVR?"
CMD_ISPD = "AT+ISPD?"

CMD_POWER_OFF = "AT+PIO20"  # Cut USB-C power (stop charging)
CMD_POWER_ON = "AT+PIO21"   # Restore USB-C power (start charging)

CMD_PD_MODE_1 = "AT+PDMO1"  # Half PD — reduced voltage/wattage
CMD_PD_MODE_2 = "AT+PDMO2"  # Full PD — maximum negotiated voltage/wattage


CAPA_BIT_PD = 0       # Supports USB Power Delivery
CAPA_BIT_FET2 = 1     # Has second FET (dual-channel)
CAPA_BIT_AUTO = 2     # Supports auto mode


@dataclass(frozen=True, slots=True)
class Capabilities:
    raw: int
    pd: bool
    fet2: bool
    auto: bool


@dataclass(frozen=True, slots=True)
class Telemetry:
    volts: float
    amps: float

    @property
    def watts(self) -> float:
        return round(self.volts * self.amps, 2)


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    firmware: str
    hardware: str
    capabilities: Capabilities


class ParseError(ValueError):
    pass


def parse_response(raw: str) -> tuple[str, str]:
    raw = raw.strip()
    if not raw.startswith("OK+"):
        raise ParseError(f"Not an OK+ response: {raw!r}")

    body = raw[3:]
    if ":" in body:
        key, value = body.split(":", 1)
    else:
        key, value = body, ""
    return key, value


def parse_telemetry(raw: str) -> Telemetry:
    key, value = parse_response(raw)
    if key != "STAT":
        raise ParseError(f"Expected STAT response, got {key!r}")
    try:
        amps_s, volts_s = value.split("/")
        return Telemetry(volts=float(volts_s), amps=float(amps_s))
    except (ValueError, AttributeError) as e:
        raise ParseError(f"Bad STAT payload: {value!r}") from e


def parse_capabilities(raw: str) -> Capabilities:
    key, value = parse_response(raw)
    if key != "CAPA":
        raise ParseError(f"Expected CAPA response, got {key!r}")
    try:
        bitmask = int(value)
    except ValueError as e:
        raise ParseError(f"Bad CAPA payload: {value!r}") from e
    return Capabilities(
        raw=bitmask,
        pd=bool(bitmask & (1 << CAPA_BIT_PD)),
        fet2=bool(bitmask & (1 << CAPA_BIT_FET2)),
        auto=bool(bitmask & (1 << CAPA_BIT_AUTO)),
    )


def parse_firmware(raw: str) -> str:
    key, value = parse_response(raw)
    if key != "FWVR":
        raise ParseError(f"Expected FWVR response, got {key!r}")
    return value


def parse_hardware(raw: str) -> str:
    key, value = parse_response(raw)
    if key != "HWVR":
        raise ParseError(f"Expected HWVR response, got {key!r}")
    return value


def parse_power_state(raw: str) -> bool:
    key, value = parse_response(raw)
    if key != "PIO2":
        raise ParseError(f"Expected PIO2 response, got {key!r}")
    if value == "1":
        return True
    if value == "0":
        return False
    raise ParseError(f"Bad PIO2 payload: {value!r}")
