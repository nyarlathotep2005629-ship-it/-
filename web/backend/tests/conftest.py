from __future__ import annotations

import asyncio
from pathlib import Path
import sys

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PRODUCTION_DB = (PROJECT_ROOT / "attendance.db").resolve()
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "web" / "backend"))

if sys.platform == "win32":
    # Windowsのテスト環境でTestClientの自己パイプ生成が停止しないよう、
    # アプリ本体ではなくテストプロセスだけSelector event loopを使用する。
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402


@pytest.fixture()
def client(tmp_path: Path):
    db_path = (tmp_path / "test.db").resolve()
    if db_path == PRODUCTION_DB:
        pytest.fail("テストが本番DB attendance.db を使用しようとしました")
    app = create_app(Settings(db_path=db_path, event_buffer_size=20))
    with TestClient(app) as test_client:
        yield test_client
