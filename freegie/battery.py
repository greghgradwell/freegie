"""Read battery and AC adapter state from Linux sysfs."""

import logging
from pathlib import Path

log = logging.getLogger(__name__)

POWER_SUPPLY_ROOT = Path("/sys/class/power_supply")

# Common battery directory names across laptop vendors
_BATTERY_NAMES = ("BAT0", "BAT1", "BATT", "battery")
# Common AC adapter directory names
_AC_NAMES = ("AC", "AC0", "ADP0", "ADP1", "ACAD", "ac")


def _find_supply(root: Path, candidates: tuple[str, ...], supply_type: str) -> Path | None:
    for name in candidates:
        path = root / name
        if path.is_dir():
            log.debug("Found %s at %s", supply_type, path)
            return path

    if not root.is_dir():
        return None
    for entry in sorted(root.iterdir()):
        type_file = entry / "type"
        if type_file.is_file():
            contents = type_file.read_text().strip()
            if contents == supply_type:
                log.debug("Found %s at %s (via type scan)", supply_type, entry)
                return entry
    return None


class BatteryReader:
    def __init__(self, root: Path = POWER_SUPPLY_ROOT):
        self._root = root
        self._battery_path: Path | None = None
        self._ac_path: Path | None = None
        self._detect()

    def _detect(self):
        self._battery_path = _find_supply(self._root, _BATTERY_NAMES, "Battery")
        self._ac_path = _find_supply(self._root, _AC_NAMES, "Mains")

        if self._battery_path is None:
            log.warning("No battery found under %s", self._root)
        if self._ac_path is None:
            log.warning("No AC adapter found under %s", self._root)

    @property
    def available(self) -> bool:
        return self._battery_path is not None

    def read_percent(self) -> int | None:
        if self._battery_path is None:
            return None
        capacity_file = self._battery_path / "capacity"
        if not capacity_file.is_file():
            return None
        return int(capacity_file.read_text().strip())

    def read_ac_online(self) -> bool | None:
        if self._ac_path is None:
            return None
        online_file = self._ac_path / "online"
        if not online_file.is_file():
            return None
        return online_file.read_text().strip() == "1"

    def read_status(self) -> str | None:
        if self._battery_path is None:
            return None
        status_file = self._battery_path / "status"
        if not status_file.is_file():
            return None
        return status_file.read_text().strip()
