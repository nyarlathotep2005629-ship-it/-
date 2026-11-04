from __future__ import annotations

from datetime import datetime, timezone
import sqlite3
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends
from pydantic import Field
from pydantic import BaseModel, ConfigDict

from ..config import Settings
from ..dependencies import get_settings
from ..errors import ApiError
from ..services import device_sync

router = APIRouter(prefix="/devices", tags=["devices"])


class CommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: Literal["PAUSE_READING", "RESUME_READING", "SYNC_NOW", "DIAGNOSTIC", "REFRESH_CACHE", "CLEAR_SESSION"]
    request_id: str = Field(default_factory=lambda: str(uuid4()), max_length=64)


def _client(settings: Settings) -> device_sync.EspHttpClient:
    if not settings.device_id or not settings.device_token:
        raise ApiError("DEVICE_NOT_PAIRED", 503, "ESP32のdevice IDとtokenを設定してください")
    return device_sync.EspHttpClient(settings.device_base_url, settings.device_id, settings.device_token, settings.device_timeout)


@router.get("/status")
def device_status(settings: Settings = Depends(get_settings)):
    client = _client(settings)
    try:
        status = client.request("GET", "/api/v1/status")
        device_sync.register_device(settings.db_path, settings.device_id)
        conn = device_sync.database.open_database(settings.db_path)
        try:
            snapshot, digest = device_sync.uid_cache_snapshot(conn)
            sync_state = conn.execute("SELECT last_acked_seq,last_sync_at,cache_digest FROM device_sync_state WHERE device_id=?", (settings.device_id,)).fetchone()
            status["pc_cache_count"] = len(snapshot)
            status["pc_cache_digest"] = digest
            status["cache_in_sync"] = status.get("cache_sha256") == digest
            status["pc_last_acked_seq"] = sync_state["last_acked_seq"] if sync_state else 0
            status["pc_last_sync_at"] = sync_state["last_sync_at"] if sync_state else None
            return status
        finally:
            conn.close()
    except device_sync.DeviceError as exc:
        raise ApiError("DEVICE_UNAVAILABLE", 503, "ESP32に接続できません") from exc


@router.post("/sync")
def sync_device(settings: Settings = Depends(get_settings)):
    try:
        return device_sync.sync_once(settings.db_path, _client(settings))
    except device_sync.DeviceError as exc:
        raise ApiError("DEVICE_SYNC_FAILED", 503, "同期に失敗しました。未ACKイベントは端末に保持されています") from exc
    except (KeyError, ValueError) as exc:
        raise ApiError("DEVICE_EVENT_INVALID", 422, "ESP32応答またはイベント形式が不正です") from exc


@router.post("/commands", status_code=202)
def send_command(payload: CommandRequest, settings: Settings = Depends(get_settings)):
    client = _client(settings)
    if payload.command in {"SYNC_NOW", "REFRESH_CACHE"}:
        try:
            return device_sync.sync_once(settings.db_path, client)
        except device_sync.DeviceError as exc:
            raise ApiError("DEVICE_SYNC_FAILED", 503, "ESP32同期に失敗しました。端末の未同期イベントは保持されます") from exc
    try:
        response = client.request("POST", "/api/v1/commands", {"command": payload.command, "request_id": payload.request_id})
        if not response.get("accepted"):
            raise device_sync.DeviceError("ESP32 did not accept the command")
        return response
    except device_sync.DeviceError as exc:
        raise ApiError("DEVICE_COMMAND_FAILED", 503, "ESP32へ操作を送信できません") from exc


