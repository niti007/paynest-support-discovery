"""One-off AWS provisioning task: schema + role passwords + checkpoint tables, then reference data and KB chunks.

    python -m solution.db.provision [--no-embed]

Owner connection from A7_DB_HOST + A7_DB_OWNER_USER/A7_DB_OWNER_PASSWORD (RDS master secret, injected by ECS).
Role passwords come from A7_APP_USER_PASSWORD / A7_ACT_PASSWORD / A7_ADMIN_PASSWORD (ECS secrets); on AWS the dev defaults are
never applied (A7_ENV must not be local). Every step is idempotent, so re-running after a deploy is safe. Prints counts only.
"""
from __future__ import annotations

import sys

from solution.db import bootstrap
from solution.ingest import load_kb, load_reference


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    bootstrap.bootstrap()
    print("bootstrap complete", flush=True)
    load_reference.main()
    sys.argv = [sys.argv[0], *argv]          # load_kb parses --embed / --no-embed
    load_kb.main()


if __name__ == "__main__":
    main()
