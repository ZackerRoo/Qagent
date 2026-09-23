"""Single-host account ownership, independent of the long research-cycle lock.

The kernel flock has no expiring lease: a paused owner retains ownership and a
dead owner releases it. Never unlink this lock file while services are running.
All account reads that inform mutations must live inside this scope. SQLite
commits may be partial; execution-event identities retain their existing meaning.
"""

from contextlib import contextmanager
from functools import wraps
import fcntl
import json
import os
from pathlib import Path
from threading import Lock, RLock, local
from time import monotonic

from sqlalchemy import text
from sqlalchemy.engine import Engine


_registry_lock = Lock()
_locks = {}
_active = local()
_open_fds = set()


def _after_fork():
    global _registry_lock, _locks, _active, _open_fds
    # A fork must not inherit reentrancy or keep its parent's flock alive.
    for fd in _open_fds:
        os.close(fd)
    _registry_lock, _locks, _active, _open_fds = Lock(), {}, local(), set()


os.register_at_fork(after_in_child=_after_fork)


def _identity(session_factory):
    engine = session_factory.kw.get("bind")
    if not isinstance(engine, Engine) or engine.dialect.name != "sqlite":
        raise RuntimeError("paper writer requires an engine-bound SQLite session factory")
    database = engine.url.database
    if not database or database == ":memory:":
        return ("memory", engine), None
    path = Path(database).expanduser().resolve()
    if str(path).startswith("file:"):
        raise RuntimeError("paper writer requires a plain SQLite database path")
    return str(path), str(path) + ".paper-writer.lock"


@contextmanager
def paper_account_writer(session_factory):
    """Reentrant within one thread, mutually exclusive across processes for a DB."""
    key, path = _identity(session_factory)
    started = monotonic()
    with _registry_lock:
        lock = _locks.setdefault(key, RLock())
    with lock:
        active = getattr(_active, "keys", None)
        if active is None:
            active = _active.keys = set()
        if key in active:
            yield {"writer_wait_seconds": monotonic() - started}
            return
        fd = None
        try:
            if path is not None:
                fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
                _open_fds.add(fd)
                fcntl.flock(fd, fcntl.LOCK_EX)
            active.add(key)
            yield {"writer_wait_seconds": monotonic() - started}
        finally:
            active.discard(key)
            if fd is not None:
                _open_fds.discard(fd)
                os.close(fd)


def paper_writer_operation(function):
    """Protect a repository method or an engine function whose first arg is repo."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        repo = args[0] if args else kwargs.get("repo", kwargs.get("self"))
        with paper_account_writer(repo.session_factory):
            return function(*args, **kwargs)
    return wrapped


def paper_writer_route(repository_factory):
    """Protect account-dependent API validation and the subsequent mutations."""
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            with paper_account_writer(repository_factory().session_factory):
                return function(*args, **kwargs)
        return wrapped
    return decorate


def run_paper_update_slot(session_factory, slot_id, callback):
    """Replay completed slots; retry incomplete slots using current account state.

    This journal is operational metadata, not a second account. The callback
    must raise on failure. A crash after a trade commit but before completion
    re-enters the existing engine and its stable execution-event identities.
    """
    with paper_account_writer(session_factory) as timing:
        engine = session_factory.kw["bind"]
        with engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE IF NOT EXISTS paper_update_slots ("
                "slot_id TEXT PRIMARY KEY, result_json TEXT NOT NULL)"
            ))
            previous = connection.execute(text(
                "SELECT result_json FROM paper_update_slots WHERE slot_id = :slot"
            ), {"slot": slot_id}).scalar_one_or_none()
        if previous is not None:
            return {"slot_id": slot_id, "replayed": True, "result": json.loads(previous), **timing}
        try:
            result = callback()
        except Exception as exc:
            # The scheduler only exposes its own controlled error taxonomy, but
            # needs this timing to distinguish a slot that expired in writer wait.
            exc.writer_wait_seconds = timing["writer_wait_seconds"]
            raise
        if hasattr(result, "model_dump"):
            result = result.model_dump(mode="json")
        payload = json.dumps(result, ensure_ascii=False, allow_nan=False)
        with engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO paper_update_slots (slot_id, result_json) VALUES (:slot, :result)"
            ), {"slot": slot_id, "result": payload})
        return {"slot_id": slot_id, "replayed": False, "result": result, **timing}
