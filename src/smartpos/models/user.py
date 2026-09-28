"""`users` and `activity_log`."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from smartpos.core.constants import Role
from smartpos.models.base import Entity


@dataclass
class User(Entity):
    username: str = ""
    full_name: str = ""
    password_hash: str = ""
    role: Role = Role.CASHIER
    warehouse_id: int | None = None
    is_active: bool = True
    last_login_at: datetime | None = None


@dataclass
class ActivityLogEntry(Entity):
    user_id: int | None = None
    action: str = ""
    entity: str = ""
    entity_id: int | None = None
    detail: str | None = None
