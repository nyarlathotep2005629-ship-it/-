"""Portable Windows exhibition launcher and local DPAPI pairing helper."""
from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
import getpass
import json
import logging
import os
from pathlib import Path
import re
import sys
import threading
import time
import urllib.request
import webbrowser


FROZEN = bool(getattr(sys, "frozen", False))
ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else ROOT
DATA = APP_DIR / "Data" / "Showcase"
LOG_DIR = DATA / "Logs"
PAIRING_FILE = DATA / "device-pairing.json"
URL = "http://127.0.0.1:8000/"
HEALTH_URL = "http://127.0.0.1:8000/api/v1/health"
DEVICE_URL = "http://192.168.4.1"


class DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _dpapi(data: bytes, *, protect: bool) -> bytes:
    if os.name != "nt":
        raise RuntimeError("展示用ペアリング設定はWindowsでのみ作成・読込できます")
    source_buffer = ctypes.create_string_buffer(data)
    source = DataBlob(len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_byte)))
    target = DataBlob()
    if protect:
        call = ctypes.WinDLL("crypt32", use_last_error=True).CryptProtectData
        call.argtypes = [ctypes.POINTER(DataBlob), wintypes.LPCWSTR, ctypes.POINTER(DataBlob),
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DataBlob)]
        call.restype = wintypes.BOOL
        ok = call(ctypes.byref(source), "RFID Showcase device token", None, None, None, 1, ctypes.byref(target))
    else:
        call = ctypes.WinDLL("crypt32", use_last_error=True).CryptUnprotectData
        call.argtypes = [ctypes.POINTER(DataBlob), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(DataBlob),
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DataBlob)]
        call.restype = wintypes.BOOL
        ok = call(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target))
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        local_free = ctypes.WinDLL("kernel32", use_last_error=True).LocalFree
        local_free.argtypes = [ctypes.c_void_p]
        local_free.restype = ctypes.c_void_p
        local_free(target.pbData)


def _load_pairing() -> dict[str, str] | None:
    if not PAIRING_FILE.exists():
        return None
    item = json.loads(PAIRING_FILE.read_text(encoding="utf-8"))
    device_id = item.get("device_id", "")
    device_url = item.get("device_url", "")
    encrypted = item.get("token_dpapi", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", device_id) or device_url != DEVICE_URL or not encrypted:
        raise ValueError("端末ペアリング設定の形式が不正です")
    token = _dpapi(base64.b64decode(encrypted, validate=True), protect=False).decode("ascii")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", token):
        raise ValueError("復号した端末tokenの形式が不正です")
    return {"device_id": device_id, "device_url": device_url, "device_token": token}


def _pair_device() -> int:
    device_id = input("ESP32のDevice ID: ").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", device_id):
        print("Device IDは英数字、ハイフン、アンダースコアの1～32文字です。")
        return 2
    token = getpass.getpass("ESP32の64桁token: ").strip()
    if not re.fullmatch(r"[0-9a-fA-F]{64}", token):
        print("tokenは64桁の16進数で入力してください。")
        return 2
    protected = base64.b64encode(_dpapi(token.encode("ascii"), protect=True)).decode("ascii")
    payload = json.dumps({"device_id": device_id, "device_url": DEVICE_URL, "token_dpapi": protected}, indent=2)
    temporary = PAIRING_FILE.with_suffix(".tmp")
    temporary.write_text(payload, encoding="utf-8")
    os.replace(temporary, PAIRING_FILE)
    print("ペアリング設定をこのWindowsユーザー用に保存しました。tokenは表示・ログ出力していません。")
    return 0


def main() -> int:
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        LOG_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"展示フォルダへ書き込めません: {exc}")
        return 2
    logging.basicConfig(filename=LOG_DIR / "launcher.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", encoding="utf-8")

    if "--pair-device" in sys.argv[1:]:
        try:
            return _pair_device()
        except Exception:
            logging.exception("ESP32 pairing setup failed")
            print(f"ペアリング設定に失敗しました。{LOG_DIR / 'launcher.log'}を確認してください。")
            return 2

    try:
        pairing = _load_pairing()
    except Exception:
        logging.exception("ESP32 pairing configuration could not be read")
        print(f"ペアリング設定を読み込めません。{LOG_DIR / 'launcher.log'}を確認してください。")
        return 2

    os.environ["RFID_ATTENDANCE_MODE"] = "showcase"
    os.environ["RFID_ATTENDANCE_DB"] = str(DATA / "attendance.db")
    if pairing:
        os.environ["RFID_DEVICE_ID"] = pairing["device_id"]
        os.environ["RFID_DEVICE_URL"] = pairing["device_url"]
        os.environ["RFID_DEVICE_TOKEN"] = pairing["device_token"]
    else:
        logging.warning("ESP32 is not paired; the local showcase database will start without device sync")

    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "web" / "backend"))
    try:
        import uvicorn
        from app.main import app
    except Exception:
        logging.exception("Application import failed")
        print(f"アプリの読込に失敗しました。{LOG_DIR / 'launcher.log'}を確認してください。")
        return 2

    # Keep Uvicorn diagnostics in the on-disk launcher log for managed PCs.
    config = uvicorn.Config(app, host="127.0.0.1", port=8000, log_level="info",
                            access_log=False, log_config=None, loop="asyncio",
                            http="h11", ws="none")
    logging.info("Loading Uvicorn application configuration")
    config.load()
    logging.info("Uvicorn application configuration loaded")
    server = uvicorn.Server(config)

    def run_server() -> None:
        logging.info("Backend thread entered")
        try:
            server.run()
        except BaseException:
            logging.exception("Backend thread failed")
            raise

    thread = threading.Thread(target=run_server, name="showcase-api", daemon=True)
    thread.start()
    logging.info("Backend thread started")
    for _ in range(120):
        if not thread.is_alive():
            logging.error("Backend stopped before becoming ready; port 8000 may be occupied")
            print(f"起動に失敗しました。ポート8000と{LOG_DIR}のログを確認してください。")
            return 1
        if server.started:
            try:
                with urllib.request.urlopen(HEALTH_URL, timeout=0.5) as response:
                    payload = json.loads(response.read(4096))
                if response.status == 200 and payload.get("status") == "ok":
                    break
            except Exception:
                pass
        time.sleep(0.25)
    else:
        server.should_exit = True
        thread.join(timeout=10)
        logging.error("Backend readiness timed out; thread_alive=%s server_started=%s",
                      thread.is_alive(), server.started)
        print(f"準備確認がタイムアウトしました。{LOG_DIR}のログを確認してください。")
        return 1

    print(f"展示モード起動: {URL}")
    if not pairing:
        print("ESP32未ペアリングです。展示前にPair-Device.cmdを一度実行してください。")
    print("停止するにはこの画面で Enter を押してください。")
    if os.environ.get("RFID_SHOWCASE_NO_BROWSER") != "1":
        webbrowser.open(URL)
    try:
        input()
    except (EOFError, KeyboardInterrupt):
        pass
    server.should_exit = True
    thread.join(timeout=10)
    return 0 if not thread.is_alive() else 2


if __name__ == "__main__":
    raise SystemExit(main())
