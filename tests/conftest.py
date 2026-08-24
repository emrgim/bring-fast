import importlib
import sys

import pytest


@pytest.fixture()
def bf(tmp_path, monkeypatch):
    """A fresh Bring Fast app on an empty database, per test."""
    monkeypatch.setenv("BRINGFAST_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("BRINGFAST_SECRET", "test-secret")
    for name in ("bring_fast.app", "bring_fast.db", "bring_fast.checkout", "bring_fast.catalog", "bring_fast.purchases", "bring_fast.compare"):
        sys.modules.pop(name, None)
    db = importlib.import_module("bring_fast.db")
    app_module = importlib.import_module("bring_fast.app")
    db.connect().close()
    return app_module


@pytest.fixture()
def client(bf):
    from fastapi.testclient import TestClient

    return TestClient(bf.app)
