"""ローカル専用FastAPIアプリケーション。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

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
from .routers import courses, health, rfid, sessions, students
from .services.rfid_runtime import RfidRuntimeController
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
        app.state.runtime_controller = RfidRuntimeController(resolved_settings)
        yield
        app.state.runtime_controller.shutdown()

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

    # Vite build成果物が存在するときだけFastAPIから表示する。
    frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if frontend_dist.is_dir():
        app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
    return app


app = create_app()
