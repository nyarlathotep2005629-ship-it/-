"""PC主導のESP32同期。eventと出欠はPC SQLiteを正本として処理する。"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import secrets
import time
from typing import Any
from urllib.error import URLError, HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from pc import database


class DeviceError(RuntimeError):
    pass


def migrate_device_schema(connection: sqlite3.Connection) -> None:
    """Add device tables without rebuilding the existing v1 tables."""
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if version > 2:
        raise DeviceError(f"Unsupported database schema version: {version}")
    connection.execute("BEGIN IMMEDIATE")
    try:
        statements = """
        CREATE TABLE IF NOT EXISTS devices (
            device_id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            last_seen_at TEXT,
            firmware_version TEXT,
            disabled_at TEXT
        );
        CREATE TABLE IF NOT EXISTS device_sync_state (
            device_id TEXT PRIMARY KEY REFERENCES devices(device_id),
            boot_id TEXT,
            last_seen_seq INTEGER NOT NULL DEFAULT 0,
            last_acked_seq INTEGER NOT NULL DEFAULT 0,
            cache_version_sent INTEGER NOT NULL DEFAULT 0,
            cache_digest TEXT,
            last_sync_at TEXT
        );
        CREATE TABLE IF NOT EXISTS session_device_bindings (
            session_generation TEXT PRIMARY KEY,
            session_id INTEGER NOT NULL REFERENCES sessions(session_id),
            device_id TEXT NOT NULL REFERENCES devices(device_id),
            created_at TEXT NOT NULL,
            cleared_at TEXT
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_active_device_session
            ON session_device_bindings(device_id) WHERE cleared_at IS NULL;
        CREATE TABLE IF NOT EXISTS device_event_inbox (
            event_id TEXT PRIMARY KEY,
            device_id TEXT NOT NULL REFERENCES devices(device_id),
            boot_id TEXT NOT NULL,
            sequence INTEGER NOT NULL CHECK(sequence > 0),
            session_id INTEGER REFERENCES sessions(session_id),
            session_generation TEXT,
            card_uid TEXT NOT NULL,
            captured_at TEXT,
            time_quality TEXT NOT NULL,
            received_at TEXT NOT NULL,
            status TEXT NOT NULL,
            result_code TEXT NOT NULL,
            processed_at TEXT,
            UNIQUE(device_id, boot_id, sequence)
        );
        CREATE INDEX IF NOT EXISTS ix_device_inbox_pending
            ON device_event_inbox(device_id, boot_id, sequence, status);
        """
        for statement in statements.split(";"):
            if statement.strip():
                connection.execute(statement)
        connection.execute("PRAGMA user_version = 2")
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def uid_cache_snapshot(connection: sqlite3.Connection) -> tuple[list[dict[str, Any]], str]:
    rows = connection.execute("SELECT student_id,card_uid FROM students ORDER BY student_id").fetchall()
    entries = [{"uid": row["card_uid"], "student_ref": row["student_id"]} for row in rows]
    digest = hashlib.sha256(json.dumps(entries, separators=(",", ":")).encode()).hexdigest()
    return entries, digest


def import_event(connection: sqlite3.Connection, device_id: str, event: dict[str, Any]) -> dict[str, Any]:
    """Atomically persist receipt and formal attendance decision."""
    uid = database.normalize_uid(event["uid"])
    event_id = str(event["event_id"])
    boot_id = str(event["boot_id"])
    sequence = int(event["sequence"])
    session_id = event.get("session_id")
    generation = event.get("session_generation")
    time_quality = str(event.get("time_quality", "UNKNOWN"))
    if not event_id or len(event_id) > 128 or not boot_id or len(boot_id) > 64 or sequence <= 0:
        raise ValueError("Invalid event identity")
    if time_quality not in {"SYNCED", "ESTIMATED", "UNKNOWN"}:
        raise ValueError("Invalid time quality")
    captured_at = event.get("captured_at")
    if time_quality == "SYNCED":
        if not isinstance(captured_at, str):
            raise ValueError("A synchronized event must include captured_at")
        try:
            parsed_time = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("Invalid captured_at") from exc
        if parsed_time.tzinfo is None:
            raise ValueError("captured_at must include a timezone")
    connection.execute("BEGIN IMMEDIATE")
    try:
        existing = connection.execute(
            "SELECT event_id,device_id,boot_id,sequence,session_id,session_generation,card_uid,captured_at,time_quality,status,result_code "
            "FROM device_event_inbox WHERE event_id=?", (event_id,),
        ).fetchone()
        if existing:
            identity = (existing["device_id"], existing["boot_id"], existing["sequence"], existing["session_id"],
                        existing["session_generation"], existing["card_uid"], existing["captured_at"], existing["time_quality"])
            received_identity = (device_id, boot_id, sequence, session_id, generation, uid, captured_at, time_quality)
            if identity != received_identity:
                raise ValueError("event_id was replayed with different contents")
            connection.commit()
            return {"event_id": event_id, "status": existing["status"], "result_code": existing["result_code"], "duplicate_event": True}

        received_at = _now()
        result_code = "SESSION_CLOSED_REVIEW"
        status = "REVIEW"
        scanned_at = captured_at if time_quality == "SYNCED" else received_at
        student = connection.execute("SELECT student_id FROM students WHERE card_uid=?", (uid,)).fetchone()
        session = None
        if session_id is not None:
            session = connection.execute("SELECT session_id,course_id,closed_at FROM sessions WHERE session_id=?", (session_id,)).fetchone()
        binding = None
        if generation:
            binding = connection.execute(
                "SELECT session_id,device_id,cleared_at FROM session_device_bindings WHERE session_generation=?",
                (generation,),
            ).fetchone()

        if time_quality == "UNKNOWN":
            result_code = "TIME_UNKNOWN_REVIEW"
        elif time_quality == "ESTIMATED":
            result_code = "TIME_ESTIMATED_REVIEW"
        elif session is None or session["closed_at"] is not None or binding is None or binding["session_id"] != session_id or binding["device_id"] != device_id or binding["cleared_at"] is not None:
            result_code = "SESSION_CLOSED_REVIEW"
        elif student is None:
            result_code, status = "UNKNOWN_UID", "REJECTED"
        else:
            enrollment = connection.execute(
                "SELECT 1 FROM enrollments WHERE student_id=? AND course_id=?",
                (student["student_id"], session["course_id"]),
            ).fetchone()
            if enrollment is None:
                result_code, status = "NOT_ENROLLED", "REJECTED"
            else:
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO attendance(session_id,student_id,scanned_at) VALUES(?,?,?)",
                    (session_id, student["student_id"], scanned_at),
                )
                if cursor.rowcount:
                    result_code, status = "SUCCESS", "APPLIED"
                else:
                    result_code, status = "ALREADY_REGISTERED", "APPLIED"

        connection.execute(
            """INSERT INTO device_event_inbox(event_id,device_id,boot_id,sequence,session_id,session_generation,
               card_uid,captured_at,time_quality,received_at,status,result_code,processed_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (event_id, device_id, boot_id, sequence, session_id, generation, uid,
             captured_at, time_quality, received_at, status, result_code, received_at),
        )
        connection.commit()
        return {"event_id": event_id, "status": status, "result_code": result_code, "duplicate_event": False}
    except Exception:
        connection.rollback()
        raise


class EspHttpClient:
    """Minimal stdlib HTTP client; no extra runtime dependency."""

    def __init__(self, base_url: str, device_id: str, token: str, timeout: float = 2.0):
        self.base_url, self.device_id, self.token, self.timeout = base_url.rstrip("/"), device_id, token, timeout

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = json.dumps(body, separators=(",", ":")).encode() if body is not None else None
        digest = hashlib.sha256(payload or b"").hexdigest()
        timestamp = str(int(time.time() * 1000))
        nonce = secrets.token_hex(16)
        message = f"{method}\n{path}\n{digest}\n{timestamp}\n{nonce}".encode()
        signature = hmac.new(self.token.encode(), message, hashlib.sha256).hexdigest()
        req = Request(self.base_url + path, data=payload, method=method,
                      headers={"Content-Type": "application/json", "X-Device-Id": self.device_id, "X-Timestamp": timestamp,
                               "X-Nonce": nonce, "X-Signature": signature})
        try:
            with urlopen(req, timeout=self.timeout) as response:
                data = response.read(65537)
                if len(data) > 65536:
                    raise DeviceError("ESP response exceeded size limit")
                return json.loads(data or b"{}")
        except (URLError, HTTPError, TimeoutError, ValueError) as exc:
            raise DeviceError("ESP32 request failed") from exc


def sync_once(db_path: Path, client: EspHttpClient, limit: int = 50) -> dict[str, Any]:
    """Pull, commit, then ACK only the contiguous terminal sequence."""
    if limit < 1 or limit > 50:
        raise ValueError("limit must be between 1 and 50")
    conn = database.open_database(db_path)
    try:
        migrate_device_schema(conn)
        conn.execute("INSERT OR IGNORE INTO devices(device_id,display_name) VALUES(?,?)", (client.device_id, "ESP32"))
        conn.execute("INSERT OR IGNORE INTO device_sync_state(device_id) VALUES(?)", (client.device_id,))
        state = conn.execute("SELECT boot_id,last_acked_seq FROM device_sync_state WHERE device_id=?", (client.device_id,)).fetchone()
        cache_state = conn.execute("SELECT cache_version_sent,cache_digest FROM device_sync_state WHERE device_id=?", (client.device_id,)).fetchone()
        # The ESP has no Internet/NTP route on the isolated SoftAP. Refresh its
        # wall clock from the PC so scans made while linked can be timestamped.
        client.request("POST", "/api/v1/commands", {
            "command": "SYNC_TIME",
            "request_id": str(uuid4()),
            "epoch_ms": int(time.time() * 1000),
        })
        status = client.request("GET", "/api/v1/status")
        boot_id = str(status["boot_id"])
        if not boot_id or len(boot_id) > 64:
            raise DeviceError("ESP returned an invalid boot_id")
        cache_entries, cache_digest = uid_cache_snapshot(conn)
        cache_error = None
        cache_version = int(cache_state["cache_version_sent"])
        if cache_state["cache_digest"] != cache_digest or status.get("cache_sha256") != cache_digest:
            version = max(cache_version, int(status.get("cache_version", 0))) + 1
            try:
                response = client.request("PUT", "/api/v1/cache", {"version": version, "sha256": cache_digest, "entries": cache_entries})
                if response.get("version") != version or response.get("sha256") != cache_digest:
                    raise DeviceError("ESP did not confirm the cache snapshot")
                conn.execute("UPDATE device_sync_state SET cache_version_sent=?,cache_digest=? WHERE device_id=?", (version, cache_digest, client.device_id))
                conn.commit()
                cache_version = version
            except DeviceError:
                # Candidate cache is an optimization. A cache transfer failure
                # must not block durable event delivery or ACK processing.
                cache_error = "CACHE_SYNC_FAILED"
        if state["boot_id"] == boot_id:
            after = max(int(state["last_acked_seq"]), int(status.get("acked_through_seq", 0)))
        else:
            after = int(status.get("acked_through_seq", 0))
        if after < 0 or after > int(status.get("last_sequence", after)):
            raise DeviceError("ESP returned an invalid ACK cursor")
        batch = client.request("GET", f"/api/v1/events?boot_id={boot_id}&after_seq={after}&limit={limit}")
        if batch.get("boot_id", boot_id) != boot_id or not isinstance(batch.get("events", []), list):
            raise DeviceError("ESP returned an invalid event batch")
        expected = after + 1
        results = []
        for event in batch.get("events", []):
            if event.get("boot_id") != boot_id or int(event.get("sequence", -1)) != expected:
                break
            results.append(import_event(conn, client.device_id, event))
            expected += 1
        ack = expected - 1
        if ack > after:
            client.request("POST", "/api/v1/events/ack", {"boot_id": boot_id, "ack_through_seq": ack})
        conn.execute("UPDATE device_sync_state SET boot_id=?,last_seen_seq=?,last_acked_seq=?,last_sync_at=? WHERE device_id=?",
                     (boot_id, int(status.get("last_sequence", ack)), max(after, ack), _now(), client.device_id))
        conn.commit()
        conn.execute("UPDATE devices SET last_seen_at=?,firmware_version=? WHERE device_id=?", (_now(), status.get("firmware_version"), client.device_id))
        conn.commit()
        return {"device_id": client.device_id, "boot_id": boot_id, "ack_through_seq": max(after, ack), "received": len(results), "results": results,
                "cache_version": cache_version, "cache_digest": cache_digest, "cache_sync_error": cache_error}
    finally:
        conn.close()


def register_device(db_path: Path, device_id: str) -> None:
    conn = database.open_database(db_path)
    try:
        migrate_device_schema(conn)
        conn.execute("INSERT OR IGNORE INTO devices(device_id,display_name) VALUES(?,?)", (device_id, "ESP32"))
        conn.execute("INSERT OR IGNORE INTO device_sync_state(device_id) VALUES(?)", (device_id,))
        conn.commit()
    finally:
        conn.close()
