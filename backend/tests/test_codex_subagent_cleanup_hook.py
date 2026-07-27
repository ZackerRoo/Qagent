from __future__ import annotations

import importlib.util
import json
from pathlib import Path


HOOK_PATH = (
    Path(__file__).resolve().parents[2]
    / ".codex"
    / "hooks"
    / "qagent_subagent_cleanup.py"
)
SPEC = importlib.util.spec_from_file_location("qagent_subagent_cleanup", HOOK_PATH)
assert SPEC and SPEC.loader
HOOK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HOOK)


def _write_transcript(
    codex_home: Path,
    *,
    agent_id: str,
    parent_id: str,
    terminal_event: str = "task_complete",
) -> Path:
    path = codex_home / "sessions" / "2026" / "07" / "27" / f"{agent_id}.jsonl"
    path.parent.mkdir(parents=True)
    records = [
        {
            "type": "session_meta",
            "payload": {
                "id": agent_id,
                "parent_thread_id": parent_id,
                "source": {
                    "subagent": {
                        "thread_spawn": {
                            "parent_thread_id": parent_id,
                            "depth": 1,
                        }
                    }
                },
            },
        },
        {
            "type": "event_msg",
            "payload": {"type": terminal_event},
        },
    ]
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )
    return path


def _write_fake_codex(path: Path) -> None:
    path.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" >> "$QAGENT_FAKE_CODEX_LOG"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def test_subagent_stop_queues_and_parent_stop_deletes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    codex_home = tmp_path / "codex"
    state_dir = tmp_path / "state"
    fake_codex = tmp_path / "codex-bin"
    fake_log = tmp_path / "codex.log"
    _write_fake_codex(fake_codex)
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("QAGENT_HOOK_STATE_DIR", str(state_dir))
    monkeypatch.setenv("QAGENT_CODEX_BIN", str(fake_codex))
    monkeypatch.setenv("QAGENT_FAKE_CODEX_LOG", str(fake_log))

    parent_id = "019eddd9-0d89-7311-9a4f-3c7f1115a9a2"
    agent_id = "019fa177-4673-79c2-a2e1-e7ba9516aca6"
    transcript = _write_transcript(
        codex_home,
        agent_id=agent_id,
        parent_id=parent_id,
    )

    queued, retained = HOOK.handle_event(
        {
            "hook_event_name": "SubagentStop",
            "session_id": parent_id,
            "agent_id": agent_id,
            "agent_transcript_path": str(transcript),
        }
    )
    assert (queued, retained) == (1, 0)
    assert not fake_log.exists()

    deleted, retained = HOOK.handle_event(
        {
            "hook_event_name": "Stop",
            "session_id": parent_id,
        }
    )
    assert (deleted, retained) == (1, 0)
    assert fake_log.read_text(encoding="utf-8").strip() == (
        f"delete --force {agent_id}"
    )
    ledger = (state_dir / "subagent-cleanup-ledger.jsonl").read_text(
        encoding="utf-8"
    )
    assert agent_id in ledger
    assert parent_id in ledger


def test_cleanup_retains_non_terminal_transcript(
    tmp_path: Path,
    monkeypatch,
) -> None:
    codex_home = tmp_path / "codex"
    state_dir = tmp_path / "state"
    fake_codex = tmp_path / "codex-bin"
    fake_log = tmp_path / "codex.log"
    _write_fake_codex(fake_codex)
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("QAGENT_HOOK_STATE_DIR", str(state_dir))
    monkeypatch.setenv("QAGENT_CODEX_BIN", str(fake_codex))
    monkeypatch.setenv("QAGENT_FAKE_CODEX_LOG", str(fake_log))

    parent_id = "parent-1"
    agent_id = "agent-1"
    transcript = _write_transcript(
        codex_home,
        agent_id=agent_id,
        parent_id=parent_id,
        terminal_event="agent_message",
    )
    HOOK.handle_event(
        {
            "hook_event_name": "SubagentStop",
            "session_id": parent_id,
            "agent_id": agent_id,
            "agent_transcript_path": str(transcript),
        }
    )

    assert HOOK.handle_event(
        {"hook_event_name": "Stop", "session_id": parent_id}
    ) == (0, 1)
    assert not fake_log.exists()


def test_cleanup_rejects_parent_mismatch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    codex_home = tmp_path / "codex"
    state_dir = tmp_path / "state"
    fake_codex = tmp_path / "codex-bin"
    fake_log = tmp_path / "codex.log"
    _write_fake_codex(fake_codex)
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("QAGENT_HOOK_STATE_DIR", str(state_dir))
    monkeypatch.setenv("QAGENT_CODEX_BIN", str(fake_codex))
    monkeypatch.setenv("QAGENT_FAKE_CODEX_LOG", str(fake_log))

    transcript = _write_transcript(
        codex_home,
        agent_id="agent-1",
        parent_id="different-parent",
    )
    HOOK.handle_event(
        {
            "hook_event_name": "SubagentStop",
            "session_id": "parent-1",
            "agent_id": "agent-1",
            "agent_transcript_path": str(transcript),
        }
    )

    assert HOOK.handle_event(
        {"hook_event_name": "Stop", "session_id": "parent-1"}
    ) == (0, 1)
    assert not fake_log.exists()


def test_session_start_sweeps_only_stale_queues(
    tmp_path: Path,
    monkeypatch,
) -> None:
    codex_home = tmp_path / "codex"
    state_dir = tmp_path / "state"
    fake_codex = tmp_path / "codex-bin"
    fake_log = tmp_path / "codex.log"
    _write_fake_codex(fake_codex)
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("QAGENT_HOOK_STATE_DIR", str(state_dir))
    monkeypatch.setenv("QAGENT_CODEX_BIN", str(fake_codex))
    monkeypatch.setenv("QAGENT_FAKE_CODEX_LOG", str(fake_log))
    monkeypatch.setenv("QAGENT_HOOK_STALE_SECONDS", "0")

    parent_id = "parent-1"
    agent_id = "agent-1"
    transcript = _write_transcript(
        codex_home,
        agent_id=agent_id,
        parent_id=parent_id,
    )
    HOOK.handle_event(
        {
            "hook_event_name": "SubagentStop",
            "session_id": parent_id,
            "agent_id": agent_id,
            "agent_transcript_path": str(transcript),
        }
    )

    deleted, retained = HOOK.handle_event(
        {"hook_event_name": "SessionStart", "session_id": "new-parent"}
    )
    assert (deleted, retained) == (1, 0)
    assert f"delete --force {agent_id}" in fake_log.read_text(encoding="utf-8")


def test_identifier_and_path_validation_fail_closed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("QAGENT_HOOK_STATE_DIR", str(tmp_path / "state"))

    assert HOOK.handle_event(
        {
            "hook_event_name": "SubagentStop",
            "session_id": "../../parent",
            "agent_id": "agent-1",
            "agent_transcript_path": "/tmp/not-a-session",
        }
    ) == (0, 1)
    assert not (tmp_path / "state" / "subagent-cleanup").exists()
