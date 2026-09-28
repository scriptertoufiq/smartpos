"""Checks the scaffold holds together: every module imports and the package
metadata is present. Real behaviour tests replace this as modules get built."""

from __future__ import annotations

import importlib
import pkgutil

import smartpos


def test_version_is_set():
    assert smartpos.__version__
    assert smartpos.__app_name__ == "SmartPOS"


def test_every_module_imports():
    """Import every module in the package so a typo cannot sit unnoticed.

    The UI modules are skipped because importing them needs PyQt5 and, on some
    systems, a display.
    """
    failed = []
    for mod in pkgutil.walk_packages(smartpos.__path__, prefix="smartpos."):
        if ".ui" in mod.name:
            continue
        try:
            importlib.import_module(mod.name)
        except Exception as exc:  # noqa: BLE001 - we want the whole list
            failed.append(f"{mod.name}: {exc}")
    assert not failed, "modules failed to import:\n" + "\n".join(failed)
