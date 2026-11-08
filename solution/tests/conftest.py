import pytest

from solution.a7 import db


@pytest.fixture(scope="session")
def owner():
    """Superuser connection (bypasses RLS). Skips if the compose stack is not up."""
    try:
        conn = db.connect(autocommit=True, connect_timeout=3)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"postgres not reachable: {e}")
    yield conn
    conn.close()


@pytest.fixture()
def app():
    """app_user connection (RLS enforced)."""
    conn = db.connect(app=True, autocommit=True)
    yield conn
    conn.close()
