import json
import logging
import urllib.error
from argparse import Namespace
from unittest.mock import MagicMock, patch

import pytest

from freegie.cli import cmd_disconnect, cmd_scan, cmd_set_max, cmd_set_min, cmd_status, cmd_stop, run_command

log = logging.getLogger(__name__)


def _mock_urlopen(response_data):
    resp = MagicMock()
    resp.read.return_value = json.dumps(response_data).encode()
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


@patch("freegie.cli.urllib.request.urlopen")
def test_cmd_status_basic(mock_urlopen, capsys):
    mock_urlopen.return_value = _mock_urlopen({
        "phase": "controlling",
        "battery_percent": 72,
        "is_charging": True,
        "charge_max": 80,
        "charge_min": 75,
        "telemetry": {"volts": 4.24, "amps": 0.80, "watts": 3.39},
        "device": {"name": "Chargie Laptops", "firmware": 10, "hardware": "3.00"},
    })

    cmd_status("http://127.0.0.1:7380")

    output = capsys.readouterr().out
    assert "Phase:     controlling" in output
    assert "Battery:   72%" in output
    assert "Charging:  yes" in output
    assert "Max:       80% (min: 75%)" in output
    assert "Device:    Chargie Laptops (FW: 10, HW: 3.00)" in output
    assert "Telemetry: 4.24V  0.80A  3.39W" in output


@patch("freegie.cli.urllib.request.urlopen")
def test_cmd_status_minimal(mock_urlopen, capsys):
    mock_urlopen.return_value = _mock_urlopen({
        "phase": "idle",
        "battery_percent": None,
        "is_charging": False,
        "charge_max": 80,
        "charge_min": 75,
        "telemetry": None,
        "device": None,
    })

    cmd_status("http://127.0.0.1:7380")

    output = capsys.readouterr().out
    assert "Phase:     idle" in output
    assert "Battery:   --" in output
    assert "Charging:  no" in output
    assert "Device:" not in output
    assert "Telemetry:" not in output


@patch("freegie.cli.urllib.request.urlopen")
def test_cmd_set_max(mock_urlopen, capsys):
    mock_urlopen.return_value = _mock_urlopen({"ok": True})

    cmd_set_max("http://127.0.0.1:7380", 85)

    output = capsys.readouterr().out
    assert "Charge max set to 85%" in output


@patch("freegie.cli.urllib.request.urlopen")
def test_cmd_set_min(mock_urlopen, capsys):
    mock_urlopen.return_value = _mock_urlopen({"ok": True})

    cmd_set_min("http://127.0.0.1:7380", 70)

    output = capsys.readouterr().out
    assert "Charge min set to 70%" in output


@patch("freegie.cli.urllib.request.urlopen")
def test_cmd_scan(mock_urlopen, capsys):
    mock_urlopen.return_value = _mock_urlopen({"ok": True, "message": "Scan started"})

    cmd_scan("http://127.0.0.1:7380")

    output = capsys.readouterr().out
    assert "Scan started" in output


@patch("freegie.cli.urllib.request.urlopen")
def test_cmd_disconnect(mock_urlopen, capsys):
    mock_urlopen.return_value = _mock_urlopen({"ok": True})

    cmd_disconnect("http://127.0.0.1:7380")

    output = capsys.readouterr().out
    assert "Disconnected" in output


@patch("freegie.cli.urllib.request.urlopen")
def test_daemon_unreachable(mock_urlopen):
    mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

    with pytest.raises(SystemExit) as exc_info:
        cmd_status("http://127.0.0.1:7380")

    assert exc_info.value.code == 1


@patch("freegie.cli.urllib.request.urlopen")
def test_cmd_stop(mock_urlopen, capsys):
    mock_urlopen.return_value = _mock_urlopen({"ok": True})

    cmd_stop("http://127.0.0.1:7380")

    output = capsys.readouterr().out
    assert "Daemon stopped" in output

    req = mock_urlopen.call_args[0][0]
    assert req.full_url == "http://127.0.0.1:7380/api/shutdown"
    assert req.method == "POST"


@patch("freegie.cli.urllib.request.urlopen")
def test_run_command_dispatch(mock_urlopen, capsys):
    mock_urlopen.return_value = _mock_urlopen({
        "phase": "idle",
        "battery_percent": 50,
        "is_charging": False,
        "charge_max": 80,
        "charge_min": 75,
        "telemetry": None,
        "device": None,
    })

    args = Namespace(command="status", url="http://127.0.0.1:7380")
    run_command(args)

    output = capsys.readouterr().out
    assert "Phase:     idle" in output
