"""FastAPI依存関係。DB接続はリクエスト単位で短時間だけ保持する。"""

from __future__ import annotations

from collections.abc import Iterator
import sqlite3

from fastapi import Request

from .config import Settings, settings_from_environment
from pc.database import open_database


def get_settings() -> Settings:
    return settings_from_environment()


def get_db(settings: Settings) -> Iterator[sqlite3.Connection]:
    connection = open_database(settings.db_path)
    try:
        connection.execute("PRAGMA busy_timeout = 3000")
        yield connection
    finally:
        connection.close()


def get_runtime_controller(request: Request):
    return request.app.state.runtime_controller
