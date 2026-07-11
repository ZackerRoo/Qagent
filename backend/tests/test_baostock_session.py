from threading import Thread
from time import sleep

from qagent.providers.baostock_session import serialized_baostock_session


def test_baostock_sessions_do_not_overlap_between_background_jobs():
    events: list[str] = []

    def run(name: str):
        with serialized_baostock_session():
            events.append(f"{name}:login")
            sleep(0.02)
            events.append(f"{name}:query")
            sleep(0.02)
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
