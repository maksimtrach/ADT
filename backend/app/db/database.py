from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from backend.app.core.config import settings


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def json_load(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(settings.database_path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA journal_mode = WAL")
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  email TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('USER', 'ADMIN')),
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

CREATE TABLE IF NOT EXISTS transformations (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id),
  source_filename TEXT NOT NULL,
  source_path TEXT NOT NULL,
  data_type TEXT NOT NULL,
  template_id TEXT NOT NULL,
  status TEXT NOT NULL,
  current_stage TEXT NOT NULL,
  processed_rows INTEGER NOT NULL DEFAULT 0,
  unprocessed_rows INTEGER NOT NULL DEFAULT 0,
  control_status TEXT NOT NULL DEFAULT 'NOT_PERFORMED',
  controls_json TEXT NOT NULL DEFAULT '{}',
  warnings_json TEXT NOT NULL DEFAULT '[]',
  error_message TEXT,
  current_version INTEGER NOT NULL DEFAULT 0,
  llm_used INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_transformations_user_created
  ON transformations(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS transformation_versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  transformation_id TEXT NOT NULL REFERENCES transformations(id) ON DELETE CASCADE,
  version INTEGER NOT NULL,
  feedback TEXT,
  profile_json TEXT NOT NULL,
  spec_json TEXT NOT NULL,
  result_path TEXT NOT NULL,
  preview_json TEXT NOT NULL,
  validation_json TEXT NOT NULL,
  processed_rows INTEGER NOT NULL,
  unprocessed_rows INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(transformation_id, version)
);

CREATE TABLE IF NOT EXISTS audit_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT REFERENCES users(id),
  action TEXT NOT NULL,
  entity_type TEXT,
  entity_id TEXT,
  outcome TEXT NOT NULL,
  details_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_logs_created ON audit_logs(created_at DESC);
"""


def initialize_database() -> None:
    settings.storage_path.mkdir(parents=True, exist_ok=True)
    with connection() as db:
        db.executescript(SCHEMA)


def record_audit(
    action: str,
    *,
    user_id: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    outcome: str = "SUCCESS",
    details: dict[str, Any] | None = None,
) -> None:
    with connection() as db:
        db.execute(
            """
            INSERT INTO audit_logs
              (user_id, action, entity_type, entity_id, outcome, details_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, action, entity_type, entity_id, outcome, json_dump(details or {}), utc_now()),
        )

