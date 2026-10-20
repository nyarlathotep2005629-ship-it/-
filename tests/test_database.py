from __future__ import annotations

from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from pc.database import (  # noqa: E402
    ActiveSessionError,
    ATTENDANCE_ALREADY_REGISTERED,
    ATTENDANCE_INVALID_UID,
    ATTENDANCE_NO_ACTIVE_SESSION,
    ATTENDANCE_NOT_ENROLLED,
    ATTENDANCE_SUCCESS,
    ATTENDANCE_UNKNOWN_CARD,
    DuplicateError,
    EnrollmentLockedError,
    NotFoundError,
    ValidationError,
    close_session,
    create_course,
    create_student,
    enroll_student,
    get_attendance_report,
    get_attendance_summary,
    get_open_session,
    list_courses,
    list_enrollments,
    list_students,
    list_sessions,
    normalize_uid,
    open_database,
    register_attendance,
    start_session,
    unenroll_student,
)


class DatabaseTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.connection = open_database(self.db_path)

    def tearDown(self) -> None:
        self.connection.close()
        self.temp_dir.cleanup()

    def add_student(self, name: str = "山田 太郎", uid: str = "04A1B2C3") -> int:
        return create_student(self.connection, name, uid)

    def add_course(self, name: str = "電子工学") -> int:
        return create_course(self.connection, name)

    def test_schema_initialization_and_foreign_keys(self) -> None:
        table_names = {
            row[0]
            for row in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        self.assertTrue(
            {"students", "courses", "enrollments", "sessions", "attendance"}
            <= table_names
        )
        self.assertEqual(
            self.connection.execute("PRAGMA foreign_keys").fetchone()[0],
            1,
        )

    def test_foreign_key_constraint_is_enforced(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "INSERT INTO enrollments (student_id, course_id) VALUES (?, ?)",
                (999, 999),
            )

    def test_student_registration_and_listing(self) -> None:
        student_id = self.add_student("  山田 太郎  ", " 04a1b2c3 ")
        self.assertEqual(
            list_students(self.connection),
            [
                {
                    "student_id": student_id,
                    "name": "山田 太郎",
                    "card_uid": "04A1B2C3",
                }
            ],
        )

    def test_duplicate_uid_is_rejected(self) -> None:
        self.add_student()
        with self.assertRaises(DuplicateError):
            self.add_student("佐藤 花子", "04A1B2C3")

    def test_uid_format_is_validated(self) -> None:
        self.assertEqual(normalize_uid(" 04a1b2c3 "), "04A1B2C3")
        self.assertEqual(normalize_uid("04A1B2C3D4E5F6"), "04A1B2C3D4E5F6")
        self.assertEqual(normalize_uid("04A1B2C3D4E5F6071829"), "04A1B2C3D4E5F6071829")

        for invalid_uid in ("", "123", "04A1ZZ99", "04A1B2C3D4E5"):
            with self.subTest(invalid_uid=invalid_uid):
                with self.assertRaises(ValidationError):
                    normalize_uid(invalid_uid)

    def test_uid_check_constraint_rejects_invalid_direct_inserts(self) -> None:
        valid_uids = (
            "00A1B2C3",
            "00010203040506",
            "00010203040506070809",
        )
        for index, uid in enumerate(valid_uids, start=1):
            self.connection.execute(
                "INSERT INTO students (name, card_uid) VALUES (?, ?)",
                (f"有効UID{index}", uid),
            )

        stored_uids = [
            row[0]
            for row in self.connection.execute(
                "SELECT card_uid FROM students ORDER BY student_id"
            )
        ]
        self.assertEqual(stored_uids, list(valid_uids))

        invalid_uids = (
            None,
            "00a1B2C3",
            "00A1B2Cz",
            "00A1B2C",
            "00A1B2C3D4E5",
            "00A1B2C3D4E5F6071829AA",
        )
        for index, uid in enumerate(invalid_uids, start=1):
            with self.subTest(uid=uid):
                with self.assertRaises(sqlite3.IntegrityError):
                    self.connection.execute(
                        "INSERT INTO students (name, card_uid) VALUES (?, ?)",
                        (f"無効UID{index}", uid),
                    )

    def test_blank_student_name_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            create_student(self.connection, "   ", "04A1B2C3")

    def test_course_registration_and_duplicate_names(self) -> None:
        first_id = self.add_course("電子工学")
        second_id = self.add_course("電子工学")
        self.assertNotEqual(first_id, second_id)
        self.assertEqual(
            [course["course_name"] for course in list_courses(self.connection)],
            ["電子工学", "電子工学"],
        )

    def test_blank_course_name_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            create_course(self.connection, "   ")

    def test_enrollment_listing_and_duplicate_rejection(self) -> None:
        student_id = self.add_student()
        course_id = self.add_course()

        self.assertTrue(enroll_student(self.connection, student_id, course_id))
        self.assertEqual(
            list_enrollments(self.connection, course_id),
            [
                {
                    "student_id": student_id,
                    "name": "山田 太郎",
                    "card_uid": "04A1B2C3",
                }
            ],
        )
        with self.assertRaises(DuplicateError):
            enroll_student(self.connection, student_id, course_id)

    def test_unenrollment(self) -> None:
        student_id = self.add_student()
        course_id = self.add_course()
        enroll_student(self.connection, student_id, course_id)

        self.assertTrue(unenroll_student(self.connection, student_id, course_id))
        self.assertEqual(list_enrollments(self.connection, course_id), [])
        with self.assertRaises(NotFoundError):
            unenroll_student(self.connection, student_id, course_id)

    def test_missing_student_and_course_are_rejected(self) -> None:
        with self.assertRaises(NotFoundError):
            enroll_student(self.connection, 999, 999)

        student_id = self.add_student()
        with self.assertRaises(NotFoundError):
            enroll_student(self.connection, student_id, 999)

        course_id = self.add_course()
        with self.assertRaises(NotFoundError):
            list_enrollments(self.connection, course_id + 999)

    def test_first_session_locks_enrollment_changes(self) -> None:
        student_id = self.add_student()
        second_student_id = create_student(
            self.connection,
            "佐藤 花子",
            "04D4E5F6",
        )
        course_id = self.add_course()
        enroll_student(self.connection, student_id, course_id)
        session = start_session(self.connection, course_id)

        self.assertEqual(session["session_number"], 1)
        with self.assertRaises(EnrollmentLockedError):
            enroll_student(self.connection, second_student_id, course_id)
        with self.assertRaises(EnrollmentLockedError):
            unenroll_student(self.connection, student_id, course_id)

    def test_only_one_open_session_is_allowed_by_schema(self) -> None:
        first_course_id = self.add_course("電子工学")
        second_course_id = self.add_course("情報工学")
        first_session = start_session(self.connection, first_course_id)
        self.assertEqual(get_open_session(self.connection), first_session)

        with self.assertRaises(ActiveSessionError):
            start_session(self.connection, second_course_id)

        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                """
                INSERT INTO sessions
                    (course_id, session_number, started_at, closed_at)
                VALUES (?, ?, ?, NULL)
                """,
                (second_course_id, 1, "2026-10-20T00:00:00.000+00:00"),
            )

    def test_attendance_registration_statuses_and_duplicate_timestamp(self) -> None:
        enrolled_student_id = self.add_student()
        other_student_id = create_student(
            self.connection,
            "佐藤 花子",
            "04D4E5F6",
        )
        course_id = self.add_course()
        enroll_student(self.connection, enrolled_student_id, course_id)
        session_id = start_session(self.connection, course_id)["session_id"]

        success = register_attendance(
            self.connection,
            session_id,
            "04a1b2c3",
        )
        self.assertEqual(success["status"], ATTENDANCE_SUCCESS)
        self.assertEqual(success["student_id"], enrolled_student_id)
        self.assertEqual(success["student_name"], "山田 太郎")
        self.assertEqual(success["card_uid"], "04A1B2C3")
        first_scanned_at = success["scanned_at"]

        duplicate = register_attendance(
            self.connection,
            session_id,
            "04A1B2C3",
        )
        self.assertEqual(duplicate["status"], ATTENDANCE_ALREADY_REGISTERED)
        self.assertEqual(duplicate["scanned_at"], first_scanned_at)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM attendance"
            ).fetchone()[0],
            1,
        )

        unknown_card = register_attendance(
            self.connection,
            session_id,
            "00112233",
        )
        self.assertEqual(unknown_card["status"], ATTENDANCE_UNKNOWN_CARD)

        not_enrolled = register_attendance(
            self.connection,
            session_id,
            "04D4E5F6",
        )
        self.assertEqual(not_enrolled["status"], ATTENDANCE_NOT_ENROLLED)
        self.assertEqual(not_enrolled["student_id"], other_student_id)

        invalid_uid = register_attendance(
            self.connection,
            session_id,
            "INVALID",
        )
        self.assertEqual(invalid_uid["status"], ATTENDANCE_INVALID_UID)

        close_session(self.connection, session_id)
        closed = register_attendance(
            self.connection,
            session_id,
            "04A1B2C3",
        )
        self.assertEqual(closed["status"], ATTENDANCE_NO_ACTIVE_SESSION)

    def test_attendance_report_and_summary(self) -> None:
        first_student_id = self.add_student()
        second_student_id = create_student(
            self.connection,
            "佐藤 花子",
            "04D4E5F6",
        )
        course_id = self.add_course()
        enroll_student(self.connection, first_student_id, course_id)
        enroll_student(self.connection, second_student_id, course_id)
        session_id = start_session(self.connection, course_id)["session_id"]

        register_attendance(self.connection, session_id, "04A1B2C3")
        open_report = get_attendance_report(self.connection, session_id)
        self.assertEqual(
            [(row["student_id"], row["attendance_status"]) for row in open_report],
            [
                (first_student_id, "PRESENT"),
                (second_student_id, "PENDING"),
            ],
        )
        self.assertIsNotNone(open_report[0]["scanned_at"])
        self.assertIsNone(open_report[1]["scanned_at"])
        self.assertEqual(
            get_attendance_summary(self.connection, session_id),
            {
                "enrolled_count": 2,
                "present_count": 1,
                "pending_count": 1,
                "absent_count": 0,
            },
        )

        close_session(self.connection, session_id)
        closed_report = get_attendance_report(self.connection, session_id)
        self.assertEqual(closed_report[1]["attendance_status"], "ABSENT")
        self.assertEqual(
            get_attendance_summary(self.connection, session_id),
            {
                "enrolled_count": 2,
                "present_count": 1,
                "pending_count": 0,
                "absent_count": 1,
            },
        )

    def test_attendance_trigger_requires_enrollment_for_each_session(self) -> None:
        enrolled_student_id = self.add_student()
        other_course_student_id = create_student(
            self.connection,
            "佐藤 花子",
            "04D4E5F6",
        )
        first_course_id = self.add_course("電子工学")
        second_course_id = self.add_course("情報工学")
        enroll_student(self.connection, enrolled_student_id, first_course_id)
        enroll_student(self.connection, other_course_student_id, second_course_id)

        first_session_id = start_session(self.connection, first_course_id)["session_id"]
        self.connection.execute(
            """
            INSERT INTO attendance (session_id, student_id, scanned_at)
            VALUES (?, ?, ?)
            """,
            (first_session_id, enrolled_student_id, "2026-10-20T00:00:00.000+00:00"),
        )

        with self.assertRaisesRegex(sqlite3.IntegrityError, "student_not_enrolled"):
            self.connection.execute(
                """
                INSERT INTO attendance (session_id, student_id, scanned_at)
                VALUES (?, ?, ?)
                """,
                (
                    first_session_id,
                    other_course_student_id,
                    "2026-10-20T00:01:00.000+00:00",
                ),
            )

        close_session(self.connection, first_session_id)
        second_session_id = start_session(self.connection, first_course_id)["session_id"]
        self.connection.execute(
            """
            INSERT INTO attendance (session_id, student_id, scanned_at)
            VALUES (?, ?, ?)
            """,
            (second_session_id, enrolled_student_id, "2026-10-20T00:02:00.000+00:00"),
        )

        with self.assertRaisesRegex(sqlite3.IntegrityError, "student_not_enrolled"):
            self.connection.execute(
                """
                INSERT INTO attendance (session_id, student_id, scanned_at)
                VALUES (?, ?, ?)
                """,
                (
                    second_session_id,
                    other_course_student_id,
                    "2026-10-20T00:03:00.000+00:00",
                ),
            )

        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM attendance").fetchone()[0],
            2,
        )

    def test_session_history_returns_open_and_closed_states(self) -> None:
        course_id = self.add_course()
        first_session_id = start_session(self.connection, course_id)["session_id"]
        close_session(self.connection, first_session_id)
        second_session_id = start_session(self.connection, course_id)["session_id"]

        history = list_sessions(self.connection, course_id)
        self.assertEqual([row["session_id"] for row in history], [
            first_session_id,
            second_session_id,
        ])
        self.assertEqual([row["session_number"] for row in history], [1, 2])
        self.assertEqual([row["status"] for row in history], ["CLOSED", "OPEN"])
        self.assertIsNotNone(history[0]["closed_at"])
        self.assertIsNone(history[1]["closed_at"])

    def test_attendance_report_rejects_missing_session(self) -> None:
        with self.assertRaises(NotFoundError):
            get_attendance_report(self.connection, 999)


if __name__ == "__main__":
    unittest.main()
