"""Run against a separate local test DB. Missing dependencies/skipped tests are failures."""

import io
import logging
import os
import sys
import unittest
from pathlib import Path


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import psycopg
    import sqlalchemy.ext.asyncio
    from alembic.config import Config
    from sqlalchemy.engine import make_url
    from sqlalchemy.pool import NullPool

    from alembic import command
    from infrastructure.config import settings

    url = make_url(settings.database_url)
    if url.host != "postgres" or os.environ.get("DEPLOYMENT_MODE") != "local":
        raise SystemExit("Tests require the isolated local compose stack")
    with psycopg.connect(url.render_as_string(hide_password=False), autocommit=True) as db:
        exists = db.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", ("agentguard_security_test",)
        ).fetchone()
        if not exists:
            db.execute("CREATE DATABASE agentguard_security_test")
    settings.database_url = url.set(database="agentguard_security_test").render_as_string(
        hide_password=False
    )
    os.environ["DATABASE_URL"] = settings.database_url
    settings.rate_limit_enabled = False
    os.environ["RATE_LIMIT_ENABLED"] = "false"
    command.upgrade(Config("alembic.ini"), "head")

    # Legacy unittest modules create independent event loops. Do not share pooled connections.
    original_create_engine = sqlalchemy.ext.asyncio.create_async_engine
    sqlalchemy.ext.asyncio.create_async_engine = lambda *a, **kw: original_create_engine(
        *a, **{**kw, "poolclass": NullPool}
    )
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk" / "python"))
    modules = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--modules=")), None)
    if modules:
        suite = unittest.defaultTestLoader.loadTestsFromNames(modules.split(","))
    elif "--all" in sys.argv:
        suite = unittest.defaultTestLoader.discover("tests")
    else:
        suite = unittest.defaultTestLoader.loadTestsFromNames(
            ["tests.test_identity_contract", "tests.test_security_integration"]
        )
    logging.getLogger("agentguard.http").setLevel(logging.CRITICAL)

    class ProgressResult(unittest.TextTestResult):
        def startTest(self, test):
            super().startTest(test)
            print("RUN", test.id(), flush=True)

    result = unittest.TextTestRunner(
        stream=io.StringIO(), verbosity=0, resultclass=ProgressResult
    ).run(suite)
    print(
        f"Ran {result.testsRun}; failures={len(result.failures)}; "
        f"errors={len(result.errors)}; skipped={len(result.skipped)}"
    )
    for test, trace in result.failures + result.errors:
        print("FAILED", test.id())
        # Never print assertions containing raw credentials or provider payloads.
        for line in trace.splitlines():
            if line.lstrip().startswith("File ") and "/app/" in line:
                print(line)
    for test, _reason in result.skipped:
        print("SKIPPED", test.id())
    return 0 if result.wasSuccessful() and not result.skipped else 1


if __name__ == "__main__":
    raise SystemExit(main())
