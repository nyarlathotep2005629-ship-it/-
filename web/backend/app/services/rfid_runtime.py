"""RFIDシリアル/MOCK runtimeの単一コントローラー。"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from pathlib import Path
import threading
import time
from typing import Any, Literal
from uuid import uuid4

from pc import database
from pc.reader import (
    EVENT_ERROR,
    EVENT_UID,
    EVENT_WARNING,
    ReaderError,
    SerialReader,
    list_serial_ports,
)

from ..config import Settings
from ..errors import ApiError


RuntimeState = Literal["IDLE", "STARTING", "REGISTERING", "ATTENDING", "STOPPING", "ERROR"]


class RfidRuntimeController:
    """COMを最大1本だけ所有し、worker threadを通じて扱う。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._state_lock = threading.RLock()
        self._operation_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._worker: threading.Thread | None = None
        self._reader: SerialReader | None = None
        self._events: deque[dict[str, Any]] = deque(maxlen=settings.event_buffer_size)
        self._sequence = 0
        self._state: RuntimeState = "IDLE"
        self._operation = "NONE"
        self._operation_id: str | None = None
        self._session_id: int | None = None
        self._mode: str | None = None
        self._port: str | None = None
        self._updated_at = self._now()
        self._last_error: str | None = None
        self._last_completed_operation_id: str | None = None
        self._last_terminal_event_kind: str | None = None

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds")

    def _append_event(self, kind: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._state_lock:
            self._sequence += 1
            event = {
                "sequence": self._sequence,
                "kind": kind,
                "created_at": self._now(),
                "state": self._state,
                "operation_id": self._operation_id,
                "payload": payload or {},
            }
            self._events.append(event)
            self._updated_at = event["created_at"]
            return event

    def _set_state(self, state: RuntimeState, *, operation: str | None = None) -> None:
        with self._state_lock:
            self._state = state
            if operation is not None:
                self._operation = operation
            self._updated_at = self._now()

    def snapshot(self) -> dict[str, Any]:
        with self._state_lock:
            return {
                "server_instance_id": self.settings.server_instance_id,
                "state": self._state,
                "operation": self._operation,
                "operation_id": self._operation_id,
                "session_id": self._session_id,
                "mode": self._mode,
                "port": self._port,
                "updated_at": self._updated_at,
                "last_error": self._last_error,
                "last_completed_operation_id": self._last_completed_operation_id,
                "last_terminal_event_kind": self._last_terminal_event_kind,
            }

    def ports(self) -> list[dict[str, str]]:
        return list_serial_ports()

    def events(self, server_instance_id: str | None, after_sequence: int, limit: int) -> dict[str, Any]:
        with self._state_lock:
            first = self._events[0]["sequence"] if self._events else self._sequence + 1
            last = self._events[-1]["sequence"] if self._events else self._sequence
            reset = server_instance_id not in (None, self.settings.server_instance_id)
            if self._events and after_sequence < first - 1:
                reset = True
            selected = [] if reset else [event for event in self._events if event["sequence"] > after_sequence][:limit]
            next_after = selected[-1]["sequence"] if selected else after_sequence
            return {
                "server_instance_id": self.settings.server_instance_id,
                "first_sequence": first,
                "last_sequence": last,
                "next_after_sequence": next_after,
                "reset_required": reset,
                "events": selected,
            }

    def _ensure_idle(self) -> None:
        if self._state != "IDLE":
            raise ApiError("RFID_BUSY", 409, "RFIDは別の処理で使用中です")

    def _begin(self, state: RuntimeState, operation: str, session_id: int | None, mode: str, port: str | None) -> str:
        self._ensure_idle()
        self._stop_event.clear()
        self._state = state
        self._operation = operation
        self._operation_id = str(uuid4())
        self._session_id = session_id
        self._mode = mode
        self._port = port
        self._last_error = None
        self._last_terminal_event_kind = None
        self._updated_at = self._now()
        return self._operation_id

    def start_attendance(self, session_id: int, mode: str, port: str | None) -> dict[str, Any]:
        with self._operation_lock:
            with self._state_lock:
                operation_id = self._begin("STARTING" if mode == "SERIAL" else "ATTENDING", "ATTENDING", session_id, mode, port)
                self._append_event("RUNTIME_STARTED", {"session_id": session_id, "mode": mode, "port": port})
            if mode == "MOCK":
                return self.snapshot()
            self._worker = threading.Thread(
                target=self._run_attendance,
                args=(operation_id, session_id, port),
                name="rfid-attendance-worker",
                daemon=True,
            )
            self._worker.start()
            return self.snapshot()

    def start_registration(self, name: str, port: str) -> dict[str, Any]:
        with self._operation_lock:
            with self._state_lock:
                operation_id = self._begin("REGISTERING", "REGISTERING", None, "SERIAL", port)
                self._append_event("REGISTRATION_STARTED", {"operation_id": operation_id})
            self._worker = threading.Thread(
                target=self._run_registration,
                args=(operation_id, name, port),
                name="rfid-registration-worker",
                daemon=True,
            )
            self._worker.start()
            return self.snapshot()

    def _new_reader(self, port: str) -> SerialReader:
        reader = SerialReader(
            port,
            baudrate=self.settings.serial_baudrate,
            timeout=self.settings.serial_timeout,
            ready_timeout=self.settings.serial_ready_timeout,
        )
        self._reader = reader
        return reader

    @staticmethod
    def _attendance_payload(result: dict[str, Any]) -> dict[str, Any]:
        return {
            key: result[key]
            for key in ("status", "student_id", "student_name", "session_id", "scanned_at")
            if key in result
        }

    def _run_attendance(self, operation_id: str, session_id: int, port: str | None) -> None:
        connection = None
        reader: SerialReader | None = None
        terminal_kind = "RUNTIME_STOPPED"
        try:
            if not port:
                raise RuntimeError("SERIAL port is required")
            connection = database.open_database(self.settings.db_path)
            reader = self._new_reader(port)
            reader.open()
            if self._stop_event.is_set():
                return
            self._set_state("ATTENDING", operation="ATTENDING")
            self._append_event("RUNTIME_READY", {"session_id": session_id})
            while not self._stop_event.is_set():
                event = reader.read_event(timeout=self.settings.serial_timeout)
                if event is None:
                    continue
                if event.kind == EVENT_UID and event.uid:
                    result = database.register_attendance(connection, session_id, event.uid)
                    self._append_event("ATTENDANCE_RESULT", self._attendance_payload(result))
                elif event.kind == EVENT_ERROR:
                    terminal_kind = "RUNTIME_ERROR"
                    self._fail_runtime("SERIAL_READ_FAILED", "Arduinoからエラー通知を受信しました")
                    break
                elif event.kind == EVENT_WARNING:
                    self._append_event("RUNTIME_WARNING", {"message": "受信データを無視しました"})
        except ReaderError:
            terminal_kind = "RUNTIME_ERROR"
            self._fail_runtime("SERIAL_OPEN_FAILED", "RFIDリーダーを利用できません")
        except Exception:
            terminal_kind = "RUNTIME_ERROR"
            self._fail_runtime("STORAGE_ERROR", "RFID処理中にエラーが発生しました")
        finally:
            if reader is not None:
                try:
                    reader.close()
                except Exception:
                    pass
            if connection is not None:
                connection.close()
            self._finish(operation_id, terminal_kind)

    def _run_registration(self, operation_id: str, name: str, port: str) -> None:
        connection = None
        reader: SerialReader | None = None
        terminal_kind = "REGISTRATION_CANCELLED"
        try:
            connection = database.open_database(self.settings.db_path)
            reader = self._new_reader(port)
            reader.open()
            self._append_event("REGISTRATION_READY")
            while not self._stop_event.is_set():
                event = reader.read_event(timeout=self.settings.serial_timeout)
                if event is None:
                    continue
                if event.kind == EVENT_UID and event.uid:
                    student_id = database.create_student(connection, name, event.uid)
                    self._append_event("REGISTRATION_COMPLETED", {"student_id": student_id, "name": name})
                    terminal_kind = "REGISTRATION_COMPLETED"
                    break
                if event.kind == EVENT_ERROR:
                    terminal_kind = "REGISTRATION_FAILED"
                    self._fail_runtime("SERIAL_READ_FAILED", "Arduinoからエラー通知を受信しました")
                    break
        except database.DuplicateError:
            terminal_kind = "REGISTRATION_FAILED"
            self._fail_runtime("DUPLICATE", "このカードUIDは既に登録されています")
        except database.ValidationError:
            terminal_kind = "REGISTRATION_FAILED"
            self._fail_runtime("VALIDATION_ERROR", "学生情報を確認してください")
        except ReaderError:
            terminal_kind = "REGISTRATION_FAILED"
            self._fail_runtime("SERIAL_OPEN_FAILED", "RFIDリーダーを利用できません")
        except Exception:
            terminal_kind = "REGISTRATION_FAILED"
            self._fail_runtime("STORAGE_ERROR", "学生登録中にエラーが発生しました")
        finally:
            if reader is not None:
                try:
                    reader.close()
                except Exception:
                    pass
            if connection is not None:
                connection.close()
            self._finish(operation_id, terminal_kind)

    def _fail_runtime(self, code: str, message: str) -> None:
        with self._state_lock:
            self._state = "ERROR"
            self._last_error = code
        self._append_event("RUNTIME_ERROR", {"code": code, "message": message})

    def _finish(self, operation_id: str, terminal_kind: str) -> None:
        with self._state_lock:
            if self._operation_id != operation_id:
                return
            self._last_completed_operation_id = operation_id
            self._last_terminal_event_kind = terminal_kind
            self._append_event(terminal_kind, {"operation_id": operation_id})
            self._state = "IDLE"
            self._operation = "NONE"
            self._operation_id = None
            self._session_id = None
            self._mode = None
            self._port = None
            self._reader = None
            self._updated_at = self._now()

    def scan_mock(self, session_id: int, card_uid: str) -> dict[str, Any]:
        with self._operation_lock:
            with self._state_lock:
                if self._state != "ATTENDING" or self._mode != "MOCK" or self._session_id != session_id:
                    raise ApiError("RFID_RUNTIME_ACTIVE", 409, "MOCK出席受付が開始されていません")
            connection = database.open_database(self.settings.db_path)
            try:
                result = database.register_attendance(connection, session_id, card_uid)
            finally:
                connection.close()
            payload = self._attendance_payload(result)
            self._append_event("ATTENDANCE_RESULT", payload)
            return payload

    def stop(self) -> dict[str, Any]:
        with self._operation_lock:
            with self._state_lock:
                if self._state == "IDLE":
                    return {"stopped": True, **self.snapshot()}
                operation_id = self._operation_id
                self._state = "STOPPING"
                self._updated_at = self._now()
                self._stop_event.set()
                self._append_event("RUNTIME_STOPPING", {"operation_id": operation_id})
                worker = self._worker
                if worker is None:
                    self._finish(operation_id or "", "RUNTIME_STOPPED")
                    return {"stopped": True, **self.snapshot()}
            worker.join(timeout=self.settings.stop_timeout)
            if worker.is_alive():
                raise ApiError("RUNTIME_STOP_TIMEOUT", 503, "RFID停止処理がタイムアウトしました", True)
            return {"stopped": True, **self.snapshot()}

    def close_session(self, session_id: int) -> dict[str, Any]:
        with self._operation_lock:
            with self._state_lock:
                if self._state != "IDLE":
                    raise ApiError("RFID_RUNTIME_ACTIVE", 409, "RFID受付を停止してから授業回を終了してください")
            connection = database.open_database(self.settings.db_path)
            try:
                open_session = database.get_open_session(connection)
                if open_session is None or open_session["session_id"] != session_id:
                    raise ApiError("SESSION_NOT_OPEN", 409, "対象授業回は受付中ではありません")
                database.close_session(connection, session_id)
                return database.get_open_session(connection) or {"session_id": session_id, "status": "CLOSED"}
            finally:
                connection.close()

    def shutdown(self) -> None:
        try:
            self.stop()
        except Exception:
            pass
