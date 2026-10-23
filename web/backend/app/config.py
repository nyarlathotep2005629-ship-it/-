"""Web版の設定。既定値はローカル運用専用とする。"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import sys
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@dataclass(frozen=True)
class Settings:
    """実行時設定。テストでは一時DBを明示的に渡す。"""

    db_path: Path = PROJECT_ROOT / "attendance.db"
    host: str = "127.0.0.1"
    port: int = 8000
    frontend_origin: str = "http://127.0.0.1:5173"
    serial_baudrate: int = 115200
    serial_timeout: float = 0.1
    serial_ready_timeout: float = 5.0
    stop_timeout: float = 3.0
    event_buffer_size: int = 200
    server_instance_id: str = ""

    def __post_init__(self) -> None:
        if not self.server_instance_id:
            object.__setattr__(self, "server_instance_id", str(uuid4()))


def settings_from_environment() -> Settings:
    """明示的な環境変数だけを上書きとして受け付ける。"""

    db_value = os.environ.get("RFID_ATTENDANCE_DB")
    return Settings(db_path=Path(db_value) if db_value else PROJECT_ROOT / "attendance.db")
