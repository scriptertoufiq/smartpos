"""Shop profile and business defaults.

These live in the `settings` table rather than .env, because a shopkeeper
changes them from the Settings screen and they belong with the data, not with
the deployment. They are cached for the life of the process and reloaded when
something writes.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path

from smartpos.core.exceptions import ValidationError
from smartpos.core.logging import get_logger
from smartpos.db.connection import transaction
from smartpos.db.settings_repository import SettingsRepository
from smartpos.db.user_repository import ActivityLogRepository
from smartpos.services.auth_service import AuthService

log = get_logger(__name__)

MANAGE = "settings.manage"

# key -> (label, default). Anything not listed here is ignored on save, so a
# stray key cannot be written from the UI.
FIELDS: dict[str, tuple[str, str]] = {
    "shop_name": ("Shop name", "SmartPOS Retail"),
    "shop_address": ("Address", ""),
    "shop_phone": ("Phone", ""),
    "shop_logo": ("Logo file", ""),
    "currency_symbol": ("Currency symbol", "BDT"),
    "default_tax_rate": ("Default tax rate (%)", "0.00"),
    "invoice_footer": ("Invoice footer", "Thank you for shopping with us."),
    "idle_lock_minutes": ("Lock the screen after (minutes)", "15"),
}

_cache: dict[str, str] | None = None


class SettingsService:
    def __init__(self, auth: AuthService | None = None) -> None:
        self._repo = SettingsRepository()
        self._audit = ActivityLogRepository()
        self._auth = auth

    # ------------------------------------------------------------------ reads

    def all(self, refresh: bool = False) -> dict[str, str]:
        """Every setting, with defaults filled in for anything unset."""
        global _cache
        if _cache is None or refresh:
            stored = self._repo.all()
            _cache = {key: stored.get(key, default)
                      for key, (_label, default) in FIELDS.items()}
            # Keep anything the shop added by hand, so it is not silently lost.
            for key, value in stored.items():
                _cache.setdefault(key, value)
        return dict(_cache)

    def get(self, key: str, default: str = "") -> str:
        return self.all().get(key, default)

    def shop_profile(self) -> dict[str, str]:
        """The block an invoice prints at the top."""
        values = self.all()
        return {
            "name": values.get("shop_name") or "SmartPOS Retail",
            "address": values.get("shop_address", ""),
            "phone": values.get("shop_phone", ""),
            "currency": values.get("currency_symbol") or "BDT",
            "footer": values.get("invoice_footer", ""),
            "logo": values.get("shop_logo", ""),
        }

    def currency(self) -> str:
        return self.get("currency_symbol", "BDT")

    def default_tax_rate(self) -> Decimal:
        try:
            return Decimal(self.get("default_tax_rate", "0") or "0")
        except InvalidOperation:
            return Decimal("0")

    # ----------------------------------------------------------------- writes

    def save(self, values: dict[str, str]) -> None:
        """Write the fields that are known. Unknown keys are ignored."""
        actor = self._require()
        cleaned = {}
        for key, raw in values.items():
            if key not in FIELDS:
                continue
            cleaned[key] = self._validate(key, raw)

        if not cleaned:
            return

        with transaction() as conn:
            for key, value in cleaned.items():
                self._repo.put(key, value, conn)
            self._audit.record(actor.id, "settings.update", "settings", None,
                               cleaned, conn)

        self.all(refresh=True)
        log.info("Settings updated: %s", ", ".join(sorted(cleaned)))

    @staticmethod
    def invalidate() -> None:
        """Forget the cache. Used after a restore replaces the database."""
        global _cache
        _cache = None

    # -------------------------------------------------------------- internals

    def _validate(self, key: str, raw: str) -> str:
        value = (raw or "").strip()
        if key == "shop_name" and not value:
            raise ValidationError("The shop name cannot be empty — it prints on "
                                  "every invoice.")
        if key == "currency_symbol":
            if not value:
                raise ValidationError("A currency symbol is required.")
            if len(value) > 8:
                raise ValidationError("A currency symbol cannot exceed 8 characters.")
        if key == "default_tax_rate":
            try:
                rate = Decimal(value or "0")
            except InvalidOperation as exc:
                raise ValidationError(f"The tax rate must be a number, got {value!r}.") from exc
            if rate < 0 or rate > 100:
                raise ValidationError("The tax rate must be between 0 and 100.")
            return f"{rate:.2f}"
        if key == "idle_lock_minutes":
            if value and (not value.isdigit() or int(value) < 0):
                raise ValidationError("The idle lock must be a whole number of minutes.")
        if key == "shop_logo" and value and not Path(value).is_file():
            raise ValidationError(f"No file at {value}.")
        return value

    def _require(self):
        if self._auth is None:
            raise ValidationError(
                "This action needs a signed-in user; the service was created "
                "without one.")
        return self._auth.require(MANAGE)
