"""Shared test fixtures.

The `db` fixture gives each test a Session wrapped in a transaction that is rolled
back at teardown, so DB-backed tests stay isolated and leave nothing behind. Only
tests that request `db` touch Postgres; the pure-domain tests do not.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import settings
from app.db import models  # noqa: F401  (register models)
from app.db.base import Base


@pytest.fixture(scope="session")
def engine():
    eng = create_engine(settings.database_url, future=True)
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture
def db(engine):
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
