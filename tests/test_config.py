import json
import logging

import pytest

from freegie.config import ChargeConfig, Config, load_config, load_state, save_state

log = logging.getLogger(__name__)


def test_defaults():
    cfg = Config()
    assert cfg.charge.charge_max == 80
    assert cfg.charge.charge_min == 75
    assert cfg.charge.pd_mode == 2
    assert cfg.daemon.port == 7380
    assert cfg.charge.poll_interval == 3
    assert cfg.charge.telemetry_interval == 30
    assert cfg.charge.auto_reconnect is True
    assert cfg.tray.notifications is True


def test_load_from_file(tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({
        "charge": {
            "charge_max": 83,
            "charge_min": 70,
            "pd_mode": 1
        },
        "daemon": {
            "log_level": "debug"
        }
    }))
    cfg = load_config(config_file)
    assert cfg.charge.charge_max == 83
    assert cfg.charge.charge_min == 70
    assert cfg.charge.pd_mode == 1
    assert cfg.daemon.log_level == "debug"
    # Unset values keep defaults
    assert cfg.daemon.port == 7380
    assert cfg.tray.notifications is True
    log.info("Loaded config: max=%d, pd_mode=%d", cfg.charge.charge_max, cfg.charge.pd_mode)


def test_load_missing_file_returns_defaults(tmp_path):
    cfg = load_config(tmp_path / "nonexistent.json")
    assert cfg.charge.charge_max == 80


def test_partial_config(tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({
        "charge": {"charge_max": 90, "charge_min": 70}
    }))
    cfg = load_config(config_file)
    assert cfg.charge.charge_max == 90
    assert cfg.charge.charge_min == 70


def test_empty_file(tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text("{}")
    cfg = load_config(config_file)
    assert cfg.charge.charge_max == 80


def test_charge_max_too_low():
    with pytest.raises(ValueError, match="charge.charge_max must be 20-100"):
        ChargeConfig(charge_max=10, charge_min=5)


def test_charge_max_too_high():
    with pytest.raises(ValueError, match="charge.charge_max must be 20-100"):
        ChargeConfig(charge_max=101)


def test_charge_min_too_low():
    with pytest.raises(ValueError, match="charge.charge_min must be 20-100"):
        ChargeConfig(charge_min=10)


def test_charge_min_equals_max():
    with pytest.raises(ValueError, match="charge_min.*must be less than.*charge_max"):
        ChargeConfig(charge_max=80, charge_min=80)


def test_charge_min_above_max():
    with pytest.raises(ValueError, match="charge_min.*must be less than.*charge_max"):
        ChargeConfig(charge_max=70, charge_min=80)


def test_invalid_pd_mode():
    with pytest.raises(ValueError, match="charge.pd_mode must be 1 or 2"):
        ChargeConfig(pd_mode=3)


def test_valid_edge_cases():
    cfg = ChargeConfig(charge_max=21, charge_min=20)
    assert cfg.charge_max == 21
    assert cfg.charge_min == 20

    cfg2 = ChargeConfig(charge_max=100, charge_min=99)
    assert cfg2.charge_max == 100
    assert cfg2.charge_min == 99


# --- State persistence ---


def test_save_and_load_state(tmp_path):
    state_file = tmp_path / "state.json"
    save_state(90, 60, 15, path=state_file)

    config = Config()
    assert config.charge.charge_max == 80
    assert config.charge.charge_min == 75
    assert config.charge.telemetry_interval == 30

    load_state(config, path=state_file)

    assert config.charge.charge_max == 90
    assert config.charge.charge_min == 60
    assert config.charge.telemetry_interval == 15


def test_load_state_missing_file(tmp_path):
    config = Config()
    load_state(config, path=tmp_path / "nonexistent.json")

    assert config.charge.charge_max == 80
    assert config.charge.charge_min == 75


def test_load_state_invalid_values(tmp_path):
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({"charge_max": 50, "charge_min": 50}))

    config = Config()
    load_state(config, path=state_file)

    assert config.charge.charge_max == 80
    assert config.charge.charge_min == 75


def test_load_state_partial(tmp_path):
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({"charge_max": 95}))

    config = Config()
    load_state(config, path=state_file)

    assert config.charge.charge_max == 95
    assert config.charge.charge_min == 75


def test_save_state_creates_parent_dirs(tmp_path):
    state_file = tmp_path / "sub" / "dir" / "state.json"
    save_state(85, 70, path=state_file)

    assert state_file.is_file()
    config = Config()
    load_state(config, path=state_file)
    assert config.charge.charge_max == 85
    assert config.charge.charge_min == 70


def test_load_state_telemetry_interval_partial(tmp_path):
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({"charge_max": 90, "charge_min": 60}))

    config = Config()
    load_state(config, path=state_file)

    assert config.charge.telemetry_interval == 30
