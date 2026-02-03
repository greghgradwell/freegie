"""Configuration loading from TOML files."""

import logging
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)
_USER_CONFIG = Path.home() / ".config" / "freegie" / "config.toml"
_SYSTEM_CONFIG = Path("/etc/freegie/config.toml")


@dataclass
class ChargeConfig:
    limit: int = 80
    allowed_drop: int = 5
    pd_mode: int = 2
    poll_interval: int = 3

    def __post_init__(self):
        if not 20 <= self.limit <= 100:
            raise ValueError(f"charge.limit must be 20-100, got {self.limit}")
        if not 1 <= self.allowed_drop <= 20:
            raise ValueError(f"charge.allowed_drop must be 1-20, got {self.allowed_drop}")
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
            raw = candidate.read_bytes()
            data = tomllib.loads(raw.decode())
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
