"""Smoke-test a built portable package without opening a browser or using hardware."""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time
from urllib.request import urlopen


def _assert_port_available(port: int) -> None:
    with socket.socket() as listener:
        try:
            listener.bind(("127.0.0.1", port))
        except OSError as exc:
            raise RuntimeError(f"127.0.0.1:{port} is already in use") from exc


def _run_once(executable: Path, *, via_cmd: bool = False) -> bytes:
    env = os.environ.copy()
    env["RFID_SHOWCASE_NO_BROWSER"] = "1"
    if via_cmd:
        launcher = executable.parent / "Start-Showcase.cmd"
        if not launcher.is_file():
            raise RuntimeError(f"CMD launcher not found: {launcher}")
        # Run the ASCII batch filename from its own directory. Passing its full
        # Unicode path through cmd.exe can be lossy on legacy Windows code pages.
        command = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", "call", launcher.name]
    else:
        command = [str(executable)]
    process = subprocess.Popen(
        command, cwd=executable.parent, env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    try:
        for _ in range(80):
            if process.poll() is not None:
                output, _ = process.communicate(timeout=2)
                log_path = executable.parent / "Data" / "Showcase" / "Logs" / "launcher.log"
                log_text = log_path.read_text(encoding="utf-8") if log_path.exists() else "(no launcher log)"
                raise RuntimeError(
                    f"launcher exited before readiness: {process.returncode}\n"
                    f"{output.decode(errors='replace')}\n{log_text}"
                )
            try:
                with urlopen("http://127.0.0.1:8000/api/v1/health", timeout=0.5) as response:
                    health = json.loads(response.read(4096))
                if health.get("status") != "ok":
                    raise RuntimeError("unexpected health response")
                with urlopen("http://127.0.0.1:8000/", timeout=1) as response:
                    html = response.read(65536)
                if b"assets/" not in html:
                    raise RuntimeError("React build assets are not being served")
                break
            except OSError:
                time.sleep(0.25)
        else:
            if process.stdin is not None:
                process.stdin.write(b"\n")
                process.stdin.flush()
                process.stdin.close()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            output = process.stdout.read() if process.stdout is not None else b""
            log_path = executable.parent / "Data" / "Showcase" / "Logs" / "launcher.log"
            log_text = log_path.read_text(encoding="utf-8") if log_path.exists() else "(no launcher log)"
            raise RuntimeError(
                f"portable application readiness timed out\n"
                f"{output.decode(errors='replace')}\n{log_text}"
            )
        assert process.stdin is not None
        process.stdin.write(b"\n")
        process.stdin.flush()
        process.stdin.close()
        process.wait(timeout=15)
        if process.returncode != 0:
            raise RuntimeError(f"launcher returned {process.returncode}")
        if process.stdout is not None:
            process.stdout.read()
        return html
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def _counts(db_path: Path) -> tuple[int, int, int]:
    with sqlite3.connect(db_path) as connection:
        return tuple(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                     for table in ("students", "courses", "enrollments"))


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: python tools/test_showcase_package.py <RFIDShowcase.exe>")
    executable = Path(sys.argv[1]).resolve()
    if not executable.is_file():
        raise SystemExit(f"package executable not found: {executable}")
    _assert_port_available(8000)
    data_dir = executable.parent / "Data" / "Showcase"
    db_path = data_dir / "attendance.db"
    counts = []
    for via_cmd in (False, True):
        _run_once(executable, via_cmd=via_cmd)
        counts.append(_counts(db_path))
        _run_once(executable, via_cmd=via_cmd)
        counts.append(_counts(db_path))
    if any(item != counts[0] for item in counts[1:]) or counts[0][0] < 1 or counts[0][1] < 1 or counts[0][2] < 1:
        raise SystemExit(f"demo database was not retained: counts={counts}")
    for log_name in ("launcher.log", "application.log"):
        if not (data_dir / "Logs" / log_name).is_file():
            raise SystemExit(f"expected startup log was not created: {log_name}")
    print(f"PASS: EXE and CMD startup, HTTP readiness, React assets, Enter-to-stop, demo DB persistence {counts[0]}, logs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
