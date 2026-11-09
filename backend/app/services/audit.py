from typing import Any

from sqlalchemy.orm import Session

from ..models import AuditLog


def record_audit(
    db: Session,
    dairy_id: int,
    user_id: int | None,
    action: str,
    entity_type: str,
    entity_id: int | str,
    details: dict[str, Any] | None = None,
) -> AuditLog:
    entry = AuditLog(
        dairy_id=dairy_id,
        user_id=user_id,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id),
        details=details or {},
    )
    db.add(entry)
    return entry
