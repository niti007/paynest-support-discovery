"""/livez must not touch the database (ALB health check on an empty DB); /readyz reports the database (503 until bootstrap)."""
from fastapi.testclient import TestClient

from solution.a7.api.app import create_app

DEAD_DSN = "postgresql://app_user:x@127.0.0.1:1/a7?connect_timeout=1"      # nothing listens on port 1


def test_livez_is_up_and_readyz_is_503_without_a_database():
    app = create_app(token="t", dsn=DEAD_DSN, admin_dsn=DEAD_DSN, admin_token="a")
    with TestClient(app) as c:
        assert c.get("/livez").json() == {"status": "ok"}
        r = c.get("/readyz")
        assert r.status_code == 503 and r.json() == {"status": "not_ready"}
        assert c.get("/livez").status_code == 200          # still alive: the ALB keeps the target in service
