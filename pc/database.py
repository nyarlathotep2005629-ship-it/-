"""Phase 1 and Phase 2 database operations for the RFID attendance system.

This module contains the database foundation, student/course/enrollment
management, session management, and Phase 2 attendance logic. Serial
communication and CLI handling remain outside this module.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterator


SCHEMA_PATH = Path(__file__).with_name("schema.sql")
VALID_UID_LENGTHS = frozenset({8, 14, 20})

ATTENDANCE_SUCCESS = "SUCCESS"
ATTENDANCE_ALREADY_REGISTERED = "ALREADY_REGISTERED"
ATTENDANCE_UNKNOWN_CARD = "UNKNOWN_CARD"
ATTENDANCE_NOT_ENROLLED = "NOT_ENROLLED"
ATTENDANCE_NO_ACTIVE_SESSION = "NO_ACTIVE_SESSION"
ATTENDANCE_INVALID_UID = "INVALID_UID"
ATTENDANCE_STORAGE_ERROR = "STORAGE_ERROR"


class DatabaseError(Exception):
    """Base class for errors that can be handled by the caller."""


class ValidationError(DatabaseError):
    """Raised when an input value does not satisfy the domain rules."""


class DuplicateError(DatabaseError):
    """Raised when an operation would create a duplicate record."""


class NotFoundError(DatabaseError):
    """Raised when a referenced student, course, or record does not exist."""


class EnrollmentLockedError(DatabaseError):
    """Raised when enrollment changes are blocked after a session exists."""


class ActiveSessionError(DatabaseError):
    """Raised when an operation would create a second OPEN session."""


class InvalidStateError(DatabaseError):
    """Raised when a record is in a state that cannot be changed."""


def connect_database(db_path: str | Path) -> sqlite3.Connection:
    """Open a SQLite connection and enable foreign-key enforcement.

    The connection uses autocommit mode so that write operations can
    explicitly reserve the database with BEGIN IMMEDIATE.
    """

    connection = sqlite3.connect(str(db_path), isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_schema(connection: sqlite3.Connection) -> None:
    """Create the Phase 1 schema without deleting existing data."""

    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    connection.executescript(schema)


def open_database(db_path: str | Path) -> sqlite3.Connection:
    """Open a database connection and initialize its tables and indexes."""

    connection = connect_database(db_path)
    try:
        initialize_schema(connection)
    except Exception:
        connection.close()
        raise
    return connection


def normalize_uid(raw_uid: str) -> str:
    """Normalize and validate a 4-, 7-, or 10-byte hexadecimal UID.

    The returned value is uppercase and retains leading zeroes.
    """

    if not isinstance(raw_uid, str):
        raise ValidationError("card_uid must be a string")

    normalized = raw_uid.strip().upper()
    if len(normalized) not in VALID_UID_LENGTHS:
        raise ValidationError("card_uid must contain 8, 14, or 20 hexadecimal characters")
    if re.fullmatch(r"[0-9A-F]+", normalized) is None:
        raise ValidationError("card_uid must contain only hexadecimal characters")
    return normalized


def _required_text(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValidationError(f"{field_name} must not be blank")
    return normalized


def _positive_id(value: int, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValidationError(f"{field_name} must be a positive integer")
    return value


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return None if row is None else dict(row)


def _exists(
    connection: sqlite3.Connection,
    table_name: str,
    column_name: str,
    value: int,
) -> bool:
    row = connection.execute(
        f"SELECT 1 FROM {table_name} WHERE {column_name} = ? LIMIT 1",
        (value,),
    ).fetchone()
    return row is not None


@contextmanager
def _write_transaction(connection: sqlite3.Connection) -> Iterator[None]:
    """Run a write operation under one IMMEDIATE transaction."""

    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
    except Exception:
        connection.rollback()
        raise
    else:
        connection.commit()


def _ensure_student_exists(
    connection: sqlite3.Connection,
    student_id: int,
) -> None:
    if not _exists(connection, "students", "student_id", student_id):
        raise NotFoundError(f"student_id {student_id} does not exist")


def _ensure_course_exists(
    connection: sqlite3.Connection,
    course_id: int,
) -> None:
    if not _exists(connection, "courses", "course_id", course_id):
        raise NotFoundError(f"course_id {course_id} does not exist")


def _get_session(
    connection: sqlite3.Connection,
    session_id: int,
) -> sqlite3.Row:
    row = connection.execute(
        """
        SELECT session_id, course_id, session_number, started_at, closed_at
        FROM sessions
        WHERE session_id = ?
        """,
        (session_id,),
    ).fetchone()
    if row is None:
        raise NotFoundError(f"session_id {session_id} does not exist")
    return row


def _ensure_enrollment_changes_allowed(
    connection: sqlite3.Connection,
    course_id: int,
) -> None:
    row = connection.execute(
        "SELECT 1 FROM sessions WHERE course_id = ? LIMIT 1",
        (course_id,),
    ).fetchone()
    if row is not None:
        raise EnrollmentLockedError(
            f"enrollment changes are locked for course_id {course_id}"
        )


def create_student(
    connection: sqlite3.Connection,
    name: str,
    card_uid: str,
) -> int:
    """Register a student and return the generated student_id."""

    normalized_name = _required_text(name, "name")
    normalized_uid = normalize_uid(card_uid)

    with _write_transaction(connection):
        existing = connection.execute(
            "SELECT student_id FROM students WHERE card_uid = ?",
            (normalized_uid,),
        ).fetchone()
        if existing is not None:
            raise DuplicateError(f"card_uid {normalized_uid} is already registered")

        try:
            cursor = connection.execute(
                "INSERT INTO students (name, card_uid) VALUES (?, ?)",
                (normalized_name, normalized_uid),
            )
        except sqlite3.IntegrityError as exc:
            raise DuplicateError("student registration violates a database constraint") from exc
        return int(cursor.lastrowid)


def list_students(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return all students ordered by student_id."""

    rows = connection.execute(
        "SELECT student_id, name, card_uid FROM students ORDER BY student_id"
    ).fetchall()
    return [dict(row) for row in rows]


