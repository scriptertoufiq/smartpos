"""Gapless document numbers.

`MAX(invoice_no) + 1` is neither gapless nor safe when two counters bill at the
same moment. These numbers are allocated by locking a counter row inside the
document's own transaction, so a rolled-back document gives its number back.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from smartpos.core.exceptions import DatabaseError
from smartpos.db.repository import BaseRepository

# doc_type -> the prefix used when a period's row has to be created.
DEFAULT_PREFIXES = {
    "sale": "INV-",
    "hold": "HLD-",
    "purchase": "PUR-",
    "sale_return": "RET-",
    "transfer": "TRF-",
}


class SequenceRepository(BaseRepository):
    table = "document_sequences"

    def allocate(self, doc_type: str, conn: Any, on: date | None = None) -> str:
        """Take the next number for `doc_type`. Requires an open transaction.

        `conn` is mandatory: allocating in a transaction of its own would hand
        out a number that survives the caller rolling back, which is exactly
        the gap this table exists to prevent.
        """
        if conn is None:
            raise DatabaseError(
                "A document number must be allocated inside the document's own "
                "transaction.")

        period = str((on or date.today()).year)

        row = self.fetch_one(
            "SELECT prefix, next_number, padding FROM document_sequences "
            "WHERE doc_type = %s AND period = %s FOR UPDATE",
            (doc_type, period), conn)

        if row is None:
            # First document of a new year. Carry the prefix and padding over
            # from any previous period so numbering style stays consistent.
            previous = self.fetch_one(
                "SELECT prefix, padding FROM document_sequences "
                "WHERE doc_type = %s ORDER BY period DESC LIMIT 1",
                (doc_type,), conn)
            prefix = previous["prefix"] if previous else DEFAULT_PREFIXES.get(doc_type, "")
            padding = previous["padding"] if previous else 6
            self.execute(
                "INSERT INTO document_sequences (doc_type, period, prefix, "
                "next_number, padding) VALUES (%s, %s, %s, 1, %s)",
                (doc_type, period, prefix, padding), conn)
            row = self.fetch_one(
                "SELECT prefix, next_number, padding FROM document_sequences "
                "WHERE doc_type = %s AND period = %s FOR UPDATE",
                (doc_type, period), conn)

        number = int(row["next_number"])
        self.execute(
            "UPDATE document_sequences SET next_number = next_number + 1 "
            "WHERE doc_type = %s AND period = %s", (doc_type, period), conn)

        return f"{row['prefix']}{period}-{str(number).zfill(int(row['padding']))}"

    def peek(self, doc_type: str, on: date | None = None) -> str | None:
        """What the next number would be, without taking it. For display only."""
        period = str((on or date.today()).year)
        row = self.fetch_one(
            "SELECT prefix, next_number, padding FROM document_sequences "
            "WHERE doc_type = %s AND period = %s", (doc_type, period))
        if row is None:
            prefix = DEFAULT_PREFIXES.get(doc_type, "")
            return f"{prefix}{period}-{'1'.zfill(6)}"
        return (f"{row['prefix']}{period}-"
                f"{str(int(row['next_number'])).zfill(int(row['padding']))}")
