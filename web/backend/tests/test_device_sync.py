from __future__ import annotations

from pathlib import Path

from pc import database
from app.services.device_sync import import_event, migrate_device_schema, sync_once


def prepared_db(path: Path):
    conn = database.open_database(path)
    migrate_device_schema(conn)
    conn.execute("INSERT INTO devices(device_id,display_name) VALUES('esp-demo','Demo ESP')")
    student_id = database.create_student(conn, "Test", "A1B2C3D4")
    course_id = database.create_course(conn, "Test course")
    database.enroll_student(conn, student_id, course_id)
    session = database.start_session(conn, course_id)
    conn.execute("INSERT INTO session_device_bindings(session_generation,session_id,device_id,created_at) VALUES('gen-1',?,'esp-demo','2026-11-04T00:00:00Z')", (session["session_id"],))
    conn.commit()
    return conn, session["session_id"], student_id


def event(event_id: str = "esp-demo:boot-1:1", sequence: int = 1):
    return {
        "event_id": event_id,
        "boot_id": "boot-1",
        "sequence": sequence,
        "session_id": 1,
        "session_generation": "gen-1",
        "uid": "A1B2C3D4",
        "captured_at": "2026-11-04T00:00:01+00:00",
        "time_quality": "SYNCED",
    }


def test_event_and_attendance_commit_atomically_and_deduplicate(tmp_path: Path):
    conn, session_id, _ = prepared_db(tmp_path / "sync.db")
    try:
        item = event()
        item["session_id"] = session_id
        first = import_event(conn, "esp-demo", item)
        replay = import_event(conn, "esp-demo", item)
        assert first["result_code"] == "SUCCESS"
        assert replay["duplicate_event"] is True
        assert conn.execute("SELECT COUNT(*) FROM attendance").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM device_event_inbox").fetchone()[0] == 1
    finally:
        conn.close()


def test_unknown_time_is_review_and_does_not_mark_attendance(tmp_path: Path):
    conn, session_id, _ = prepared_db(tmp_path / "unknown-time.db")
    try:
        item = event()
        item.update(session_id=session_id, time_quality="UNKNOWN", captured_at=None)
        result = import_event(conn, "esp-demo", item)
        assert result["status"] == "REVIEW"
        assert result["result_code"] == "TIME_UNKNOWN_REVIEW"
        assert conn.execute("SELECT COUNT(*) FROM attendance").fetchone()[0] == 0
    finally:
        conn.close()


def test_estimated_time_is_review_and_does_not_mark_attendance(tmp_path: Path):
    conn, session_id, _ = prepared_db(tmp_path / "estimated-time.db")
    try:
        item = event()
        item.update(session_id=session_id, time_quality="ESTIMATED")
        result = import_event(conn, "esp-demo", item)
        assert result["status"] == "REVIEW"
        assert result["result_code"] == "TIME_ESTIMATED_REVIEW"
        assert conn.execute("SELECT COUNT(*) FROM attendance").fetchone()[0] == 0
    finally:
        conn.close()


def test_closed_session_event_is_review(tmp_path: Path):
    conn, session_id, _ = prepared_db(tmp_path / "closed.db")
    try:
        database.close_session(conn, session_id)
        item = event()
        item["session_id"] = session_id
        result = import_event(conn, "esp-demo", item)
        assert result["status"] == "REVIEW"
        assert result["result_code"] == "SESSION_CLOSED_REVIEW"
        assert conn.execute("SELECT COUNT(*) FROM attendance").fetchone()[0] == 0
    finally:
        conn.close()


def test_migration_is_idempotent_and_preserves_core_rows(tmp_path: Path):
    conn, session_id, student_id = prepared_db(tmp_path / "migration.db")
    try:
        migrate_device_schema(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM students WHERE student_id=?", (student_id,)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM sessions WHERE session_id=?", (session_id,)).fetchone()[0] == 1
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()


class FakeDevice:
    device_id = "esp-demo"
    def __init__(self, item):
        self.item=item
        self.acked=[]
        self.cache=None
        self.fail_ack_once=False
    def request(self, method, path, body=None):
        if path == "/api/v1/status":
            acked=self.acked[-1] if self.acked else 0
            return {"boot_id":"boot-1","last_sequence":1,"acked_through_seq":acked,"pending_events":1-acked,
                    "cache_version":self.cache["version"] if self.cache else 0,"cache_sha256":self.cache["sha256"] if self.cache else ""}
        if path == "/api/v1/commands" and body["command"] == "SYNC_TIME": return {"accepted":True}
        if path == "/api/v1/cache":
            self.cache=body
            return {"version":body["version"],"sha256":body["sha256"]}
        if path.startswith("/api/v1/events?"):
            if "after_seq=0" in path and not self.acked: return {"boot_id":"boot-1","events":[self.item]}
            return {"boot_id":"boot-1","events":[]}
        if path == "/api/v1/events/ack":
            self.acked.append(body["ack_through_seq"])
            if self.fail_ack_once:
                self.fail_ack_once=False
                from app.services.device_sync import DeviceError
                raise DeviceError("simulated lost ACK response")
            return {"acked_through_seq":body["ack_through_seq"]}
        raise AssertionError((method,path))


