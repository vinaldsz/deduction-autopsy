import os

import pytest
from dotenv import load_dotenv

from semantic_layer.etl import build_db

load_dotenv()


@pytest.fixture(scope="session", autouse=True)
def _deductions_db(tmp_path_factory):
    """Build the relational store once and point DEDUCTIONS_DB at it for the whole test session.

    As of Layer 28 the MCP tools / FixtureLoader read the DB, so every in-process tool/agent/pipeline
    test needs one. Built into a temp dir (never the repo's data/deductions.db); the env var is what
    FixtureLoader resolves at call time.
    """
    db_path = tmp_path_factory.mktemp("deductions_db") / "deductions.db"
    build_db(db_path=db_path)
    previous = os.environ.get("DEDUCTIONS_DB")
    os.environ["DEDUCTIONS_DB"] = str(db_path)
    yield
    if previous is None:
        os.environ.pop("DEDUCTIONS_DB", None)
    else:
        os.environ["DEDUCTIONS_DB"] = previous
