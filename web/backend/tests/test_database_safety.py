from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import PROJECT_ROOT, Settings
from app.main import create_app


def test_web_test_database_path_is_not_production_database(tmp_path: Path) -> None:
    production_db = (PROJECT_ROOT / "attendance.db").resolve()
    test_db = (tmp_path / "safety.db").resolve()
    assert test_db != production_db
    production_wal = production_db.parent / "attendance.db-wal"
    production_shm = production_db.parent / "attendance.db-shm"
    wal_before = production_wal.exists()
    shm_before = production_shm.exists()

    app = create_app(Settings(db_path=test_db))
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/students",
            json={"name": "DB保護確認", "card_uid": "A1B2C3D4"},
        )

    assert response.status_code == 201
    assert production_wal.exists() is wal_before
    assert production_shm.exists() is shm_before
