"""Local ESP32 HTTP simulator for development and test; binds loopback only."""
from __future__ import annotations
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit
import uuid

STATE_PATH = Path(os.environ.get("RFID_MOCK_STATE", "mock-esp-state.json"))
DEVICE_ID = os.environ.get("RFID_DEVICE_ID", "mock-esp")
TOKEN = os.environ.get("RFID_DEVICE_TOKEN", "")
PORT = int(os.environ.get("RFID_MOCK_PORT", "8765"))
LOCK = threading.Lock()
REPLAY_NONCES: list[str] = []

def initial_state():
    return {"device_id": DEVICE_ID, "boot_id": uuid.uuid4().hex, "firmware_version": "mock-2.0",
            "state": "READY", "pending_events": [], "acked": 0, "last_sequence": 0,
            "reading_enabled": False, "active_session_id": 0, "session_generation": "", "cache_version": 0,
            "cache_sha256": "", "time_synced": False}

def load():
    try: return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError: return initial_state()

def save(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    temp.replace(STATE_PATH)

class Handler(BaseHTTPRequestHandler):
    server_version = "RfidMockESP/2"
    def log_message(self, fmt, *args): pass
    def authorized(self, body):
        if not TOKEN: return False
        timestamp = self.headers.get("X-Timestamp", "")
        nonce = self.headers.get("X-Nonce", "")
        if not timestamp.isdigit() or len(timestamp) < 13 or len(timestamp) > 16 or len(nonce) != 32: return False
        digest = hashlib.sha256(body).hexdigest()
        signature = hmac.new(TOKEN.encode(), f"{self.command}\n{self.path}\n{digest}\n{timestamp}\n{nonce}".encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(self.headers.get("X-Device-Id", ""), DEVICE_ID) or not hmac.compare_digest(self.headers.get("X-Signature", ""), signature): return False
        if abs(int(time.time() * 1000) - int(timestamp)) > 60000: return False
        with LOCK:
            if nonce in REPLAY_NONCES: return False
            REPLAY_NONCES.append(nonce)
            if len(REPLAY_NONCES) > 512: del REPLAY_NONCES[:-512]
        return True
    def send_json(self, value, status=200):
        data = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        body=b""
        if not self.authorized(body): return self.send_json({"error":"unauthorized"},401)
        state=load(); parts=urlsplit(self.path)
        if parts.path.endswith("/status"):
            result={k:v for k,v in state.items() if k!="pending_events"}; result["pending_events"]=len(state["pending_events"])
            result["acked_through_seq"]=state["acked"];result["time_quality"]="SYNCED" if state.get("time_synced") else "UNKNOWN"
            return self.send_json(result)
        if parts.path.endswith("/events"):
            q=parse_qs(parts.query); boot=q.get("boot_id",[""])[0]; after=int(q.get("after_seq",[0])[0]); limit=min(50,int(q.get("limit",[50])[0]))
            if boot!=state["boot_id"]: return self.send_json({"reset_required":True},409)
            items=[x for x in state["pending_events"] if x["sequence"]>after][:limit]
            return self.send_json({"boot_id":state["boot_id"],"events":items})
        self.send_json({"error":"not found"},404)
    def do_POST(self):
        length=int(self.headers.get("Content-Length","0"));
        if length>65536: return self.send_json({},413)
        body=self.rfile.read(length)
        if urlsplit(self.path).path=="/mock/scan":
            try: payload=json.loads(body or b"{}")
            except ValueError:return self.send_json({},400)
            uid=str(payload.get("uid"," ")).strip().upper()
            if len(uid) not in (8,14,20) or any(c not in "0123456789ABCDEF" for c in uid):return self.send_json({},422)
            state=load()
            if not state["reading_enabled"] or not state["active_session_id"]:return self.send_json({"error":"no active session"},409)
            state["last_sequence"]+=1;seq=state["last_sequence"]
            captured=datetime.now(timezone.utc).isoformat(timespec="seconds") if state.get("time_synced") else None
            quality="SYNCED" if captured else "UNKNOWN"
            state["pending_events"].append({"event_id":f"{DEVICE_ID}:{state['boot_id']}:{seq}","device_id":DEVICE_ID,"boot_id":state["boot_id"],"sequence":seq,"session_id":state["active_session_id"],"session_generation":state["session_generation"],"uid":uid,"captured_at":captured,"time_quality":quality})
            save(state);return self.send_json({"saved":True,"sequence":seq},201)
        if not self.authorized(body): return self.send_json({"error":"unauthorized"},401)
        try: payload=json.loads(body or b"{}")
        except ValueError: return self.send_json({},400)
        state=load(); path=urlsplit(self.path).path
        if path.endswith("/ack"):
            if payload.get("boot_id")!=state["boot_id"]: return self.send_json({},409)
            ack=int(payload.get("ack_through_seq",0))
            if ack<state["acked"] or ack>state["last_sequence"]: return self.send_json({},409)
            state["pending_events"]=[x for x in state["pending_events"] if x["sequence"]>ack]; state["acked"]=ack; save(state)
            return self.send_json({"acked_through_seq":ack})
        if path.endswith("/commands"):
            cmd=payload.get("command")
            if cmd=="BIND_SESSION": state["active_session_id"]=int(payload["session_id"]);state["session_generation"]=payload["session_generation"];state["reading_enabled"]=True
            elif cmd=="CLEAR_SESSION": state["reading_enabled"]=False;state["active_session_id"]=0;state["session_generation"]=""
            elif cmd=="PAUSE_READING": state["reading_enabled"]=False
            elif cmd=="RESUME_READING": state["reading_enabled"]=bool(state["active_session_id"])
            elif cmd=="SYNC_TIME": state["time_synced"]=True
            elif cmd not in {"SYNC_NOW","DIAGNOSTIC","REFRESH_CACHE"}: return self.send_json({},422)
            save(state);return self.send_json({"accepted":True,"request_id":payload.get("request_id")},202)
        self.send_json({},404)
    def do_PUT(self):
        length=int(self.headers.get("Content-Length","0"));
        if length>65536:return self.send_json({},413)
        body=self.rfile.read(length)
        if not self.authorized(body):return self.send_json({},401)
        payload=json.loads(body);state=load()
        digest=hashlib.sha256(json.dumps(payload["entries"],separators=(",", ":")).encode()).hexdigest()
        if not hmac.compare_digest(str(payload.get("sha256", "")), digest): return self.send_json({},422)
        if int(payload["version"]) < int(state["cache_version"]) or (int(payload["version"]) == int(state["cache_version"]) and state.get("cache_sha256") not in ("", digest)): return self.send_json({},409)
        state["cache_version"]=int(payload["version"]);state["cache_sha256"]=digest;save(state)
        self.send_json({"version":state["cache_version"],"sha256":digest})

if __name__=="__main__":
    if not TOKEN: raise SystemExit("RFID_DEVICE_TOKEN must be set; no default credential is used")
    server=ThreadingHTTPServer(("127.0.0.1",PORT),Handler)
    print(f"Mock ESP32 listening on http://127.0.0.1:{PORT}")
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
