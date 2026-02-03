import logging

import pytest

from freegie.config import ChargeConfig, Config, load_config

log = logging.getLogger(__name__)


def test_defaults():
    cfg = Config()
    assert cfg.charge.limit == 80
    assert cfg.charge.allowed_drop == 5
    assert cfg.charge.pd_mode == 2
    assert cfg.daemon.port == 7380
    assert cfg.charge.poll_interval == 3
    assert cfg.tray.notifications is True


def test_load_from_file(tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "[charge]\n"
        "limit = 83\n"
        "allowed_drop = 3\n"
        "pd_mode = 1\n"
        "\n"
        "[daemon]\n"
        'log_level = "debug"\n'
    )
    cfg = load_config(config_file)
    assert cfg.charge.limit == 83
    assert cfg.charge.allowed_drop == 3
    assert cfg.charge.pd_mode == 1
    assert cfg.daemon.log_level == "debug"
    # Unset values keep defaults
    assert cfg.daemon.port == 7380
    assert cfg.tray.notifications is True
    log.info("Loaded config: limit=%d, pd_mode=%d", cfg.charge.limit, cfg.charge.pd_mode)


def test_load_missing_file_returns_defaults(tmp_path):
    cfg = load_config(tmp_path / "nonexistent.toml")
    assert cfg.charge.limit == 80


def test_partial_config(tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text("[charge]\nlimit = 90\n")
    cfg = load_config(config_file)
    assert cfg.charge.limit == 90
    assert cfg.charge.allowed_drop == 5  # default


def test_empty_file(tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text("")
    cfg = load_config(config_file)
    assert cfg.charge.limit == 80


def test_charge_limit_too_low():
    with pytest.raises(ValueError, match="charge.limit must be 20-100"):
        ChargeConfig(limit=10)


def test_charge_limit_too_high():
    with pytest.raises(ValueError, match="charge.limit must be 20-100"):
        ChargeConfig(limit=101)


def test_allowed_drop_out_of_range():
    with pytest.raises(ValueError, match="charge.allowed_drop must be 1-20"):
        ChargeConfig(allowed_drop=0)


def test_invalid_pd_mode():
    with pytest.raises(ValueError, match="charge.pd_mode must be 1 or 2"):
        ChargeConfig(pd_mode=3)