def test_sync_commits_before_ack_and_replay_is_safe(tmp_path: Path):
    path=tmp_path/"sync-flow.db"
    conn, session_id, _=prepared_db(path)
    conn.close()
    item=event();item["session_id"]=session_id
    client=FakeDevice(item)
    result=sync_once(path,client)
    assert result["ack_through_seq"]==1
    assert client.acked==[1]
    replay=sync_once(path,client)
    assert replay["received"]==0
    conn=database.open_database(path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM attendance").fetchone()[0]==1
        assert conn.execute("SELECT status FROM device_event_inbox").fetchone()[0]=="APPLIED"
    finally: conn.close()


def test_sync_recovers_if_ack_commits_but_response_is_lost(tmp_path: Path):
    path=tmp_path/"ack-lost.db"
    conn, session_id, _=prepared_db(path)
    conn.close()
    item=event();item["session_id"]=session_id
    client=FakeDevice(item);client.fail_ack_once=True
    try:
        sync_once(path,client)
        assert False, "lost ACK response should surface"
    except Exception as exc:
        from app.services.device_sync import DeviceError
        assert isinstance(exc,DeviceError)
    result=sync_once(path,client)
    assert result["received"]==0
    assert result["ack_through_seq"]==1
    conn=database.open_database(path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM attendance").fetchone()[0]==1
        assert conn.execute("SELECT last_acked_seq FROM device_sync_state WHERE device_id='esp-demo'").fetchone()[0]==1
    finally:
        conn.close()


def test_session_drain_fetches_multiple_batches_before_unbinding(tmp_path: Path, monkeypatch):
    from app.config import Settings
    from app.routers import devices

    path = tmp_path / "drain.db"
    conn, session_id, _ = prepared_db(path)
    conn.close()

    class QueueDevice:
        device_id = "esp-demo"

        def __init__(self):
            self.acked = 0
            self.cache_version = 0
            self.cache_sha256 = ""
            self.items = []
            for sequence in range(1, 2001):
                item = event(f"esp-demo:boot-1:{sequence}", sequence)
                item.update(session_id=session_id, uid="A1B2C3D4")
                self.items.append(item)

        def request(self, method, path, body=None):
            if path == "/api/v1/status":
                return {"boot_id": "boot-1", "last_sequence": 2000, "acked_through_seq": self.acked,
                        "pending_events": 2000 - self.acked, "cache_version": self.cache_version,
                        "cache_sha256": self.cache_sha256, "active_session_id": session_id,
                        "session_generation": "gen-1"}
            if path == "/api/v1/commands" and body["command"] == "SYNC_TIME":
                return {"accepted": True}
            if path == "/api/v1/commands" and body["command"] == "PAUSE_READING":
                return {"accepted": True}
            if path == "/api/v1/commands" and body["command"] == "CLEAR_SESSION":
                return {"accepted": True}
            if path == "/api/v1/cache":
                self.cache_version = body["version"]
                self.cache_sha256 = body["sha256"]
                return {"version": self.cache_version, "sha256": self.cache_sha256}
            if path.startswith("/api/v1/events?"):
                after = int(path.split("after_seq=", 1)[1].split("&", 1)[0])
                return {"boot_id": "boot-1", "events": [item for item in self.items if item["sequence"] > after][:50]}
            if path == "/api/v1/events/ack":
                self.acked = body["ack_through_seq"]
                return {"acked_through_seq": self.acked}
            raise AssertionError((method, path))

    fake = QueueDevice()
    monkeypatch.setattr(devices, "_client", lambda _settings: fake)
    settings = Settings(db_path=path, device_id="esp-demo", device_token="x" * 64)
    devices.finish_bound_session(session_id, settings)
    conn = database.open_database(path)
    try:
        assert fake.acked == 2000
        assert conn.execute("SELECT COUNT(*) FROM device_event_inbox").fetchone()[0] == 2000
        assert conn.execute("SELECT COUNT(*) FROM attendance").fetchone()[0] == 1
        assert conn.execute("SELECT cleared_at FROM session_device_bindings WHERE session_id=?", (session_id,)).fetchone()[0] is not None
    finally:
        conn.close()
