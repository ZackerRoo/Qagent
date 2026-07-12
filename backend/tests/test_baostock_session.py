import time
from threading import Thread

import baostock.common.context as baostock_context
import pytest

from qagent.providers.baostock_session import (
    baostock_call_deadline,
    serialized_baostock_session,
)


class FakeSocket:
    def __init__(self):
        self.shutdown_called = False
        self.close_called = False

    def shutdown(self, _how):
        self.shutdown_called = True

    def close(self):
        self.close_called = True


def test_baostock_sessions_do_not_overlap_between_background_jobs():
    events: list[str] = []

    def run(name: str):
        with serialized_baostock_session():
            events.append(f"{name}:login")
            time.sleep(0.02)
            events.append(f"{name}:query")
            time.sleep(0.02)
            events.append(f"{name}:logout")

    first = Thread(target=run, args=("first",))
    second = Thread(target=run, args=("second",))
    first.start()
    second.start()
    first.join()
    second.join()

    assert events in [
        [
            "first:login",
            "first:query",
            "first:logout",
            "second:login",
            "second:query",
            "second:logout",
        ],
        [
            "second:login",
            "second:query",
            "second:logout",
            "first:login",
            "first:query",
            "first:logout",
        ],
    ]


def test_baostock_call_deadline_closes_global_socket_and_raises(monkeypatch):
    active_socket = FakeSocket()
    monkeypatch.setattr(
        baostock_context,
        "default_socket",
        active_socket,
        raising=False,
    )

    with pytest.raises(TimeoutError, match="total deadline"):
        with baostock_call_deadline(0.01):
            time.sleep(0.03)

    assert active_socket.shutdown_called is True
    assert active_socket.close_called is True


def test_baostock_call_deadline_leaves_socket_open_when_call_finishes(monkeypatch):
    active_socket = FakeSocket()
    monkeypatch.setattr(
        baostock_context,
        "default_socket",
        active_socket,
        raising=False,
    )

    with baostock_call_deadline(1):
        pass

    assert active_socket.shutdown_called is False
    assert active_socket.close_called is False


def test_baostock_call_deadline_replaces_socket_error_after_expiry(monkeypatch):
    monkeypatch.setattr(
        baostock_context,
        "default_socket",
        FakeSocket(),
        raising=False,
    )

    with pytest.raises(TimeoutError, match="total deadline"):
        with baostock_call_deadline(0.01):
            time.sleep(0.03)
            raise OSError("socket was closed by deadline")
