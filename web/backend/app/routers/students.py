from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, status

from ..config import Settings
from ..dependencies import get_settings
from ..schemas import StudentCreateRequest
from ..services import database_service

router = APIRouter(prefix="/students", tags=["students"])


@router.get("")
def get_students(settings: Settings = Depends(get_settings)):
    return database_service.list_students(Path(settings.db_path))


@router.post("", status_code=status.HTTP_201_CREATED)
def post_student(payload: StudentCreateRequest, settings: Settings = Depends(get_settings)):
    return database_service.create_student(Path(settings.db_path), payload.name, payload.card_uid)
