import logging
from unittest.mock import MagicMock

import pytest

from freegie.ble import BLEManager, _expected_response_key, _response_key
from freegie.protocol import SCAN_SERVICE_UUIDS

log = logging.getLogger(__name__)


def _make_device(name: str | None = None):
    dev = MagicMock()
    dev.name = name
    dev.address = "AA:BB:CC:DD:EE:FF"
    return dev


def _make_adv(service_uuids: list[str] | None = None):
    adv = MagicMock()
    adv.service_uuids = service_uuids or []
    return adv


def test_scan_filter_matches_primary_uuid():
    dev = _make_device("Something")
    adv = _make_adv([SCAN_SERVICE_UUIDS[0]])
    assert BLEManager._scan_filter(dev, adv) is True


def test_scan_filter_matches_alt_uuid():
    dev = _make_device("Something")
    adv = _make_adv([SCAN_SERVICE_UUIDS[1]])
    assert BLEManager._scan_filter(dev, adv) is True


def test_scan_filter_rejects_name_without_uuid():
    dev = _make_device("Chargie Laptops")
    adv = _make_adv([])
    assert BLEManager._scan_filter(dev, adv) is False


def test_scan_filter_rejects_unknown():
    dev = _make_device("Random Device")
    adv = _make_adv(["00001234-0000-1000-8000-00805f9b34fb"])
    assert BLEManager._scan_filter(dev, adv) is False


def test_scan_filter_handles_none_name():
    dev = _make_device(None)
    adv = _make_adv([])
    assert BLEManager._scan_filter(dev, adv) is False



@pytest.mark.parametrize("command,expected", [
    ("AT+STAT?", "STAT"),
    ("AT+CAPA?", "CAPA"),
    ("AT+FWVR?", "FWVR"),
    ("AT+HWVR?", "HWVR"),
    ("AT+ISPD?", "ISPD"),
    ("AT+PIO20", "PIO2"),
    ("AT+PIO21", "PIO2"),
    ("AT+PDMO1", "PDMO"),
    ("AT+PDMO2", "PDMO"),
    ("AT+HALF0", "HALF"),
    ("AT+HALF1", "HALF"),
])
def test_expected_response_key(command, expected):
    assert _expected_response_key(command) == expected


@pytest.mark.parametrize("response,expected", [
    ("OK+STAT:1.81/15.00", "STAT"),
    ("OK+PIO2:0", "PIO2"),
    ("OK+PDMO:2", "PDMO"),
    ("OK+CAPA:1047965", "CAPA"),
    ("OK+PDCP:0x01", "PDCP"),
    ("OK+Set", "Set"),
])
def test_response_key(response, expected):
    assert _response_key(response) == expected
