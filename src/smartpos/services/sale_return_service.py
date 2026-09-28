"""Returns against an existing sale."""

from __future__ import annotations

from datetime import date

from smartpos.models.sale_return import SaleReturn


class SaleReturnService:
    def create(self, sale_return: SaleReturn) -> int:
        """Save the return, put the quantity back into stock, settle the refund.

        Validates that no line returns more than was sold, counting quantities
        already returned on earlier returns against the same invoice.
        """
        raise NotImplementedError

    def get(self, return_id: int) -> SaleReturn | None:
        raise NotImplementedError

    def list(self, date_from: date | None = None, date_to: date | None = None) -> list[SaleReturn]:
        raise NotImplementedError

    def returnable_quantities(self, sale_id: int) -> dict[int, object]:
        """Per sale_item_id, how much may still be returned."""
        raise NotImplementedError

    def next_reference_no(self) -> str:
        raise NotImplementedError
