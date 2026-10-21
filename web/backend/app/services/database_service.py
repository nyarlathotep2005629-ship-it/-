"""既存pc.databaseを呼び出すWeb用サービス。スキーマは変更しない。"""

from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Any

from pc import database


def _open(db_path: Path) -> sqlite3.Connection:
    connection = database.open_database(db_path)
    connection.execute("PRAGMA busy_timeout = 3000")
    return connection


def _one(connection: sqlite3.Connection, query: str, *params: Any) -> dict[str, Any] | None:
    row = connection.execute(query, params).fetchone()
    return dict(row) if row is not None else None


def list_students(db_path: Path) -> list[dict[str, Any]]:
    connection = _open(db_path)
    try:
        return database.list_students(connection)
    finally:
        connection.close()


def create_student(db_path: Path, name: str, card_uid: str) -> dict[str, Any]:
    connection = _open(db_path)
    try:
        student_id = database.create_student(connection, name, card_uid)
        return _one(
            connection,
            "SELECT student_id, name, card_uid FROM students WHERE student_id = ?",
            student_id,
        ) or {}
    finally:
        connection.close()


def list_courses(db_path: Path) -> list[dict[str, Any]]:
    connection = _open(db_path)
    try:
        return database.list_courses(connection)
    finally:
        connection.close()


def create_course(db_path: Path, course_name: str) -> dict[str, Any]:
    connection = _open(db_path)
    try:
        course_id = database.create_course(connection, course_name)
        return _one(
            connection,
            "SELECT course_id, course_name FROM courses WHERE course_id = ?",
            course_id,
        ) or {}
    finally:
        connection.close()


def list_enrollments(db_path: Path, course_id: int) -> list[dict[str, Any]]:
    connection = _open(db_path)
    try:
        return database.list_enrollments(connection, course_id)
    finally:
        connection.close()


def enroll_student(db_path: Path, student_id: int, course_id: int) -> bool:
    connection = _open(db_path)
    try:
        return database.enroll_student(connection, student_id, course_id)
    finally:
        connection.close()


def unenroll_student(db_path: Path, student_id: int, course_id: int) -> bool:
    connection = _open(db_path)
    try:
        return database.unenroll_student(connection, student_id, course_id)
    finally:
        connection.close()


def get_open_session(db_path: Path) -> dict[str, Any] | None:
    connection = _open(db_path)
    try:
        return database.get_open_session(connection)
    finally:
        connection.close()


def start_session(db_path: Path, course_id: int) -> dict[str, Any]:
    connection = _open(db_path)
    try:
        return database.start_session(connection, course_id)
    finally:
        connection.close()


def close_session(db_path: Path, session_id: int) -> bool:
    connection = _open(db_path)
    try:
        return database.close_session(connection, session_id)
    finally:
        connection.close()


def list_sessions(db_path: Path, course_id: int) -> list[dict[str, Any]]:
    connection = _open(db_path)
    try:
        return database.list_sessions(connection, course_id)
    finally:
        connection.close()


def get_attendance(db_path: Path, session_id: int) -> list[dict[str, Any]]:
    connection = _open(db_path)
    try:
        return database.get_attendance_report(connection, session_id)
    finally:
        connection.close()


def get_summary(db_path: Path, session_id: int) -> dict[str, int]:
    connection = _open(db_path)
    try:
        return database.get_attendance_summary(connection, session_id)
    finally:
        connection.close()


def register_attendance(db_path: Path, session_id: int, card_uid: str) -> dict[str, Any]:
    connection = _open(db_path)
    try:
        result = database.register_attendance(connection, session_id, card_uid)
        result.pop("card_uid", None)
        result.pop("error", None)
        return result
    finally:
        connection.close()
