"""Idempotent exhibition-only demo data; never copies operational records."""
from pathlib import Path

from pc import database


def ensure_demo_data(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = database.open_database(db_path)
    try:
        if connection.execute("SELECT COUNT(*) FROM students").fetchone()[0] != 0:
            return
        course_id = database.create_course(connection, "文化祭デモ授業")
        people = [
            ("デモ学生A", "D0A00001"),
            ("デモ学生B", "D0A00002"),
            ("デモ学生C", "D0A00003"),
        ]
        ids = [database.create_student(connection, name, uid) for name, uid in people]
        for student_id in ids:
            database.enroll_student(connection, student_id, course_id)
    finally:
        connection.close()
