from datetime import datetime, timezone
from threading import Lock
from typing import Callable
from uuid import uuid4

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TaskRecord(BaseModel):
    task_id: str
    kind: str
    status: str = "queued"
    progress: int = 0
    message: str = "Queued"
    result: dict[str, object] | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None


class TaskManager:
    def __init__(self) -> None:
        self._records: dict[str, TaskRecord] = {}
        self._lock = Lock()

    def create(self, kind: str, message: str = "Queued") -> TaskRecord:
        record = TaskRecord(task_id=f"task-{uuid4().hex[:12]}", kind=kind, message=message)
        with self._lock:
            self._records[record.task_id] = record
        return record

    def get(self, task_id: str) -> TaskRecord | None:
        with self._lock:
            record = self._records.get(task_id)
            return record.model_copy(deep=True) if record else None

    def list(self, limit: int = 20) -> list[TaskRecord]:
        with self._lock:
            records = sorted(self._records.values(), key=lambda item: item.created_at, reverse=True)
            return [record.model_copy(deep=True) for record in records[:limit]]

    def mark_running(self, task_id: str, message: str = "Running") -> None:
        self.update(task_id, status="running", progress=5, message=message, started_at=_now())

    def mark_succeeded(self, task_id: str, result: dict[str, object], message: str = "Done") -> None:
        self.update(
            task_id,
            status="succeeded",
            progress=100,
            message=message,
            result=result,
            finished_at=_now(),
        )

    def mark_failed(self, task_id: str, error: str) -> None:
        self.update(
            task_id,
            status="failed",
            progress=100,
            message="Failed",
            error=error,
            finished_at=_now(),
        )

    def update(self, task_id: str, **changes: object) -> None:
        with self._lock:
            record = self._records.get(task_id)
            if record is None:
                return
            self._records[task_id] = record.model_copy(update=changes)

    def run(self, task_id: str, work: Callable[[], dict[str, object]]) -> None:
        self.mark_running(task_id)
        try:
            result = work()
        except Exception as exc:
            self.mark_failed(task_id, str(exc))
            return
        self.mark_succeeded(task_id, result)
