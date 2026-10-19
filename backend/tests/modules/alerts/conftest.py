"""Explicit opt-in for the B01 live PostgreSQL migration checks.

``test_b01_alerts_migration.PostgresLiveChecksCase`` runs real-PG
assertions only when ``B01_LIVE_PG_TESTS=1``. This conftest (in B's
own test tree) turns that flag on for this module, so the checks run
wherever a PostgreSQL server is reachable — locally against a
developer PG and in CI ``test-backend`` against the compose PG — and
skip cleanly where none is.

The safety boundary is structural, not just the flag: the case creates
its own dedicated throwaway database (``b01_live_pg_test``) on the
configured server, migrates and probes ONLY that database, and drops
it in teardown. The configured application database is never written
to, so a business URL cannot be polluted even with the flag on. Set
``B01_LIVE_PG_TESTS=0`` to opt out explicitly.
"""

from __future__ import annotations

import os

os.environ.setdefault("B01_LIVE_PG_TESTS", "1")
