"""Test isolation.

`backend.config.get_settings()` is cached and reads `DATABASE_PATH` once, so the
environment variable has to be set before anything imports `backend`. That is
why it happens at module import time here rather than inside a fixture: pytest
loads `conftest.py` before it collects the test modules.

The result is that no test ever writes to the developer's real
`data/inboxpilot.db`.
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from pathlib import Path

import pytest

_TMP_DIR = tempfile.mkdtemp(prefix="inboxpilot-tests-")
atexit.register(shutil.rmtree, _TMP_DIR, True)

os.environ["DATABASE_PATH"] = os.path.join(_TMP_DIR, "session.db")


@pytest.fixture
def temp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the store at an empty database for one test.

    The settings object is a mutable pydantic model and `Settings.db_path` is
    computed on every call, so patching the attribute is enough. The store keys
    its "schema already created" cache on the resolved path, so a new file also
    means a fresh schema.
    """
    from backend import config

    db_file = tmp_path / "store.db"
    monkeypatch.setattr(config.get_settings(), "database_path", str(db_file))
    return db_file
