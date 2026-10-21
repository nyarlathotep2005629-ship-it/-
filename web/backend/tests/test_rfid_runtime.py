from __future__ import annotations

from pathlib import Path
import time

from pc.reader import EVENT_UID, ReaderEvent

from app.config import Settings
from app.services import rfid_runtime
from app.services.rfid_runtime import RfidRuntimeController
from pc import database

TEST_UID = "AB" * 4


class FakeSerialReader:
    def __init__(self, *args, **kwargs):
        self.events = [ReaderEvent(EVENT_UID, uid=TEST_UID)]
        self.closed = False

    def open(self) -> None:
        return None

    def read_event(self, timeout: float | None = None):
        if self.events:
            return self.events.pop(0)
        time.sleep(min(timeout or 0.01, 0.01))
        return None

    def close(self) -> None:
        self.closed = True


def wait_for(predicate, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate()


def prepare_database(path: Path) -> int:
    connection = database.open_database(path)
    try:
        student_id = database.create_student(connection, "テスト学生", TEST_UID)
        course_id = database.create_course(connection, "テスト授業")
        database.enroll_student(connection, student_id, course_id)
        return database.start_session(connection, course_id)["session_id"]
    finally:
        connection.close()


def test_serial_worker_fake_reader_and_stop(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "runtime.db"
    session_id = prepare_database(db_path)
    monkeypatch.setattr(rfid_runtime, "SerialReader", FakeSerialReader)
    controller = RfidRuntimeController(Settings(db_path=db_path, stop_timeout=1.0))

    started = controller.start_attendance(session_id, "SERIAL", "FAKE")
    assert started["operation_id"]
    wait_for(lambda: any(event["kind"] == "ATTENDANCE_RESULT" for event in controller.events(None, 0, 100)["events"]))
    controller.stop()

    connection = database.open_database(db_path)
    try:
        assert database.get_attendance_report(connection, session_id)[0]["attendance_status"] == "PRESENT"
    finally:
        connection.close()


def test_mock_runtime_rejects_second_start_and_supports_cursor_reset(tmp_path: Path) -> None:
    db_path = tmp_path / "mock.db"
    session_id = prepare_database(db_path)
    controller = RfidRuntimeController(Settings(db_path=db_path, event_buffer_size=2))
    controller.start_attendance(session_id, "MOCK", None)
    try:
        controller.start_attendance(session_id, "MOCK", None)
        raise AssertionError("second runtime start was accepted")
    except Exception as exc:
        assert getattr(exc, "code", None) == "RFID_BUSY"
    controller.scan_mock(session_id, TEST_UID)
    controller.stop()
    batch = controller.events(None, 0, 100)
    assert batch["reset_required"] is True
