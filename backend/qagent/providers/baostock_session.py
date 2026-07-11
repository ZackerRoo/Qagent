from contextlib import contextmanager
from threading import RLock


BAOSTOCK_SESSION_LOCK = RLock()


@contextmanager
def serialized_baostock_session():
    """Protect BaoStock's process-global login socket across complete sessions."""
    with BAOSTOCK_SESSION_LOCK:
        yield
