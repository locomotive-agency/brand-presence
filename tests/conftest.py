import socket

import google.auth
import pytest

from fakes import misuses, unsettled


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("tests must not use the network or application-default credentials")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(google.auth, "default", denied)
    yield
    assert not misuses()
    assert not unsettled()
