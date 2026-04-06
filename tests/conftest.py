import os

import pytest
from fastapi.testclient import TestClient

os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = "sqlite:///./test_codegraphrag.db"
os.environ["SYNC_MOCK_MODE"] = "true"
os.environ["ENABLE_EXTERNAL_STORES"] = "false"
os.environ["REQUIRE_NEO4J"] = "false"
os.environ["REQUIRE_QDRANT"] = "false"

from app.db.base import Base  # noqa: E402
from app.db.session import engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def reset_db() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as c:
        yield c
