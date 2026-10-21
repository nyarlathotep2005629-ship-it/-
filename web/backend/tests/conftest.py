from __future__ import annotations

from pathlib import Path
import sys

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "web" / "backend"))

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402


@pytest.fixture()
def client(tmp_path: Path):
    app = create_app(Settings(db_path=tmp_path / "test.db", event_buffer_size=20))
    with TestClient(app) as test_client:
        yield test_client
