"""RFID入力方式、UID検証、シリアル受信を提供する。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import time

import serial
from serial.tools import list_ports

try:
    from .database import ValidationError, normalize_uid
except ImportError:  # pragma: no cover - 直接実行時の補助
    from database import ValidationError, normalize_uid


EXIT_COMMAND = "Q"
DEFAULT_BAUDRATE = 115200
DEFAULT_TIMEOUT = 0.1
DEFAULT_READY_TIMEOUT = 5.0
MAX_LINE_LENGTH = 64

EVENT_READY = "READY"
EVENT_UID = "UID"
EVENT_ERROR = "ERROR"
EVENT_WARNING = "WARNING"


class ReaderError(Exception):
    """RFID入力機器を呼び出し側で扱うための基底例外。"""


class ReaderConnectionError(ReaderError):
    """ポートのオープン、読み取り、書き込みに失敗した。"""


class ReaderInitializationError(ReaderError):
    """ArduinoからREADYを受信できなかった。"""


@dataclass(frozen=True)
class ReaderEvent:
    """シリアル1行を解釈した結果。"""

    kind: str
    uid: str | None = None
    message: str | None = None
    raw: str | None = None


class MockReader:
    """キーボードからUIDを受け取るPhase 3用の入力クラス。"""

    def __init__(self, input_func: Callable[[str], str] = input) -> None:
        self._input_func = input_func

    def read_uid(self) -> str | None:
        """UIDを正規化して返す。Q入力時はNoneを返す。"""

        raw_uid = self._input_func(
            "UIDを入力してください（Qでメニューへ戻る）："
        )
        if raw_uid.strip().upper() == EXIT_COMMAND:
            return None

        try:
            return normalize_uid(raw_uid)
        except ValidationError:
            # 不正UIDもdatabase.register_attendanceへ渡し、
            # 呼び出し側でINVALID_UIDとして表示できるようにする。
            return raw_uid


def list_serial_ports() -> list[dict[str, str]]:
    """現在列挙できるシリアルポートを返す。"""

    return [
        {
            "device": port.device,
            "description": port.description or "",
            "hwid": port.hwid or "",
        }
        for port in list_ports.comports()
    ]


class SerialReader:
    """ArduinoからLF終端のREADY/UID/ERR行を受信する。"""

    def __init__(
        self,
        port: str,
        baudrate: int = DEFAULT_BAUDRATE,
        timeout: float = DEFAULT_TIMEOUT,
        ready_timeout: float = DEFAULT_READY_TIMEOUT,
        serial_factory: Callable[..., object] = serial.serial_for_url,
    ) -> None:
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.ready_timeout = ready_timeout
        self._serial_factory = serial_factory
        self._serial: object | None = None
        self._ready = False

    @property
    def is_ready(self) -> bool:
        return self._ready

    def __enter__(self) -> "SerialReader":
        self.open()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def open(self) -> None:
        """ポートを開き、HELLO/READYハンドシェイクを完了する。"""

        if self._serial is not None:
            return
        try:
            self._serial = self._serial_factory(
                self.port,
                baudrate=self.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=self.timeout,
                write_timeout=self.timeout,
            )
            self._reset_input_buffer()
            self._wait_for_ready()
        except ReaderError:
            self.close()
            raise
        except (OSError, serial.SerialException) as exc:
            self.close()
            raise ReaderConnectionError(
                f"シリアルポートを利用できません: {self.port}"
            ) from exc

    def close(self) -> None:
        """シリアルポートを確実に閉じる。"""

        if self._serial is not None:
            try:
                self._serial.close()  # type: ignore[attr-defined]
            finally:
                self._serial = None
                self._ready = False

    def read_event(self, timeout: float | None = None) -> ReaderEvent | None:
        """1行を読み取り、イベントへ変換する。データなしはNone。"""

        self._ensure_open()
        try:
            line = self._read_line(self.timeout if timeout is None else timeout)
        except (OSError, serial.SerialException) as exc:
            raise ReaderConnectionError("シリアル受信に失敗しました") from exc
        except ReaderError as exc:
            return ReaderEvent(EVENT_WARNING, message=str(exc))

        if line is None:
            return None
        return self._parse_line(line)

    def _ensure_open(self) -> None:
        if self._serial is None:
            raise ReaderConnectionError("シリアルポートが開かれていません")

    def _reset_input_buffer(self) -> None:
        self._ensure_open()
        reset_input_buffer = getattr(self._serial, "reset_input_buffer", None)
        if reset_input_buffer is not None:
            reset_input_buffer()

    def _write_hello(self) -> None:
        self._ensure_open()
        try:
            self._serial.write(b"HELLO\n")  # type: ignore[attr-defined]
            flush = getattr(self._serial, "flush", None)
            if flush is not None:
                flush()
        except (OSError, serial.SerialException) as exc:
            raise ReaderConnectionError("HELLO送信に失敗しました") from exc

    def _wait_for_ready(self) -> None:
        start = time.monotonic()
        deadline = start + self.ready_timeout
        next_hello = start + 0.2
        hello_attempts = 0

        while time.monotonic() < deadline:
            event = self.read_event(timeout=self.timeout)
            if event is not None:
                if event.kind == EVENT_READY:
                    self._ready = True
                    return
                if event.kind == EVENT_ERROR:
                    raise ReaderInitializationError(
                        event.message or "ArduinoのRFID初期化に失敗しました"
                    )
                # READY確認前のUIDや不正行は破棄する。

            now = time.monotonic()
            if now >= next_hello and hello_attempts < 3:
                self._write_hello()
                hello_attempts += 1
                next_hello = now + 0.5

        raise ReaderInitializationError(
            f"READYを受信できませんでした: {self.port}"
        )

    def _read_line(self, timeout: float) -> str | None:
        self._ensure_open()
        deadline = time.monotonic() + max(0.0, timeout)
        data = bytearray()

        while time.monotonic() < deadline:
            chunk = self._serial.read(1)  # type: ignore[attr-defined]
            if not chunk:
                if not data:
                    return None
                raise ReaderError("シリアル行がLFで終端されていません")
            if chunk == b"\n":
                try:
                    return data.decode("ascii")
                except UnicodeDecodeError as exc:
                    self._reset_input_buffer()
                    raise ReaderError("ASCII以外のシリアルデータを受信しました") from exc
            if chunk == b"\r":
                continue
            if len(data) >= MAX_LINE_LENGTH:
                self._reset_input_buffer()
                raise ReaderError("シリアル行が64バイトを超えています")
            data.extend(chunk)

        if data:
            raise ReaderError("シリアル行の受信がタイムアウトしました")
        return None

    @staticmethod
    def _parse_line(line: str) -> ReaderEvent:
        if line == "READY":
            return ReaderEvent(EVENT_READY, raw=line)
        if line.startswith("UID:"):
            raw_uid = line[4:]
            try:
                normalized_uid = normalize_uid(raw_uid)
            except ValidationError:
                return ReaderEvent(
                    EVENT_WARNING,
                    message="不正なUIDを受信したため無視しました",
                    raw=line,
                )
            return ReaderEvent(EVENT_UID, uid=normalized_uid, raw=line)
        if line.startswith("ERR:"):
            return ReaderEvent(EVENT_ERROR, message=line, raw=line)
        return ReaderEvent(
            EVENT_WARNING,
            message="仕様外のシリアルメッセージを無視しました",
            raw=line,
        )


__all__ = [
    "DEFAULT_BAUDRATE",
    "DEFAULT_READY_TIMEOUT",
    "DEFAULT_TIMEOUT",
    "EVENT_ERROR",
    "EVENT_READY",
    "EVENT_UID",
    "EVENT_WARNING",
    "EXIT_COMMAND",
    "MAX_LINE_LENGTH",
    "MockReader",
    "ReaderConnectionError",
    "ReaderEvent",
    "ReaderError",
    "ReaderInitializationError",
    "SerialReader",
    "list_serial_ports",
]
