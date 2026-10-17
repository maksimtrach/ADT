from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from backend.app.api.dependencies import admin_user
from backend.app.db.database import connection, json_load
from backend.app.models.domain import AuditLogItem, UserPublic


router = APIRouter(prefix="/audit-log", tags=["audit-log"])


@router.get("")
def list_audit_log(
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
    action: str | None = Query(default=None, max_length=100),
    user: UserPublic = Depends(admin_user),
):
    where = "WHERE a.action = ?" if action else ""
    params = [action] if action else []
    offset = (page - 1) * page_size
    with connection() as db:
        total = db.execute(f"SELECT COUNT(*) FROM audit_logs a {where}", params).fetchone()[0]
        rows = db.execute(
            f"""
            SELECT a.*, u.name AS user_name, u.email AS user_email
            FROM audit_logs a LEFT JOIN users u ON u.id = a.user_id
            {where}
            ORDER BY a.created_at DESC LIMIT ? OFFSET ?
            """,
            [*params, page_size, offset],
        ).fetchall()
    data = []
    for row in rows:
        item = dict(row)
        item["details"] = json_load(item.pop("details_json"), {})
        data.append(AuditLogItem.model_validate(item))
    return {"data": data, "meta": {"page": page, "pageSize": page_size, "total": total}}

