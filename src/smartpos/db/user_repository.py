"""Queries against `users`, and the audit trail in `activity_log`."""

from __future__ import annotations

import json
from typing import Any

from smartpos.core.constants import Role
from smartpos.db.repository import BaseRepository, Row

_USER_COLUMNS = (
    "id, username, full_name, password_hash, role, warehouse_id, "
    "is_active, last_login_at, created_at, updated_at"
)


class UserRepository(BaseRepository):
    table = "users"

    # ------------------------------------------------------------------ reads

    def get(self, user_id: int, conn: Any = None) -> Row | None:
        return self.fetch_one(
            f"SELECT {_USER_COLUMNS} FROM users WHERE id = %s", (user_id,), conn
        )

    def get_by_username(self, username: str, conn: Any = None) -> Row | None:
        """Usernames are compared case-insensitively, as MySQL's default
        collation already does -- so 'Admin' and 'admin' are one account."""
        return self.fetch_one(
            f"SELECT {_USER_COLUMNS} FROM users WHERE username = %s", (username,), conn
        )

    def list_all(
        self,
        include_inactive: bool = True,
        role: Role | None = None,
        search: str | None = None,
        conn: Any = None,
    ) -> list[Row]:
        sql = [f"SELECT {_USER_COLUMNS} FROM users WHERE 1 = 1"]
        params: list[Any] = []
        if not include_inactive:
            sql.append("AND is_active = 1")
        if role is not None:
            sql.append("AND role = %s")
            params.append(str(role))
        if search:
            sql.append("AND (username LIKE %s OR full_name LIKE %s)")
            like = f"%{search}%"
            params.extend([like, like])
        sql.append("ORDER BY is_active DESC, username")
        return self.fetch_all(" ".join(sql), tuple(params), conn)

    def username_taken(self, username: str, exclude_id: int | None = None,
                       conn: Any = None) -> bool:
        sql = "SELECT 1 FROM users WHERE username = %s"
        params: list[Any] = [username]
        if exclude_id is not None:
            sql += " AND id <> %s"
            params.append(exclude_id)
        return self.fetch_one(sql + " LIMIT 1", tuple(params), conn) is not None

    def count_active_admins(self, exclude_id: int | None = None, conn: Any = None) -> int:
        """How many enabled admins remain. Guards against locking everyone out."""
        sql = "SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND is_active = 1"
        params: list[Any] = []
        if exclude_id is not None:
            sql += " AND id <> %s"
            params.append(exclude_id)
        return int(self.fetch_value(sql, tuple(params), conn) or 0)

    def has_activity(self, user_id: int, conn: Any = None) -> bool:
        """True if any document still points at this user.

        Every such foreign key is ON DELETE SET NULL, so a delete would succeed
        and quietly strip the attribution -- which is why it is refused.
        """
        checks = (
            "SELECT 1 FROM sales WHERE user_id = %s LIMIT 1",
            "SELECT 1 FROM purchases WHERE user_id = %s LIMIT 1",
            "SELECT 1 FROM sale_returns WHERE user_id = %s LIMIT 1",
            "SELECT 1 FROM stock_movements WHERE user_id = %s LIMIT 1",
            "SELECT 1 FROM stock_transfers WHERE user_id = %s LIMIT 1",
            "SELECT 1 FROM customer_payments WHERE user_id = %s LIMIT 1",
            "SELECT 1 FROM supplier_payments WHERE user_id = %s LIMIT 1",
        )
        return any(self.fetch_one(sql, (user_id,), conn) for sql in checks)

    # ----------------------------------------------------------------- writes

    def create(self, data: dict[str, Any], conn: Any = None) -> int:
        return self.insert(data, conn)

    def update_fields(self, user_id: int, data: dict[str, Any], conn: Any = None) -> bool:
        return self.update(user_id, data, conn)

    def set_active(self, user_id: int, active: bool, conn: Any = None) -> bool:
        return self.update(user_id, {"is_active": 1 if active else 0}, conn)

    def set_password(self, user_id: int, password_hash: str, conn: Any = None) -> bool:
        return self.update(user_id, {"password_hash": password_hash}, conn)

    def touch_last_login(self, user_id: int, conn: Any = None) -> None:
        self.execute(
            "UPDATE users SET last_login_at = NOW() WHERE id = %s", (user_id,), conn
        )


class ActivityLogRepository(BaseRepository):
    """Append-only audit trail. Nothing here ever updates or deletes."""

    table = "activity_log"

    def record(
        self,
        user_id: int | None,
        action: str,
        entity: str,
        entity_id: int | None = None,
        detail: dict[str, Any] | None = None,
        conn: Any = None,
    ) -> int:
        return self.insert(
            {
                "user_id": user_id,
                "action": action,
                "entity": entity,
                "entity_id": entity_id,
                "detail": json.dumps(detail, default=str) if detail else None,
            },
            conn,
        )

    def for_entity(self, entity: str, entity_id: int, limit: int = 50,
                   conn: Any = None) -> list[Row]:
        return self.fetch_all(
            "SELECT a.*, u.username FROM activity_log a "
            "LEFT JOIN users u ON u.id = a.user_id "
            "WHERE a.entity = %s AND a.entity_id = %s "
            "ORDER BY a.created_at DESC LIMIT %s",
            (entity, entity_id, limit),
            conn,
        )

    def recent(self, limit: int = 100, conn: Any = None) -> list[Row]:
        return self.fetch_all(
            "SELECT a.*, u.username FROM activity_log a "
            "LEFT JOIN users u ON u.id = a.user_id "
            "ORDER BY a.created_at DESC LIMIT %s",
            (limit,),
            conn,
        )
