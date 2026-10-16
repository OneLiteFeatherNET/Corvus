from __future__ import annotations

import importlib.util

from corvus_services.config import Settings
from corvus_services.nats_source import connect_options


def test_no_credentials_file_by_default():
    assert "user_credentials" not in connect_options(Settings(), "n")


def test_credentials_file_is_passed_to_nats_connect():
    opts = connect_options(Settings(nats_creds_file="/etc/nats/user.creds"), "corvus-ingest")

    assert opts == {
        "max_reconnect_attempts": -1,
        "name": "corvus-ingest",
        "user_credentials": "/etc/nats/user.creds",
    }


def test_environment_variable_sets_the_credentials_file(monkeypatch):
    monkeypatch.setenv("CORVUS_NATS_CREDS_FILE", "/x.creds")

    assert Settings().nats_creds_file == "/x.creds"


def test_nkeys_package_is_installed_for_creds_file_authentication():
    assert importlib.util.find_spec("nkeys") is not None, (
        "nats-py needs the 'nkeys' package (extra nats-py[nkeys]) to authenticate "
        "with a JWT/NKEY creds file; without it the NATS connect crashes at runtime"
    )
