"""Configuration loading from JSON files, with persistent user state."""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)
_USER_CONFIG = Path.home() / ".config" / "freegie" / "config.json"
_SYSTEM_CONFIG = Path("/etc/freegie/config.json")
_STATE_FILE = Path.home() / ".config" / "freegie" / "state.json"


@dataclass
class ChargeConfig:
    charge_max: int = 80
    charge_min: int = 75
    pd_mode: int = 2
    poll_interval: int = 3
    telemetry_interval: int = 30
    auto_reconnect: bool = True

    def __post_init__(self):
        if not 20 <= self.charge_max <= 100:
            raise ValueError(f"charge.charge_max must be 20-100, got {self.charge_max}")
        if not 20 <= self.charge_min <= 100:
            raise ValueError(f"charge.charge_min must be 20-100, got {self.charge_min}")
        if self.charge_min >= self.charge_max:
            raise ValueError(
                f"charge.charge_min ({self.charge_min}) must be less than "
                f"charge.charge_max ({self.charge_max})"
            )
        if self.pd_mode not in (1, 2):
            raise ValueError(f"charge.pd_mode must be 1 or 2, got {self.pd_mode}")


@dataclass
class DaemonConfig:
    port: int = 7380
    log_level: str = "info"


@dataclass
class TrayConfig:
    notifications: bool = True


@dataclass
class Config:
    charge: ChargeConfig = field(default_factory=ChargeConfig)
    daemon: DaemonConfig = field(default_factory=DaemonConfig)
    tray: TrayConfig = field(default_factory=TrayConfig)


def load_config(path: Path | None = None) -> Config:
    if path is not None:
        candidates = [path]
    else:
        candidates = [_USER_CONFIG, _SYSTEM_CONFIG]

    for candidate in candidates:
        if candidate.is_file():
            log.info("Loading config from %s", candidate)
            data = json.loads(candidate.read_text())
            return _parse(data)

    log.info("No config file found, using defaults")
    return Config()


def _parse(data: dict) -> Config:
    charge_data = data.get("charge", {})
    daemon_data = data.get("daemon", {})
    tray_data = data.get("tray", {})

    return Config(
        charge=ChargeConfig(**charge_data),
        daemon=DaemonConfig(**daemon_data),
        tray=TrayConfig(**tray_data),
    )


def load_state(config: Config, path: Path = _STATE_FILE) -> Config:
    if not path.is_file():
        return config

    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        log.warning("Failed to read state file %s: %s", path, e)
        return config

    charge_min = data.get("charge_min", config.charge.charge_min)
    charge_max = data.get("charge_max", config.charge.charge_max)
    telemetry_interval = data.get("telemetry_interval", config.charge.telemetry_interval)

    try:
        config.charge = ChargeConfig(
            charge_max=charge_max,
            charge_min=charge_min,
            pd_mode=config.charge.pd_mode,
            poll_interval=config.charge.poll_interval,
            telemetry_interval=telemetry_interval,
            auto_reconnect=config.charge.auto_reconnect,
        )
    except ValueError as e:
        log.warning("Ignoring invalid saved state: %s", e)

    return config


def save_state(charge_max: int, charge_min: int, telemetry_interval: int = 30, path: Path = _STATE_FILE):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "charge_max": charge_max,
        "charge_min": charge_min,
        "telemetry_interval": telemetry_interval,
    }
    path.write_text(json.dumps(data, indent=2) + "\n")
