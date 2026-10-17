"""Overrides for the alerts test directory.

The root ``tests/conftest.py`` declares a session-scoped **autouse**
``db`` fixture that initialises the PostgreSQL database. The alerts
tests are deliberately database-free (in-memory SQLite / pure fakes)
and must stay runnable standalone without PostgreSQL, so this file
overrides the fixture with a no-op for everything under
``tests/modules/alerts/``.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session", autouse=True)
def db() -> object:  # noqa: ANN201 - matches the overridden fixture name
    yield