@router.get("/events")
def inbox_events(settings: Settings = Depends(get_settings), limit: int = 100):
    if limit < 1 or limit > 500:
        raise ApiError("INVALID_LIMIT", 422, "limitは1から500の範囲です")
    conn = device_sync.database.open_database(settings.db_path)
    try:
        device_sync.migrate_device_schema(conn)
        rows = conn.execute(
            "SELECT event_id,device_id,boot_id,sequence,session_id,time_quality,received_at,status,result_code "
            "FROM device_event_inbox ORDER BY received_at DESC LIMIT ?", (limit,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


@router.post("/session/{session_id}/bind")
def bind_session(session_id: int, settings: Settings = Depends(get_settings)):
    if not settings.device_id or not settings.device_token:
        raise ApiError("DEVICE_NOT_PAIRED", 503, "ESP32のdevice IDとtokenを設定してください")
    conn = device_sync.database.open_database(settings.db_path)
    generation = ""
    inserted = False
    try:
        device_sync.migrate_device_schema(conn)
        session = conn.execute("SELECT closed_at FROM sessions WHERE session_id=?", (session_id,)).fetchone()
        if session is None or session["closed_at"] is not None:
            raise ApiError("SESSION_NOT_OPEN", 409, "OPEN中の授業回だけ端末へ紐付けできます")
        conn.execute("INSERT OR IGNORE INTO devices(device_id,display_name) VALUES(?,?)", (settings.device_id, "ESP32"))
        active = conn.execute("SELECT session_generation,session_id FROM session_device_bindings WHERE device_id=? AND cleared_at IS NULL", (settings.device_id,)).fetchone()
        if active:
            if active["session_id"] != session_id:
                raise ApiError("DEVICE_ALREADY_BOUND", 409, "ESP32は別の授業回に紐付いています")
            generation = active["session_generation"]
        else:
            generation = str(uuid4())
            conn.execute("INSERT INTO session_device_bindings(session_generation,session_id,device_id,created_at) VALUES(?,?,?,?)",
                         (generation, session_id, settings.device_id, datetime.now(timezone.utc).isoformat()))
            inserted = True
        conn.commit()
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise ApiError("DEVICE_ALREADY_BOUND", 409, "ESP32は別の授業回に紐付いています") from exc
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    try:
        result = _client(settings).request("POST", "/api/v1/commands", {
            "command": "BIND_SESSION", "request_id": str(uuid4()),
            "session_id": session_id, "session_generation": generation,
        })
        if not result.get("accepted"):
            raise device_sync.DeviceError("ESP32 did not accept the session binding")
        return {"session_id": session_id, "session_generation": generation, "device": result}
    except device_sync.DeviceError as exc:
        if inserted:
            conn = device_sync.database.open_database(settings.db_path)
            try:
                conn.execute("UPDATE session_device_bindings SET cleared_at=? WHERE session_generation=?", (datetime.now(timezone.utc).isoformat(), generation))
                conn.commit()
            finally:
                conn.close()
        raise ApiError("SESSION_BIND_FAILED", 503, "端末への授業回紐付けに失敗しました") from exc


def finish_bound_session(session_id: int, settings: Settings) -> None:
    """Stop, drain and unbind before the existing close transaction."""
    conn = device_sync.database.open_database(settings.db_path)
    try:
        device_sync.migrate_device_schema(conn)
        binding = conn.execute("SELECT session_generation FROM session_device_bindings WHERE session_id=? AND device_id=? AND cleared_at IS NULL",
                               (session_id, settings.device_id)).fetchone() if settings.device_id else None
    finally:
        conn.close()
    if binding is None:
        return
    if not settings.device_token:
        raise ApiError("DEVICE_NOT_PAIRED", 409, "授業回を終了する前にESP32接続設定を復旧してください")
    client = _client(settings)
    try:
        client.request("POST", "/api/v1/commands", {"command": "PAUSE_READING", "request_id": str(uuid4())})
        # 2,000 queued events at 50 per pull need 40 syncs plus one final
        # status read to confirm the device queue reached zero.
        for _ in range(41):
            status = client.request("GET", "/api/v1/status")
            if int(status.get("active_session_id", 0) or 0) != session_id or status.get("session_generation") != binding["session_generation"]:
                raise device_sync.DeviceError("Device session does not match the PC binding")
            pending = int(status.get("pending_events", 0))
            if pending == 0:
                break
            result = device_sync.sync_once(settings.db_path, client)
            if int(result.get("received", 0)) == 0:
                raise device_sync.DeviceError("Device queue made no drain progress")
        else:
            raise device_sync.DeviceError("Device queue exceeded the bounded drain window")
        result = client.request("POST", "/api/v1/commands", {"command": "CLEAR_SESSION", "request_id": str(uuid4())})
        if not result.get("accepted"):
            raise device_sync.DeviceError("Device did not clear its session")
        conn = device_sync.database.open_database(settings.db_path)
        try:
            device_sync.migrate_device_schema(conn)
            conn.execute("UPDATE session_device_bindings SET cleared_at=? WHERE session_id=? AND device_id=? AND cleared_at IS NULL",
                         (datetime.now(timezone.utc).isoformat(), session_id, settings.device_id))
            conn.commit()
        finally:
            conn.close()
    except device_sync.DeviceError as exc:
        raise ApiError("DEVICE_DRAIN_FAILED", 409, "端末停止・未同期イベント回収を確認できないため授業回を終了できません") from exc
