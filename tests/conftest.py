import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--chargie",
        action="store_true",
        default=False,
        help="Run integration tests that require Chargie hardware",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--chargie"):
        return
    skip = pytest.mark.skip(reason="needs --chargie flag and real hardware")
    for item in items:
        if "chargie" in item.keywords:
            item.add_marker(skip)
