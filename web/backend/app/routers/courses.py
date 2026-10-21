from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, status

from ..config import Settings
from ..dependencies import get_settings
from ..schemas import CourseCreateRequest, EnrollmentCreateRequest
from ..services import database_service

router = APIRouter(prefix="/courses", tags=["courses"])


@router.get("")
def get_courses(settings: Settings = Depends(get_settings)):
    return database_service.list_courses(Path(settings.db_path))


@router.post("", status_code=status.HTTP_201_CREATED)
def post_course(payload: CourseCreateRequest, settings: Settings = Depends(get_settings)):
    return database_service.create_course(Path(settings.db_path), payload.course_name)


@router.get("/{course_id}/enrollments")
def get_enrollments(course_id: int, settings: Settings = Depends(get_settings)):
    return database_service.list_enrollments(Path(settings.db_path), course_id)


@router.post("/{course_id}/enrollments", status_code=status.HTTP_201_CREATED)
def post_enrollment(
    course_id: int,
    payload: EnrollmentCreateRequest,
    settings: Settings = Depends(get_settings),
):
    database_service.enroll_student(Path(settings.db_path), payload.student_id, course_id)
    return {"student_id": payload.student_id, "course_id": course_id, "enrolled": True}


@router.delete("/{course_id}/enrollments/{student_id}")
def delete_enrollment(course_id: int, student_id: int, settings: Settings = Depends(get_settings)):
    database_service.unenroll_student(Path(settings.db_path), student_id, course_id)
    return {"student_id": student_id, "course_id": course_id, "enrolled": False}


@router.get("/{course_id}/sessions")
def get_sessions(course_id: int, settings: Settings = Depends(get_settings)):
    return database_service.list_sessions(Path(settings.db_path), course_id)
