"""SQLAlchemy engine, session factory, and declarative base."""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(settings.database_url, future=True, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def init_db() -> None:
    """Create tables. A take-home convenience; production would use migrations."""
    from app.db import models  # noqa: F401  (register models on Base.metadata)

    Base.metadata.create_all(engine)


def get_session():
    """FastAPI dependency: a Session per request, closed at the end."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
