#!/usr/bin/env python3
"""Fail-closed cleanup for completed Codex sub-agent sessions."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


TERMINAL_EVENTS = {"task_complete", "turn_aborted"}
SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
DEFAULT_STALE_SECONDS = 24 * 60 * 60


def _codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser().resolve()


def _state_dir() -> Path:
    override = os.environ.get("QAGENT_HOOK_STATE_DIR")
    path = Path(override).expanduser() if override else _codex_home() / "qagent-hooks"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path.resolve()


def _queue_path(parent_session_id: str) -> Path:
    digest = hashlib.sha256(parent_session_id.encode("utf-8")).hexdigest()[:24]
    queue_dir = _state_dir() / "subagent-cleanup"
    queue_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    return queue_dir / f"{digest}.json"


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=True, sort_keys=True)
            handle.write("\n")
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _append_ledger(record: dict[str, Any]) -> None:
    path = _state_dir() / "subagent-cleanup-ledger.jsonl"
    with _locked(path):
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=True, sort_keys=True))
            handle.write("\n")
        os.chmod(path, 0o600)


def _safe_identifier(value: Any) -> str | None:
    if isinstance(value, str) and SAFE_ID.fullmatch(value):
        return value
    return None


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _read_first_record(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            line = handle.readline()
    except OSError:
        return {}
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _terminal_event(path: Path, tail_bytes: int = 2 * 1024 * 1024) -> str | None:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - tail_bytes))
            raw = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return None

    for line in reversed(raw.splitlines()):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "event_msg":
            continue
        event_type = event.get("payload", {}).get("type")
        if event_type in TERMINAL_EVENTS:
            return str(event_type)
    return None


def _validate_transcript(
    transcript_path: str,
    agent_id: str,
    parent_session_id: str,
) -> tuple[Path | None, str | None]:
    path = Path(transcript_path).expanduser().resolve()
    sessions_root = (_codex_home() / "sessions").resolve()
    if not _is_within(path, sessions_root) or not path.is_file():
        return None, None

    meta = _read_first_record(path)
    payload = meta.get("payload", {})
    source = payload.get("source", {})
    spawn = source.get("subagent", {}).get("thread_spawn", {})
    recorded_parent = payload.get("parent_thread_id") or spawn.get("parent_thread_id")
    if (
        meta.get("type") != "session_meta"
        or payload.get("id") != agent_id
        or recorded_parent != parent_session_id
    ):
        return None, None

    terminal = _terminal_event(path)
    if terminal not in TERMINAL_EVENTS:
        return None, None
    return path, terminal


def _queue_subagent(event: dict[str, Any]) -> tuple[int, int]:
    parent_id = _safe_identifier(event.get("session_id"))
    agent_id = _safe_identifier(event.get("agent_id"))
    transcript = event.get("agent_transcript_path")
    if not parent_id or not agent_id or not isinstance(transcript, str):
        return 0, 1

    path = _queue_path(parent_id)
    with _locked(path):
        queue = _load_json(path)
        if queue and queue.get("parent_session_id") != parent_id:
            return 0, 1
        entries = queue.setdefault("entries", {})
        entries[agent_id] = {
            "agent_id": agent_id,
            "parent_session_id": parent_id,
            "transcript_path": transcript,
            "queued_at": int(time.time()),
        }
        queue["parent_session_id"] = parent_id
        _atomic_write(path, queue)
    return 1, 0


def _codex_binary() -> str | None:
    override = os.environ.get("QAGENT_CODEX_BIN")
    if override:
        return override
    discovered = shutil.which("codex")
    if discovered:
        return discovered
    bundled = Path("/Applications/ChatGPT.app/Contents/Resources/codex")
    return str(bundled) if bundled.is_file() else None


def _delete_session(agent_id: str) -> tuple[bool, str]:
    binary = _codex_binary()
    if not binary:
        return False, "codex binary unavailable"
    try:
        result = subprocess.run(
            [binary, "delete", "--force", agent_id],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    if result.returncode == 0:
        return True, ""
    detail = (result.stderr or result.stdout).strip()
    return False, detail[-500:]


def _clean_queue(path: Path) -> tuple[int, int]:
    deleted = 0
    retained = 0
    with _locked(path):
        queue = _load_json(path)
        parent_id = _safe_identifier(queue.get("parent_session_id"))
        entries = queue.get("entries", {})
        if not parent_id or not isinstance(entries, dict):
            return 0, 1

        remaining: dict[str, Any] = {}
        for agent_id, entry in entries.items():
            safe_agent_id = _safe_identifier(agent_id)
            transcript = entry.get("transcript_path") if isinstance(entry, dict) else None
            if not safe_agent_id or not isinstance(transcript, str):
                retained += 1
                remaining[agent_id] = entry
                continue

            validated_path, terminal = _validate_transcript(
                transcript,
                safe_agent_id,
                parent_id,
            )
            if not validated_path or not terminal:
                retained += 1
                remaining[agent_id] = entry
                continue

            size_bytes = validated_path.stat().st_size
            success, error = _delete_session(safe_agent_id)
            if success:
                deleted += 1
                _append_ledger(
                    {
                        "agent_id": safe_agent_id,
                        "bytes_removed": size_bytes,
                        "deleted_at": int(time.time()),
                        "parent_session_id": parent_id,
                        "terminal_event": terminal,
                    }
                )
            else:
                retained += 1
                entry["last_error"] = error
                remaining[agent_id] = entry

        if remaining:
            queue["entries"] = remaining
            _atomic_write(path, queue)
        else:
            path.unlink(missing_ok=True)
    return deleted, retained


def _clean_parent(parent_session_id: str) -> tuple[int, int]:
    return _clean_queue(_queue_path(parent_session_id))


def _clean_stale() -> tuple[int, int]:
    stale_seconds = int(
        os.environ.get("QAGENT_HOOK_STALE_SECONDS", DEFAULT_STALE_SECONDS)
    )
    cutoff = time.time() - max(0, stale_seconds)
    queue_dir = _state_dir() / "subagent-cleanup"
    if not queue_dir.is_dir():
        return 0, 0

    deleted = 0
    retained = 0
    for path in queue_dir.glob("*.json"):
        try:
            is_stale = path.stat().st_mtime <= cutoff
        except OSError:
            continue
        if not is_stale:
            continue
        cleaned, kept = _clean_queue(path)
        deleted += cleaned
        retained += kept
    return deleted, retained


def handle_event(event: dict[str, Any]) -> tuple[int, int]:
    event_name = event.get("hook_event_name")
    if event_name == "SubagentStop":
        return _queue_subagent(event)
    if event_name == "Stop":
        parent_id = _safe_identifier(event.get("session_id"))
        return _clean_parent(parent_id) if parent_id else (0, 1)
    if event_name == "SessionStart":
        return _clean_stale()
    return 0, 0


def main() -> int:
    try:
        event = json.load(sys.stdin)
        if not isinstance(event, dict):
            raise ValueError("hook input must be a JSON object")
        _, retained = handle_event(event)
        output: dict[str, Any] = {"continue": True}
        if retained:
            output["systemMessage"] = (
                "Sub-agent session cleanup retained entries that failed safety checks."
            )
        print(json.dumps(output, ensure_ascii=True))
        return 0
    except Exception as exc:  # Fail closed without interrupting the parent task.
        print(
            json.dumps(
                {
                    "continue": True,
                    "systemMessage": f"Sub-agent session cleanup skipped: {exc}",
                },
                ensure_ascii=True,
            )
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
