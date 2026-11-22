from __future__ import annotations

from typing import Any
from uuid import uuid4

from backend.app.db.database import connection, json_dump, utc_now


def create_processing_run(
    transformation_id: str,
    target_version: int,
    feedback: str | None = None,
) -> str:
    run_id = str(uuid4())
    with connection() as db:
        db.execute(
            """
            INSERT INTO transformation_runs
              (id, transformation_id, target_version, feedback, status, started_at)
            VALUES (?, ?, ?, ?, 'QUEUED', ?)
            """,
            (run_id, transformation_id, target_version, feedback, utc_now()),
        )
    return run_id


def record_run_event(
    run_id: str,
    transformation_id: str,
    stage: str,
    event_type: str,
    details: dict[str, Any] | None = None,
    *,
    level: str = "INFO",
) -> None:
    with connection() as db:
        db.execute(
            """
            INSERT INTO transformation_run_events
              (run_id, transformation_id, stage, event_type, level, details_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, transformation_id, stage, event_type, level, json_dump(details or {}), utc_now()),
        )


def set_run_status(run_id: str, status: str) -> None:
    finished_at = utc_now() if status in {"COMPLETED", "FAILED", "CANCELLED"} else None
    with connection() as db:
        db.execute(
            "UPDATE transformation_runs SET status = ?, finished_at = ? WHERE id = ?",
            (status, finished_at, run_id),
        )


__all__ = ["create_processing_run", "record_run_event", "set_run_status"]
