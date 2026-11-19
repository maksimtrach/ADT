"""Temporary, user-owned XLSX uploads used for source previews and processing."""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import UploadFile
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from backend.app.core.config import settings
from backend.app.core.errors import AppError
from backend.app.services.file_profiler import safe_value, validate_xlsx


PREVIEW_ROWS = 100
PREVIEW_COLUMNS = 120
UPLOAD_TTL_SECONDS = 24 * 60 * 60


def _root() -> Path:
    return settings.storage_path / "_source_uploads"


def _directory(upload_id: str) -> Path:
    try:
        if str(UUID(upload_id)) != upload_id:
            raise ValueError("non-canonical UUID")
    except ValueError as error:
        raise AppError(404, "Предварительно загруженный файл не найден", "UPLOAD_NOT_FOUND") from error
    return _root() / upload_id


def cleanup_expired_uploads() -> None:
    root = _root()
    if not root.exists():
        return
    cutoff = time.time() - UPLOAD_TTL_SECONDS
    for directory in root.iterdir():
        if directory.is_symlink() or not directory.is_dir():
            continue
        try:
            if str(UUID(directory.name)) != directory.name or directory.stat().st_mtime >= cutoff:
                continue
        except (ValueError, OSError):
            continue
        shutil.rmtree(directory)


def owned_upload(upload_id: str, user_id: str) -> tuple[Path, dict]:
    directory = _directory(upload_id)
    try:
        metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
        if metadata["user_id"] != user_id or not (directory / "source.xlsx").is_file():
            raise AppError(404, "Предварительно загруженный файл не найден", "UPLOAD_NOT_FOUND")
        if time.time() - metadata["created_at"] > UPLOAD_TTL_SECONDS:
            raise AppError(410, "Срок хранения предварительно загруженного файла истёк", "UPLOAD_EXPIRED")
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise AppError(404, "Предварительно загруженный файл не найден", "UPLOAD_NOT_FOUND") from error
    return directory, metadata


def discard_upload(upload_id: str, user_id: str) -> None:
    directory, _ = owned_upload(upload_id, user_id)
    shutil.rmtree(directory)


def source_preview(upload_id: str, user_id: str, sheet_name: str | None = None) -> dict:
    directory, metadata = owned_upload(upload_id, user_id)
    try:
        workbook = load_workbook(directory / "source.xlsx", read_only=True, data_only=True, keep_links=False)
        try:
            sheets = workbook.sheetnames
            if sheet_name is not None and sheet_name not in sheets:
                raise AppError(404, "Лист книги не найден", "SHEET_NOT_FOUND")
            sheet = workbook[sheet_name] if sheet_name else workbook.active
            total_rows = sheet.max_row or 0
            total_columns = sheet.max_column or 0
            width = min(max(total_columns, 1), PREVIEW_COLUMNS)
            rows = []
            last_filled_column = 0
            for row_number, values in enumerate(sheet.iter_rows(
                min_row=1, max_row=min(total_rows, PREVIEW_ROWS), max_col=width, values_only=True,
            ), 1):
                cells = [safe_value(value) for value in values]
                last_filled_column = max(last_filled_column, max(
                    (index + 1 for index, value in enumerate(cells) if value is not None), default=0,
                ))
                rows.append({"row": row_number, "cells": cells})
            shown_columns = max(1, last_filled_column) if rows else 0
            for row in rows:
                row["cells"] = row["cells"][:shown_columns]
            return {
                "upload_id": upload_id,
                "filename": metadata["filename"],
                "sheets": sheets,
                "sheet_name": sheet.title,
                "columns": [get_column_letter(index) for index in range(1, shown_columns + 1)],
                "rows": rows,
                "total_rows": total_rows,
                "total_columns": total_columns,
                "columns_truncated": total_columns > PREVIEW_COLUMNS,
            }
        finally:
            workbook.close()
    except AppError:
        raise
    except Exception as error:
        raise AppError(400, "Не удалось прочитать исходный XLSX-файл", "INVALID_XLSX") from error


async def stage_upload(file: UploadFile, user_id: str) -> dict:
    filename = Path(file.filename or "").name
    if not filename or Path(filename).suffix.lower() != ".xlsx":
        await file.close()
        raise AppError(400, "Поддерживаются только файлы XLSX", "UNSUPPORTED_FILE")
    cleanup_expired_uploads()
    root = _root()
    root.mkdir(parents=True, exist_ok=True)
    upload_id = str(uuid4())
    directory = root / upload_id
    directory.mkdir()
    size = 0
    try:
        with (directory / "source.xlsx").open("wb") as destination:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise AppError(413, "Файл превышает допустимый размер", "FILE_TOO_LARGE")
                destination.write(chunk)
        validate_xlsx(directory / "source.xlsx", settings.max_upload_bytes)
        (directory / "metadata.json").write_text(json.dumps({
            "user_id": user_id, "filename": filename, "size_bytes": size, "created_at": time.time(),
        }, ensure_ascii=False), encoding="utf-8")
        return source_preview(upload_id, user_id)
    except Exception:
        shutil.rmtree(directory)
        raise
    finally:
        await file.close()


def consume_upload(upload_id: str, user_id: str, destination: Path) -> tuple[Path, dict]:
    directory, metadata = owned_upload(upload_id, user_id)
    try:
        os.replace(directory / "source.xlsx", destination)
    except FileNotFoundError as error:
        raise AppError(409, "Файл уже передан в обработку", "UPLOAD_CONSUMED") from error
    return directory, metadata
