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


@pytest.fixture
def api_client(engine):
    """A TestClient whose DB writes happen inside a transaction that is rolled
    back at teardown. Endpoint commits become savepoints, so the API can commit
    normally while the test leaves the database clean."""
    from fastapi.testclient import TestClient

    from app.db.base import get_session
    from app.domain.rates import RateProvider
    from app.main import create_app

    connection = engine.connect()
    transaction = connection.begin()

    def override_session():
        session = Session(bind=connection, join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            session.close()

    app = create_app(provider=RateProvider.seeded())
    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as client:
        yield client
    transaction.rollback()
    connection.close()
