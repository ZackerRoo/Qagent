from contextlib import contextmanager
import socket
from threading import Event, RLock, Timer

import baostock.common.context as baostock_context


BAOSTOCK_SESSION_LOCK = RLock()


@contextmanager
def serialized_baostock_session():
    """Protect BaoStock's process-global login socket across complete sessions."""
    with BAOSTOCK_SESSION_LOCK:
        yield


@contextmanager
def baostock_call_deadline(timeout_seconds: float):
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    expired = Event()

    def interrupt_socket() -> None:
        expired.set()
        active_socket = getattr(baostock_context, "default_socket", None)
        if active_socket is None:
            return
        try:
            active_socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            active_socket.close()
        except OSError:
            pass

    timer = Timer(timeout_seconds, interrupt_socket)
    timer.daemon = True
    timer.start()
    try:
        yield
    except Exception as exc:
        if expired.is_set():
            raise TimeoutError(
                f"BaoStock call exceeded the {timeout_seconds}s total deadline"
            ) from exc
        raise
    finally:
        timer.cancel()
    if expired.is_set():
        raise TimeoutError(
            f"BaoStock call exceeded the {timeout_seconds}s total deadline"
        )
