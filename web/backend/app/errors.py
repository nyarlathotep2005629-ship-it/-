"""APIエラーの共通形式と既存DB例外の安全な変換。"""

from __future__ import annotations

from dataclasses import dataclass
import sqlite3
from uuid import uuid4

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from pc.database import (
    ActiveSessionError,
    DatabaseError,
    DuplicateError,
    EnrollmentLockedError,
    InvalidStateError,
    NotFoundError,
    ValidationError,
)
from pc.reader import ReaderConnectionError, ReaderError, ReaderInitializationError


@dataclass
class ApiError(Exception):
    code: str
    status_code: int
    message: str
    retryable: bool = False


def db_error_to_api(exc: DatabaseError) -> ApiError:
    if isinstance(exc, ValidationError):
        return ApiError("VALIDATION_ERROR", 422, "入力値を確認してください")
    if isinstance(exc, DuplicateError):
        return ApiError("DUPLICATE", 409, "既に登録されています")
    if isinstance(exc, EnrollmentLockedError):
        return ApiError("ENROLLMENT_LOCKED", 409, "授業回開始後は履修を変更できません")
    if isinstance(exc, ActiveSessionError):
        return ApiError("ACTIVE_SESSION", 409, "既に受付中の授業回があります")
    if isinstance(exc, InvalidStateError):
        return ApiError("SESSION_NOT_OPEN", 409, "対象授業回は受付中ではありません")
    if isinstance(exc, NotFoundError):
        return ApiError("NOT_FOUND", 404, "指定されたデータが見つかりません")
    return ApiError("STORAGE_ERROR", 500, "データベース処理に失敗しました")


def reader_error_to_api(exc: ReaderError) -> ApiError:
    if isinstance(exc, ReaderInitializationError):
        return ApiError("RFID_NOT_READY", 503, "ArduinoからREADYを受信できませんでした", True)
    if isinstance(exc, ReaderConnectionError):
        return ApiError("SERIAL_OPEN_FAILED", 503, "シリアルポートを利用できません", True)
    return ApiError("SERIAL_READ_FAILED", 503, "RFIDリーダーとの通信に失敗しました", True)


def error_response(error: ApiError, request_id: str | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code,
        content={
            "code": error.code,
            "message": error.message,
            "request_id": request_id or str(uuid4()),
            "retryable": error.retryable,
        },
    )


async def api_error_handler(_: Request, exc: ApiError) -> JSONResponse:
    return error_response(exc)


async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    return error_response(ApiError("VALIDATION_ERROR", 422, "入力値を確認してください"))


async def database_error_handler(_: Request, exc: DatabaseError) -> JSONResponse:
    return error_response(db_error_to_api(exc))


async def reader_error_handler(_: Request, exc: ReaderError) -> JSONResponse:
    return error_response(reader_error_to_api(exc))


async def sqlite_error_handler(_: Request, exc: sqlite3.Error) -> JSONResponse:
    return error_response(ApiError("DATABASE_BUSY", 503, "データベースが使用中です", True))