def create_course(
    connection: sqlite3.Connection,
    course_name: str,
) -> int:
    """Register a course and return the generated course_id."""

    normalized_name = _required_text(course_name, "course_name")

    with _write_transaction(connection):
        cursor = connection.execute(
            "INSERT INTO courses (course_name) VALUES (?)",
            (normalized_name,),
        )
        return int(cursor.lastrowid)


def list_courses(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return all courses ordered by course_id."""

    rows = connection.execute(
        "SELECT course_id, course_name FROM courses ORDER BY course_id"
    ).fetchall()
    return [dict(row) for row in rows]


def enroll_student(
    connection: sqlite3.Connection,
    student_id: int,
    course_id: int,
) -> bool:
    """Enroll a student in a course.

    The existence check, session-lock check, duplicate check, and INSERT are
    performed in the same write transaction.
    """

    student_id = _positive_id(student_id, "student_id")
    course_id = _positive_id(course_id, "course_id")

    with _write_transaction(connection):
        _ensure_student_exists(connection, student_id)
        _ensure_course_exists(connection, course_id)
        _ensure_enrollment_changes_allowed(connection, course_id)

        existing = connection.execute(
            """
            SELECT 1
            FROM enrollments
            WHERE student_id = ? AND course_id = ?
            """,
            (student_id, course_id),
        ).fetchone()
        if existing is not None:
            raise DuplicateError(
                f"student_id {student_id} is already enrolled in course_id {course_id}"
            )

        try:
            connection.execute(
                """
                INSERT INTO enrollments (student_id, course_id)
                VALUES (?, ?)
                """,
                (student_id, course_id),
            )
        except sqlite3.IntegrityError as exc:
            raise DuplicateError("enrollment violates a database constraint") from exc
        return True


def unenroll_student(
    connection: sqlite3.Connection,
    student_id: int,
    course_id: int,
) -> bool:
    """Remove an enrollment before the course receives its first session."""

    student_id = _positive_id(student_id, "student_id")
    course_id = _positive_id(course_id, "course_id")

    with _write_transaction(connection):
        _ensure_student_exists(connection, student_id)
        _ensure_course_exists(connection, course_id)
        _ensure_enrollment_changes_allowed(connection, course_id)

        existing = connection.execute(
            """
            SELECT 1
            FROM enrollments
            WHERE student_id = ? AND course_id = ?
            """,
            (student_id, course_id),
        ).fetchone()
        if existing is None:
            raise NotFoundError(
                f"student_id {student_id} is not enrolled in course_id {course_id}"
            )

        connection.execute(
            """
            DELETE FROM enrollments
            WHERE student_id = ? AND course_id = ?
            """,
            (student_id, course_id),
        )
        return True


def list_enrollments(
    connection: sqlite3.Connection,
    course_id: int,
) -> list[dict[str, Any]]:
    """Return enrolled students for one course."""

    course_id = _positive_id(course_id, "course_id")
    _ensure_course_exists(connection, course_id)

    rows = connection.execute(
        """
        SELECT s.student_id, s.name, s.card_uid
        FROM enrollments AS e
        JOIN students AS s ON s.student_id = e.student_id
        WHERE e.course_id = ?
        ORDER BY s.student_id
        """,
        (course_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def start_session(
    connection: sqlite3.Connection,
    course_id: int,
) -> dict[str, Any]:
    """Create the next OPEN session for a course."""

    course_id = _positive_id(course_id, "course_id")

    with _write_transaction(connection):
        _ensure_course_exists(connection, course_id)

        open_session = connection.execute(
            "SELECT session_id FROM sessions WHERE closed_at IS NULL LIMIT 1"
        ).fetchone()
        if open_session is not None:
            raise ActiveSessionError("an OPEN session already exists")

        next_number = connection.execute(
            """
            SELECT COALESCE(MAX(session_number), 0) + 1
            FROM sessions
            WHERE course_id = ?
            """,
            (course_id,),
        ).fetchone()[0]
        started_at = _utc_now()

        try:
            cursor = connection.execute(
                """
                INSERT INTO sessions
                    (course_id, session_number, started_at, closed_at)
                VALUES (?, ?, ?, NULL)
                """,
                (course_id, int(next_number), started_at),
            )
        except sqlite3.IntegrityError as exc:
            raise ActiveSessionError("could not create an OPEN session") from exc

        row = connection.execute(
            """
            SELECT session_id, course_id, session_number, started_at, closed_at
            FROM sessions
            WHERE session_id = ?
            """,
            (cursor.lastrowid,),
        ).fetchone()
        return dict(row)


def get_open_session(
    connection: sqlite3.Connection,
) -> dict[str, Any] | None:
    """Return the current OPEN session, or None if no session is open."""

    row = connection.execute(
        """
        SELECT session_id, course_id, session_number, started_at, closed_at
        FROM sessions
        WHERE closed_at IS NULL
        ORDER BY session_id
        LIMIT 1
        """
    ).fetchone()
    return _row_to_dict(row)


def close_session(
    connection: sqlite3.Connection,
    session_id: int,
) -> bool:
    """Close an OPEN session and preserve its start timestamp."""

    session_id = _positive_id(session_id, "session_id")

    with _write_transaction(connection):
        row = connection.execute(
            """
            SELECT session_id, closed_at
            FROM sessions
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError(f"session_id {session_id} does not exist")
        if row["closed_at"] is not None:
            raise InvalidStateError(f"session_id {session_id} is already closed")

        connection.execute(
            "UPDATE sessions SET closed_at = ? WHERE session_id = ?",
            (_utc_now(), session_id),
        )
        return True


def register_attendance(
    connection: sqlite3.Connection,
    session_id: int,
    card_uid: str,
) -> dict[str, Any]:
    """Register one student's attendance and return a status dictionary.

    Expected business outcomes are returned as status values. SQLite errors
    are rolled back by the transaction context and returned as STORAGE_ERROR.
    """

    try:
        normalized_uid = normalize_uid(card_uid)
    except ValidationError:
        return {
            "status": ATTENDANCE_INVALID_UID,
            "session_id": session_id,
            "card_uid": card_uid,
        }

    try:
        session_id = _positive_id(session_id, "session_id")
    except ValidationError:
        return {
            "status": ATTENDANCE_NO_ACTIVE_SESSION,
            "session_id": session_id,
            "card_uid": normalized_uid,
        }

    try:
        with _write_transaction(connection):
            session = connection.execute(
                """
                SELECT session_id, course_id, session_number, started_at, closed_at
                FROM sessions
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
            if session is None or session["closed_at"] is not None:
                return {
                    "status": ATTENDANCE_NO_ACTIVE_SESSION,
                    "session_id": session_id,
                    "card_uid": normalized_uid,
                }

            student = connection.execute(
                """
                SELECT student_id, name, card_uid
                FROM students
                WHERE card_uid = ?
                """,
                (normalized_uid,),
            ).fetchone()
            if student is None:
                return {
                    "status": ATTENDANCE_UNKNOWN_CARD,
                    "session_id": session_id,
                    "card_uid": normalized_uid,
                }

            enrolled = connection.execute(
                """
                SELECT 1
                FROM enrollments
                WHERE student_id = ? AND course_id = ?
                """,
                (student["student_id"], session["course_id"]),
            ).fetchone()
            student_details = {
                "student_id": student["student_id"],
                "student_name": student["name"],
                "card_uid": normalized_uid,
                "session_id": session_id,
            }
            if enrolled is None:
                return {
                    "status": ATTENDANCE_NOT_ENROLLED,
                    **student_details,
                }

            existing = connection.execute(
                """
                SELECT scanned_at
                FROM attendance
                WHERE session_id = ? AND student_id = ?
                """,
                (session_id, student["student_id"]),
            ).fetchone()
            if existing is not None:
                return {
                    "status": ATTENDANCE_ALREADY_REGISTERED,
                    "scanned_at": existing["scanned_at"],
                    **student_details,
                }

            scanned_at = _utc_now()
            connection.execute(
                """
                INSERT INTO attendance (session_id, student_id, scanned_at)
                VALUES (?, ?, ?)
                """,
                (session_id, student["student_id"], scanned_at),
            )
            return {
                "status": ATTENDANCE_SUCCESS,
                "scanned_at": scanned_at,
                **student_details,
            }
    except sqlite3.Error as exc:
        return {
            "status": ATTENDANCE_STORAGE_ERROR,
            "session_id": session_id,
            "card_uid": normalized_uid,
            "error": str(exc),
        }


def get_attendance_report(
    connection: sqlite3.Connection,
    session_id: int,
) -> list[dict[str, Any]]:
    """Return attendance status for every enrolled student in a session."""

    session_id = _positive_id(session_id, "session_id")
    session = _get_session(connection, session_id)

    rows = connection.execute(
        """
        SELECT
            s.student_id,
            s.name,
            CASE
                WHEN a.student_id IS NOT NULL THEN 'PRESENT'
                WHEN se.closed_at IS NULL THEN 'PENDING'
                ELSE 'ABSENT'
            END AS attendance_status,
            a.scanned_at
        FROM sessions AS se
        JOIN enrollments AS e
            ON e.course_id = se.course_id
        JOIN students AS s
            ON s.student_id = e.student_id
        LEFT JOIN attendance AS a
            ON a.session_id = se.session_id
            AND a.student_id = s.student_id
        WHERE se.session_id = ?
        ORDER BY s.student_id
        """,
        (session["session_id"],),
    ).fetchall()
    return [dict(row) for row in rows]


def get_attendance_summary(
    connection: sqlite3.Connection,
    session_id: int,
) -> dict[str, int]:
    """Return enrollment and attendance counts for one session."""

    report = get_attendance_report(connection, session_id)
    summary = {
        "enrolled_count": len(report),
        "present_count": 0,
        "pending_count": 0,
        "absent_count": 0,
    }
    for row in report:
        if row["attendance_status"] == "PRESENT":
            summary["present_count"] += 1
        elif row["attendance_status"] == "PENDING":
            summary["pending_count"] += 1
        else:
            summary["absent_count"] += 1
    return summary


def list_sessions(
    connection: sqlite3.Connection,
    course_id: int,
) -> list[dict[str, Any]]:
    """Return the session history for one course."""

    course_id = _positive_id(course_id, "course_id")
    _ensure_course_exists(connection, course_id)

    rows = connection.execute(
        """
        SELECT
            session_id,
            session_number,
            started_at,
            closed_at,
            CASE
                WHEN closed_at IS NULL THEN 'OPEN'
                ELSE 'CLOSED'
            END AS status
        FROM sessions
        WHERE course_id = ?
        ORDER BY session_number
        """,
        (course_id,),
    ).fetchall()
    return [dict(row) for row in rows]


__all__ = [
    "ActiveSessionError",
    "ATTENDANCE_ALREADY_REGISTERED",
    "ATTENDANCE_INVALID_UID",
    "ATTENDANCE_NO_ACTIVE_SESSION",
    "ATTENDANCE_NOT_ENROLLED",
    "ATTENDANCE_STORAGE_ERROR",
    "ATTENDANCE_SUCCESS",
    "ATTENDANCE_UNKNOWN_CARD",
    "DatabaseError",
    "DuplicateError",
    "EnrollmentLockedError",
    "InvalidStateError",
    "NotFoundError",
    "ValidationError",
    "close_session",
    "connect_database",
    "create_course",
    "create_student",
    "enroll_student",
    "get_open_session",
    "get_attendance_report",
    "get_attendance_summary",
    "initialize_schema",
    "list_courses",
    "list_enrollments",
    "list_students",
    "list_sessions",
    "normalize_uid",
    "open_database",
    "register_attendance",
    "start_session",
    "unenroll_student",
]
