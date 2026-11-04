from __future__ import annotations

import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from pc import database
from app.services.device_sync import DeviceError, EspHttpClient, migrate_device_schema, sync_once


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def test_mock_http_offline_queue_sync_and_authentication(tmp_path: Path):
    port = _free_port()
    token = "a" * 64
    env = os.environ.copy()
    env.update({
        "RFID_DEVICE_ID": "mock-esp",
        "RFID_DEVICE_TOKEN": token,
        "RFID_MOCK_PORT": str(port),
        "RFID_MOCK_STATE": str(tmp_path / "esp-state.json"),
    })
    process = subprocess.Popen(
        [sys.executable, str(PROJECT_ROOT / "tools" / "mock_esp32.py")],
        cwd=PROJECT_ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    client = EspHttpClient(f"http://127.0.0.1:{port}", "mock-esp", token, timeout=0.5)
    try:
        for _ in range(60):
            try:
                client.request("GET", "/api/v1/status")
                break
            except DeviceError:
                time.sleep(0.05)
        else:
            raise AssertionError("Mock ESP32 did not become ready")

        try:
            with urlopen(f"http://127.0.0.1:{port}/api/v1/status", timeout=1):
                raise AssertionError("unauthenticated request unexpectedly succeeded")
        except HTTPError as exc:
            assert exc.code == 401

        db_path = tmp_path / "integration.db"
        conn = database.open_database(db_path)
        try:
            migrate_device_schema(conn)
            student_id = database.create_student(conn, "Demo", "A1B2C3D4")
            course_id = database.create_course(conn, "Demo course")
            database.enroll_student(conn, student_id, course_id)
            session = database.start_session(conn, course_id)
            session_id = session["session_id"]
            generation = "integration-generation-1"
            conn.execute(
                "INSERT INTO devices(device_id,display_name) VALUES(?,?)",
                ("mock-esp", "Mock ESP32"),
            )
            conn.execute(
                "INSERT INTO session_device_bindings(session_generation,session_id,device_id,created_at) VALUES(?,?,?,?)",
                (generation, session_id, "mock-esp", "2026-11-04T00:00:00Z"),
            )
            conn.commit()
        finally:
            conn.close()

        before_scan = sync_once(db_path, client)
        assert before_scan["cache_sync_error"] is None
        binding = client.request("POST", "/api/v1/commands", {
            "command": "BIND_SESSION", "request_id": "test-bind-1",
            "session_id": session_id, "session_generation": generation,
        })
        assert binding["accepted"] is True
        scan = Request(
            f"http://127.0.0.1:{port}/mock/scan",
            data=b'{"uid":"A1B2C3D4"}',
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(scan, timeout=1) as response:
            assert response.status == 201

        synced = sync_once(db_path, client)
        assert synced["received"] == 1
        assert synced["results"][0]["status"] == "APPLIED"
        assert client.request("GET", "/api/v1/status")["pending_events"] == 0
        conn = database.open_database(db_path)
        try:
            assert conn.execute("SELECT COUNT(*) FROM attendance").fetchone()[0] == 1
            assert conn.execute("SELECT status FROM device_event_inbox").fetchone()[0] == "APPLIED"
        finally:
            conn.close()
    finally:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
