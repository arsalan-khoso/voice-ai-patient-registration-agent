"""Test fixtures: in-memory SQLite shared across threads, FastAPI dependency + webhook session overridden."""
import os

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("ENVIRONMENT", "test")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.db import Base, get_db, make_engine
from app.main import app
from app.routers import vapi as vapi_router


@pytest.fixture()
def session_factory(monkeypatch):
    # TEST_DATABASE_URL=postgresql://... runs the whole suite against real PostgreSQL (production engine).
    pg_url = os.environ.get("TEST_DATABASE_URL")
    if pg_url:
        engine = make_engine(pg_url.replace("postgresql://", "postgresql+psycopg://", 1))
        Base.metadata.drop_all(engine)
    else:
        engine = make_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def _get_db():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = _get_db
    monkeypatch.setattr(vapi_router, "SessionLocal", factory)
    yield factory
    app.dependency_overrides.clear()
    if pg_url:
        Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture()
def client(session_factory):
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def settings_override():
    """Temporarily change settings values (get_settings is cached, so mutate the instance)."""
    settings = get_settings()
    original = {}

    def _set(**kwargs):
        for key, value in kwargs.items():
            original.setdefault(key, getattr(settings, key))
            setattr(settings, key, value)

    yield _set
    for key, value in original.items():
        setattr(settings, key, value)


VALID_PATIENT = {
    "first_name": "Jane", "last_name": "Doe", "date_of_birth": "04/12/1985", "sex": "Female",
    "phone_number": "(415) 555-0134", "address_line_1": "123 Main St", "city": "Springfield",
    "state": "il", "zip_code": "62701",
}


@pytest.fixture()
def valid_patient():
    return dict(VALID_PATIENT)
