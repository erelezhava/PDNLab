"""Shared test setup.

Every pdnlab-dc command puts a hard memory ceiling on its own process. A command called
from a test runs inside pytest, so the ceiling would stay on pytest for the rest of the
session; it is replaced here by a recorder (the real ceiling is tested in a child
process, tests/test_run.py). ``ceiling_calls`` lists the limits the commands asked for.
"""

import pytest


@pytest.fixture(autouse=True)
def ceiling_calls(monkeypatch):
    import pdnlab_dc.memory
    calls = []

    def record(limit):
        calls.append(limit)
        return "ceiling skipped in tests"

    monkeypatch.setattr(pdnlab_dc.memory, "apply_ceiling", record)
    return calls
