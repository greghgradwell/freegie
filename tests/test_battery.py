import logging

import pytest

from freegie.battery import BatteryReader

log = logging.getLogger(__name__)


@pytest.fixture
def sysfs(tmp_path):
    """Create a fake /sys/class/power_supply tree."""
    bat = tmp_path / "BAT0"
    bat.mkdir()
    (bat / "type").write_text("Battery\n")
    (bat / "capacity").write_text("72\n")
    (bat / "status").write_text("Charging\n")

    ac = tmp_path / "AC"
    ac.mkdir()
    (ac / "type").write_text("Mains\n")
    (ac / "online").write_text("1\n")

    return tmp_path


@pytest.fixture
def reader(sysfs):
    return BatteryReader(root=sysfs)


def test_read_percent(reader):
    assert reader.read_percent() == 72


def test_read_ac_online(reader):
    assert reader.read_ac_online() is True


def test_read_ac_offline(sysfs):
    (sysfs / "AC" / "online").write_text("0\n")
    reader = BatteryReader(root=sysfs)
    assert reader.read_ac_online() is False


def test_read_status(reader):
    assert reader.read_status() == "Charging"


def test_available(reader):
    assert reader.available is True


def test_no_battery(tmp_path):
    """Empty sysfs root — no battery found."""
    reader = BatteryReader(root=tmp_path)
    assert reader.available is False
    assert reader.read_percent() is None
    assert reader.read_status() is None


def test_no_ac(tmp_path):
    """Battery exists but no AC adapter."""
    bat = tmp_path / "BAT0"
    bat.mkdir()
    (bat / "type").write_text("Battery\n")
    (bat / "capacity").write_text("50\n")

    reader = BatteryReader(root=tmp_path)
    assert reader.read_percent() == 50
    assert reader.read_ac_online() is None


def test_detects_nonstandard_battery_name(tmp_path):
    """Battery with an unusual name is found via type file scan."""
    weird = tmp_path / "XBAT_weird"
    weird.mkdir()
    (weird / "type").write_text("Battery\n")
    (weird / "capacity").write_text("99\n")

    reader = BatteryReader(root=tmp_path)
    assert reader.available is True
    assert reader.read_percent() == 99
    log.info("Detected non-standard battery at %s", weird)


def test_detects_nonstandard_ac_name(tmp_path):
    """AC adapter with an unusual name is found via type file scan."""
    weird_ac = tmp_path / "MY_CHARGER"
    weird_ac.mkdir()
    (weird_ac / "type").write_text("Mains\n")
    (weird_ac / "online").write_text("1\n")

    reader = BatteryReader(root=tmp_path)
    assert reader.read_ac_online() is True


def test_prefers_known_name_over_scan(tmp_path):
    """BAT0 is used even if another Battery-type entry exists."""
    bat0 = tmp_path / "BAT0"
    bat0.mkdir()
    (bat0 / "type").write_text("Battery\n")
    (bat0 / "capacity").write_text("40\n")

    bat_other = tmp_path / "ZZZ_BAT"
    bat_other.mkdir()
    (bat_other / "type").write_text("Battery\n")
    (bat_other / "capacity").write_text("99\n")

    reader = BatteryReader(root=tmp_path)
    assert reader.read_percent() == 40


def test_missing_capacity_file(tmp_path):
    """Battery dir exists but capacity file is missing."""
    bat = tmp_path / "BAT0"
    bat.mkdir()
    (bat / "type").write_text("Battery\n")
    # no capacity file

    reader = BatteryReader(root=tmp_path)
    assert reader.available is True
    assert reader.read_percent() is None
