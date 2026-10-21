from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, status

from ..config import Settings
from ..dependencies import get_runtime_controller, get_settings
from ..errors import ApiError
from ..schemas import MockScanRequest, RuntimeStartRequest, SessionCreateRequest
from ..services import database_service
from ..services.rfid_runtime import RfidRuntimeController

router = APIRouter(tags=["sessions", "attendance"])


@router.get("/sessions/open")
def get_open_session(settings: Settings = Depends(get_settings)):
    return database_service.get_open_session(Path(settings.db_path))


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
def post_session(payload: SessionCreateRequest, settings: Settings = Depends(get_settings)):
    return database_service.start_session(Path(settings.db_path), payload.course_id)


@router.post("/sessions/{session_id}/close")
def post_close_session(
    session_id: int,
    controller: RfidRuntimeController = Depends(get_runtime_controller),
):
    return controller.close_session(session_id)


@router.get("/sessions/{session_id}/attendance")
def get_attendance(session_id: int, settings: Settings = Depends(get_settings)):
    return database_service.get_attendance(Path(settings.db_path), session_id)


@router.get("/sessions/{session_id}/summary")
def get_summary(session_id: int, settings: Settings = Depends(get_settings)):
    return database_service.get_summary(Path(settings.db_path), session_id)


@router.post("/attendance/runtime/start", status_code=status.HTTP_202_ACCEPTED)
def start_attendance_runtime(
    payload: RuntimeStartRequest,
    settings: Settings = Depends(get_settings),
    controller: RfidRuntimeController = Depends(get_runtime_controller),
):
    open_session = database_service.get_open_session(Path(settings.db_path))
    if open_session is None or open_session["session_id"] != payload.session_id:
        raise ApiError("SESSION_NOT_OPEN", 409, "対象授業回は受付中ではありません")
    return controller.start_attendance(payload.session_id, payload.mode, payload.port)


@router.post("/attendance/runtime/scan")
def scan_mock(
    payload: MockScanRequest,
    settings: Settings = Depends(get_settings),
    controller: RfidRuntimeController = Depends(get_runtime_controller),
):
    open_session = database_service.get_open_session(Path(settings.db_path))
    if open_session is None:
        raise ApiError("SESSION_NOT_OPEN", 409, "対象授業回は受付中ではありません")
    return controller.scan_mock(open_session["session_id"], payload.card_uid)
