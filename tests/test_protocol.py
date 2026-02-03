import logging

import pytest

from freegie.protocol import (
    Capabilities,
    ParseError,
    Telemetry,
    parse_capabilities,
    parse_firmware,
    parse_hardware,
    parse_power_state,
    parse_response,
    parse_telemetry,
)

log = logging.getLogger(__name__)


def test_parse_response_stat():
    key, value = parse_response("OK+STAT:1.81/15.00")
    assert key == "STAT"
    assert value == "1.81/15.00"


def test_parse_response_pio2():
    key, value = parse_response("OK+PIO2:0")
    assert key == "PIO2"
    assert value == "0"


def test_parse_response_no_value():
    key, value = parse_response("OK+Set")
    assert key == "Set"
    assert value == ""


def test_parse_response_strips_whitespace():
    key, value = parse_response("  OK+FWVR:10\n")
    assert key == "FWVR"
    assert value == "10"


def test_parse_response_colon_in_value():
    key, value = parse_response("OK+TEST:a:b:c")
    assert key == "TEST"
    assert value == "a:b:c"


def test_parse_response_not_ok():
    with pytest.raises(ParseError, match="Not an OK"):
        parse_response("ERROR")


def test_parse_response_empty_string():
    with pytest.raises(ParseError):
        parse_response("")


@pytest.mark.parametrize("raw,expected_volts,expected_amps", [
    ("OK+STAT:1.81/15.00", 1.81, 15.00),
    ("OK+STAT:4.24/15.00", 4.24, 15.00),
    ("OK+STAT:0.00/0.00", 0.0, 0.0),
    ("OK+STAT:2.56/15.00", 2.56, 15.00),
])
def test_parse_telemetry_valid(raw, expected_volts, expected_amps):
    t = parse_telemetry(raw)
    assert t.volts == pytest.approx(expected_volts)
    assert t.amps == pytest.approx(expected_amps)
    log.info("Parsed %s -> %.2fV %.2fA %.2fW", raw, t.volts, t.amps, t.watts)


def test_parse_telemetry_watts():
    t = parse_telemetry("OK+STAT:4.24/15.00")
    assert t.watts == pytest.approx(63.60)


def test_parse_telemetry_wrong_key():
    with pytest.raises(ParseError, match="Expected STAT"):
        parse_telemetry("OK+CAPA:12345")


def test_parse_telemetry_bad_payload():
    with pytest.raises(ParseError, match="Bad STAT"):
        parse_telemetry("OK+STAT:garbage")


def test_parse_capabilities_real_device():
    # 1047965 = 0x100A9D -> bit0=1(PD), bit1=0(FET2), bit2=1(AUTO)
    cap = parse_capabilities("OK+CAPA:1047965")
    assert cap.pd is True
    assert cap.fet2 is False
    assert cap.auto is True
    assert cap.raw == 1047965
    log.info("Capabilities: PD=%s FET2=%s AUTO=%s (raw=0x%X)", cap.pd, cap.fet2, cap.auto, cap.raw)


def test_parse_capabilities_all_bits_off():
    cap = parse_capabilities("OK+CAPA:0")
    assert cap.pd is False
    assert cap.fet2 is False
    assert cap.auto is False


def test_parse_capabilities_all_bits_on():
    cap = parse_capabilities("OK+CAPA:7")
    assert cap.pd is True
    assert cap.fet2 is True
    assert cap.auto is True


def test_parse_capabilities_wrong_key():
    with pytest.raises(ParseError, match="Expected CAPA"):
        parse_capabilities("OK+STAT:1.0/1.0")


def test_parse_capabilities_non_integer():
    with pytest.raises(ParseError, match="Bad CAPA"):
        parse_capabilities("OK+CAPA:abc")


def test_parse_firmware():
    assert parse_firmware("OK+FWVR:10") == "10"


def test_parse_firmware_wrong_key():
    with pytest.raises(ParseError, match="Expected FWVR"):
        parse_firmware("OK+HWVR:3.00")


def test_parse_hardware():
    assert parse_hardware("OK+HWVR:3.00") == "3.00"


def test_parse_hardware_wrong_key():
    with pytest.raises(ParseError, match="Expected HWVR"):
        parse_hardware("OK+FWVR:10")


def test_parse_power_state_on():
    assert parse_power_state("OK+PIO2:1") is True


def test_parse_power_state_off():
    assert parse_power_state("OK+PIO2:0") is False


def test_parse_power_state_wrong_key():
    with pytest.raises(ParseError, match="Expected PIO2"):
        parse_power_state("OK+STAT:1.0/1.0")


def test_parse_power_state_bad_value():
    with pytest.raises(ParseError, match="Bad PIO2"):
        parse_power_state("OK+PIO2:2")


def test_telemetry_frozen():
    t = Telemetry(volts=1.0, amps=2.0)
    with pytest.raises(AttributeError):
        t.volts = 5.0  # type: ignore[misc]


def test_capabilities_frozen():
    c = Capabilities(raw=7, pd=True, fet2=True, auto=True)
    with pytest.raises(AttributeError):
        c.pd = False  # type: ignore[misc]
