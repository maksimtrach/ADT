"""Portable, declarative document memory. No executable code or workbook data.

The JSON library is the source of truth. Writes are atomic and process-locked;
imports never overwrite a revision and always start as private drafts.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.core.config import settings
from backend.app.core.errors import AppError
from backend.app.services.account_structure import MONEY_KEYS

MAX_PACKAGE_BYTES = 20 * 1024 * 1024


def now():
    return datetime.now(timezone.utc).isoformat()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MemoryCard(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    revision: int = Field(default=1, ge=1, le=10000, strict=True)
    schema_version: Literal[1] = 1
    document_type: Literal["trial-balance-general"] = "trial-balance-general"
    template_id: Literal["trial_balance_flat_v1"] = "trial_balance_flat_v1"
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(max_length=1500)
    # Relative header rows; physical column positions remain significant.
    header: list[list[Annotated[str, Field(max_length=200)]]] = Field(min_length=1, max_length=5)
    column_mapping: dict[str, Annotated[int, Field(ge=1, le=120, strict=True)]]
    row_mode: Literal["flat", "account_tree"]
    verified_rows: int = Field(ge=1, strict=True)
    independent_totals: bool
    created_at: str = Field(default_factory=now, max_length=50)

    @model_validator(mode="after")
    def valid_layout(self):
        allowed = {"account", "account_name", "subaccount", "analytics", *MONEY_KEYS}
        # A code-only tree (01 -> 01.01) is valid without a separate account-name
        # column. Replay derives classification from the optional mapping and
        # still revalidates the complete workbook.
        required = {"account", *MONEY_KEYS}
        positions = list(self.column_mapping.values())
        widths = {len(row) for row in self.header}
        if (not required.issubset(self.column_mapping) or not set(self.column_mapping).issubset(allowed)
                or len(set(positions)) != len(positions) or len(widths) != 1
                or not 1 <= next(iter(widths)) <= 120 or max(positions) > next(iter(widths))
                or not any(cell for row in self.header for cell in row)
                or sum(len(cell) for row in self.header for cell in row) > 16000):
            raise ValueError("Некорректная структура карточки памяти")
        return self

    def signature(self):
        return self.model_dump(mode="json", include={"schema_version", "document_type", "template_id",
                                                    "header", "column_mapping", "row_mode"})

    def digest(self):
        return hashlib.sha256(json.dumps(self.model_dump(mode="json"), sort_keys=True,
                                         ensure_ascii=False).encode()).hexdigest()


class MemoryRecord(StrictModel):
    card: MemoryCard
    owner_id: str = Field(max_length=100)
    status: Literal["draft", "approved", "disabled"] = "draft"
    scope: Literal["private", "shared"] = "private"
    approved_by: str | None = None
    successes: int = Field(default=0, ge=0)
    rejections: int = Field(default=0, ge=0)


class MemoryCase(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    owner_id: str
    transformation_id: str
    run_id: str
    outcome: Literal["resolved", "failed", "unsupported"]
    reason: str = Field(max_length=300)
    card_id: UUID | None = None
    card_revision: int | None = None
    created_at: str = Field(default_factory=now)


class Library(StrictModel):
    format: Literal["audit-document-memory"] = "audit-document-memory"
    format_version: Literal[1] = 1
    records: list[MemoryRecord] = Field(default_factory=list, max_length=500)
    cases: list[MemoryCase] = Field(default_factory=list, max_length=5000)

    @model_validator(mode="after")
    def unique_revisions(self):
        keys = [(r.card.id, r.card.revision) for r in self.records]
        if len(set(keys)) != len(keys):
            raise ValueError("Повторяющиеся версии карточек")
        return self


class MemoryPackage(StrictModel):
    format: Literal["audit-document-memory-export"] = "audit-document-memory-export"
    format_version: Literal[1] = 1
    cards: list[MemoryCard] = Field(max_length=500)


class MemoryStore:
    def __init__(self, root: Path | None = None):
        self.root = root or settings.document_memory_path
        self.path = self.root / "library.json"

    def read(self) -> Library:
        if not self.path.exists():
            return Library()
        try:
            if self.path.stat().st_size > 20 * 1024 * 1024:
                raise ValueError("Размер памяти превышен")
            return Library.model_validate_json(self.path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as error:
            raise AppError(409, "Хранилище памяти повреждено или имеет неподдерживаемую версию", "MEMORY_INVALID") from error

    @contextmanager
    def transaction(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / ".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            library = self.read()
            yield library
            # Validate the complete resulting library before replacing anything.
            payload = Library.model_validate(library.model_dump()).model_dump_json(indent=2)
            if len(payload.encode("utf-8")) > MAX_PACKAGE_BYTES:
                raise AppError(409, "Хранилище памяти превышает 20 МБ", "MEMORY_FULL")
            descriptor, name = tempfile.mkstemp(prefix=".library-", dir=self.root)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                    output.write(payload)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(name, self.path)
            finally:
                if os.path.exists(name):
                    os.unlink(name)

    def visible(self, owner: str, admin=False):
        return [r for r in self.read().records if admin or r.owner_id == owner or
                (r.scope == "shared" and r.status == "approved")]

    def active(self, owner: str):
        records = [r for r in self.read().records if r.owner_id == owner or r.scope == "shared"]
        # A newer draft/disabled revision suspends the old revision too.
        latest = {}
        for record in records:
            key = str(record.card.id)
            if key not in latest or record.card.revision > latest[key].card.revision:
                latest[key] = record
        return [r for r in latest.values() if r.status == "approved"]

    def save_case(self, case: MemoryCase, card: MemoryCard | None = None):
        with self.transaction() as library:
            if card:
                same = next((r for r in library.records if r.owner_id == case.owner_id
                             and r.card.signature() == card.signature()), None)
                if same:
                    card = same.card
                else:
                    if len(library.records) >= 500:
                        raise AppError(409, "Достигнут лимит 500 карточек памяти", "MEMORY_FULL")
                    related = [r for r in library.records if r.owner_id == case.owner_id
                               and r.card.header == card.header and r.card.template_id == card.template_id]
                    if related:
                        previous = max(related, key=lambda r: r.card.revision)
                        card = card.model_copy(update={"id": previous.card.id, "revision": previous.card.revision + 1})
                    library.records.append(MemoryRecord(card=card, owner_id=case.owner_id))
                case.card_id = card.id
                case.card_revision = card.revision
            library.cases = (library.cases + [case])[-5000:]
        return card

    def set_status(self, card_id: UUID, revision: int, status: str, scope: str, owner: str, admin=False):
        with self.transaction() as library:
            record = next((r for r in library.records if r.card.id == card_id and r.card.revision == revision
                           and (admin or r.owner_id == owner)), None)
            if not record:
                raise AppError(404, "Карточка не найдена", "NOT_FOUND")
            if (scope == "shared" or record.scope == "shared") and not admin:
                raise AppError(403, "Общими подходами управляет администратор", "FORBIDDEN")
            record.status, record.scope = status, scope
            record.approved_by = owner if status == "approved" else None
        return record

    def record_usage(self, card: MemoryCard, accepted: bool):
        with self.transaction() as library:
            for record in library.records:
                if record.card.id == card.id and record.card.revision == card.revision:
                    if accepted:
                        record.successes += 1
                    else:
                        record.rejections += 1

    def export(self, owner: str, admin=False):
        # No local user IDs, source files, run IDs, comments, or financial values.
        return MemoryPackage(cards=[r.card for r in self.visible(owner, admin)])

    def import_package(self, package: MemoryPackage, owner: str):
        imported = skipped = 0
        with self.transaction() as library:
            for card in package.cards:
                existing = next((r for r in library.records if r.card.id == card.id
                                 and r.card.revision == card.revision), None)
                if existing:
                    if existing.owner_id != owner or existing.card.digest() != card.digest():
                        raise AppError(409, "Конфликт идентификатора или содержимого версии; импорт отменён", "MEMORY_CONFLICT")
                    skipped += 1
                    continue
                # Prevent importing a new revision into somebody else's private family.
                if any(r.card.id == card.id and r.owner_id != owner for r in library.records):
                    raise AppError(409, "Семейство карточек принадлежит другому владельцу", "MEMORY_CONFLICT")
                library.records.append(MemoryRecord(card=card, owner_id=owner))
                imported += 1
            if len(library.records) > 500:
                raise AppError(409, "Достигнут лимит 500 карточек памяти", "MEMORY_FULL")
        return {"imported": imported, "skipped": skipped}
