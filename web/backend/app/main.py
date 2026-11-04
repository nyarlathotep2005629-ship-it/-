"""ローカル専用FastAPIアプリケーション。"""

from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import Settings, settings_from_environment
from .errors import (
    api_error_handler,
    database_error_handler,
    reader_error_handler,
    sqlite_error_handler,
    validation_error_handler,
)
from .routers import courses, health, rfid, sessions, students, devices
from .services.rfid_runtime import RfidRuntimeController
from .services import device_sync
from .services.demo_seed import ensure_demo_data
from pc import database
from .dependencies import get_settings
from pc.database import DatabaseError
from pc.reader import ReaderError
import sqlite3
from fastapi.exceptions import RequestValidationError
from .errors import ApiError


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or settings_from_environment()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        resolved_settings.db_path.parent.mkdir(parents=True, exist_ok=True)
        log_dir = resolved_settings.db_path.parent / "Logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(log_dir / "application.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        root_logger = logging.getLogger()
        root_logger.addHandler(file_handler)
        connection = database.open_database(resolved_settings.db_path)
        try:
            device_sync.migrate_device_schema(connection)
        finally:
            connection.close()
        if resolved_settings.demo_mode:
            ensure_demo_data(resolved_settings.db_path)
        app.state.runtime_controller = RfidRuntimeController(resolved_settings)
        stop_sync = asyncio.Event()

        async def sync_loop():
            while not stop_sync.is_set():
                if resolved_settings.device_id and resolved_settings.device_token:
                    try:
                        await asyncio.to_thread(device_sync.sync_once, resolved_settings.db_path,
                            device_sync.EspHttpClient(resolved_settings.device_base_url, resolved_settings.device_id,
                                resolved_settings.device_token, resolved_settings.device_timeout))
                    except Exception:
                        logging.getLogger(__name__).exception("ESP32 background sync failed")
                try:
                    await asyncio.wait_for(stop_sync.wait(), timeout=2.0)
                except TimeoutError:
                    pass

        sync_task = asyncio.create_task(sync_loop())
        yield
        stop_sync.set()
        sync_task.cancel()
        try:
            await sync_task
        except asyncio.CancelledError:
            pass
        app.state.runtime_controller.shutdown()
        root_logger.removeHandler(file_handler)
        file_handler.close()

    app = FastAPI(
        title="RFID出欠管理システム Web API",
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )
    # Factoryで明示されたDBを全ルーターへ伝播させる。テストでは必ず一時DBを使う。
    app.dependency_overrides[get_settings] = lambda: resolved_settings
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[resolved_settings.frontend_origin, "http://localhost:5173"],
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type"],
    )
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(DatabaseError, database_error_handler)
    app.add_exception_handler(ReaderError, reader_error_handler)
    app.add_exception_handler(sqlite3.Error, sqlite_error_handler)

    app.include_router(health.router, prefix="/api/v1")
    app.include_router(students.router, prefix="/api/v1")
    app.include_router(courses.router, prefix="/api/v1")
    app.include_router(sessions.router, prefix="/api/v1")
    app.include_router(rfid.router, prefix="/api/v1")
    app.include_router(devices.router, prefix="/api/v1")

    # Vite build成果物が存在するときだけFastAPIから表示する。
    frozen_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[3]))
    frontend_dist = frozen_root / "web" / "frontend" / "dist"
    if not frontend_dist.is_dir():
        frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if frontend_dist.is_dir():
        app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
    return app


app = create_app()
