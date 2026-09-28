"""Shared pytest fixtures.

Tests that need a database should run against a throwaway schema, not the
development one -- point DB_NAME at `smartpos_test` before running them.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session")
def settings():
    """Load settings from the environment for the test session."""
    pytest.skip("Settings loading is not implemented yet.")


@pytest.fixture
def db_connection(settings):
    """A connection to the test database, rolled back after each test."""
    pytest.skip("Database layer is not implemented yet.")
