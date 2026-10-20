"""RFID出欠管理システムのPhase 3 CLI。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pc.database import (  # noqa: E402
    ATTENDANCE_ALREADY_REGISTERED,
    ATTENDANCE_INVALID_UID,
    ATTENDANCE_NO_ACTIVE_SESSION,
    ATTENDANCE_NOT_ENROLLED,
    ATTENDANCE_STORAGE_ERROR,
    ATTENDANCE_SUCCESS,
    ATTENDANCE_UNKNOWN_CARD,
    DatabaseError,
    DuplicateError,
    EnrollmentLockedError,
    InvalidStateError,
    NotFoundError,
    ValidationError,
    close_session,
    create_course,
    create_student,
    enroll_student,
    get_attendance_report,
    get_attendance_summary,
    get_open_session,
    list_courses,
    list_enrollments,
    list_sessions,
    list_students,
    open_database,
    register_attendance,
    start_session,
    unenroll_student,
)
from pc.reader import (  # noqa: E402
    EVENT_ERROR,
    EVENT_UID,
    EVENT_WARNING,
    MockReader,
    ReaderConnectionError,
    ReaderInitializationError,
    SerialReader,
    list_serial_ports,
)


DB_PATH = PROJECT_ROOT / "attendance.db"
INPUT_MODE_MOCK = "MOCK"
INPUT_MODE_SERIAL = "SERIAL"
JST = timezone(timedelta(hours=9), name="Asia/Tokyo")

InputFunc = Callable[[str], str]
OutputFunc = Callable[[str], None]


ATTENDANCE_MESSAGES = {
    ATTENDANCE_SUCCESS: "出席登録成功",
    ATTENDANCE_ALREADY_REGISTERED: "登録済み",
    ATTENDANCE_UNKNOWN_CARD: "未登録カード",
    ATTENDANCE_NOT_ENROLLED: "履修対象外",
    ATTENDANCE_NO_ACTIVE_SESSION: "受付中セッションなし",
    ATTENDANCE_INVALID_UID: "不正UID",
    ATTENDANCE_STORAGE_ERROR: "DB保存エラー",
}

STATUS_LABELS = {
    "PRESENT": "出席",
    "PENDING": "未受付",
    "ABSENT": "欠席",
}


def _read_choice(
    prompt: str,
    input_func: InputFunc,
    output_func: OutputFunc,
) -> int | None:
    """数値選択を読み取り、形式不正時はNoneを返す。"""

    raw_value = input_func(prompt).strip()
    try:
        return int(raw_value)
    except ValueError:
        output_func("数字を入力してください。")
        return None


def _read_text(
    prompt: str,
    input_func: InputFunc,
) -> str | None:
    """空入力またはQをキャンセルとして扱う。"""

    value = input_func(prompt).strip()
    if not value or value.upper() == "Q":
        return None
    return value


def _confirm(
    prompt: str,
    input_func: InputFunc,
    output_func: OutputFunc,
) -> bool:
    """Y/はいだけを確定として扱う。"""

    answer = input_func(f"{prompt} (Y/N)：").strip().upper()
    if answer in {"Y", "YES", "はい"}:
        return True
    if answer not in {"N", "NO", "いいえ", ""}:
        output_func("YまたはNを入力してください。操作をキャンセルします。")
    return False


def _keyboard_q_pressed() -> bool:
    """WindowsコンソールからQを非ブロッキングで検出する。"""

    try:
        import msvcrt
    except ImportError:  # pragma: no cover - Windows CLIでは使用される
        return False

    q_pressed = False
    while msvcrt.kbhit():
        key = msvcrt.getwch()
        if key.upper() == "Q":
            q_pressed = True
    return q_pressed


def _format_jst(timestamp: str | None) -> str:
    """UTC ISO 8601時刻を日本時間表示へ変換する。"""

    if not timestamp:
        return "-"
    try:
        parsed = datetime.fromisoformat(timestamp)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(JST).strftime("%Y/%m/%d %H:%M:%S")
    except ValueError:
        return timestamp


def _course_name(connection: sqlite3.Connection, course_id: int) -> str:
    for course in list_courses(connection):
        if course["course_id"] == course_id:
            return course["course_name"]
    return f"(授業ID {course_id})"


def _print_students(
    connection: sqlite3.Connection,
    output_func: OutputFunc,
) -> list[dict[str, Any]]:
    students = list_students(connection)
    if not students:
        output_func("学生は登録されていません。")
        return students

    output_func("学生一覧")
    for student in students:
        output_func(
            f"  ID={student['student_id']}  "
            f"名前={student['name']}  UID={student['card_uid']}"
        )
    return students


def _print_courses(
    connection: sqlite3.Connection,
    output_func: OutputFunc,
) -> list[dict[str, Any]]:
    courses = list_courses(connection)
    if not courses:
        output_func("授業は登録されていません。")
        return courses

    output_func("授業一覧")
    for course in courses:
        output_func(
            f"  ID={course['course_id']}  名前={course['course_name']}"
        )
    return courses


def _select_course(
    connection: sqlite3.Connection,
    input_func: InputFunc,
    output_func: OutputFunc,
) -> dict[str, Any] | None:
    courses = list_courses(connection)
    if not courses:
        output_func("授業がありません。先に授業を登録してください。")
        return None

    while True:
        _print_courses(connection, output_func)
        course_id = _read_choice(
            "授業IDを入力してください（0で戻る）：",
            input_func,
            output_func,
        )
        if course_id is None:
            continue
        if course_id == 0:
            return None
        for course in courses:
            if course["course_id"] == course_id:
                return course
        output_func("存在しない授業IDです。")


def _select_student(
    connection: sqlite3.Connection,
    input_func: InputFunc,
    output_func: OutputFunc,
    students: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    students = list_students(connection) if students is None else students
    if not students:
        output_func("学生がありません。先に学生を登録してください。")
        return None

    while True:
        for student in students:
            output_func(
                f"  ID={student['student_id']}  "
                f"名前={student['name']}  UID={student['card_uid']}"
            )
        student_id = _read_choice(
            "学生IDを入力してください（0で戻る）：",
            input_func,
            output_func,
        )
        if student_id is None:
            continue
        if student_id == 0:
            return None
        for student in students:
            if student["student_id"] == student_id:
                return student
        output_func("存在しない学生IDです。")


def _read_card_uid(
    mode: str,
    serial_port: str | None,
    input_func: InputFunc,
    output_func: OutputFunc,
) -> str | None:
    """現在の入力モードで学生登録用のUIDを1件読み取る。"""

    if mode == INPUT_MODE_MOCK:
        return MockReader(input_func).read_uid()

    if not serial_port:
        output_func("SERIALモードのCOMポートが選択されていません。")
        return None

    try:
        with SerialReader(serial_port) as reader:
            output_func(
                f"{serial_port}へ接続しました。カードを読み取ってください。"
            )
            while True:
                if _keyboard_q_pressed():
                    output_func("カード読み取りをキャンセルしました。")
                    return None
                event = reader.read_event()
                if event is None:
                    continue
                if event.kind == EVENT_UID:
                    return event.uid
                if event.kind == EVENT_ERROR:
                    output_func(f"Arduinoエラー：{event.message}")
                    return None
                if event.kind == EVENT_WARNING:
                    output_func(f"シリアル受信警告：{event.message}")
    except ReaderInitializationError as exc:
        output_func(f"Arduino準備エラー：{exc}")
    except ReaderConnectionError as exc:
        output_func(f"シリアル通信エラー：{exc}")
    return None


def _student_menu(
    connection: sqlite3.Connection,
    input_func: InputFunc,
    output_func: OutputFunc,
    mode: str,
    serial_port: str | None,
) -> None:
    while True:
        output_func("\n--- 学生管理 ---")
        output_func("1. 学生登録")
        output_func("2. 学生一覧")
        output_func("0. 戻る")
        choice = _read_choice("選択：", input_func, output_func)
        if choice == 0:
            return
        if choice == 1:
            name = _read_text("学生名（Qでキャンセル）：", input_func)
            if name is None:
                output_func("登録をキャンセルしました。")
                continue
            output_func("UIDを読み取ります。")
            card_uid = _read_card_uid(
                mode,
                serial_port,
                input_func,
                output_func,
            )
            if card_uid is None:
                output_func("登録をキャンセルしました。")
                continue
            output_func(f"登録内容：名前={name} UID={card_uid}")
            if not _confirm("この内容で登録しますか", input_func, output_func):
                output_func("登録をキャンセルしました。")
                continue
            try:
                student_id = create_student(connection, name, card_uid)
            except DuplicateError:
                output_func("そのUIDはすでに登録されています。")
            except ValidationError as exc:
                output_func(f"入力エラー：{exc}")
            except DatabaseError as exc:
                output_func(f"DBエラー：{exc}")
            else:
                output_func(f"学生を登録しました。学生ID={student_id}")
        elif choice == 2:
            try:
                _print_students(connection, output_func)
            except sqlite3.Error as exc:
                output_func(f"DBエラー：{exc}")
        elif choice is not None:
            output_func("選択肢が正しくありません。")


def _course_menu(
    connection: sqlite3.Connection,
    input_func: InputFunc,
    output_func: OutputFunc,
) -> None:
    while True:
        output_func("\n--- 授業管理 ---")
        output_func("1. 授業登録")
        output_func("2. 授業一覧")
        output_func("0. 戻る")
        choice = _read_choice("選択：", input_func, output_func)
        if choice == 0:
            return
        if choice == 1:
            course_name = _read_text(
                "授業名（Qでキャンセル）：",
                input_func,
            )
            if course_name is None:
                output_func("登録をキャンセルしました。")
                continue
            if not _confirm("この授業名で登録しますか", input_func, output_func):
                output_func("登録をキャンセルしました。")
                continue
            try:
                course_id = create_course(connection, course_name)
            except ValidationError as exc:
                output_func(f"入力エラー：{exc}")
            except DatabaseError as exc:
                output_func(f"DBエラー：{exc}")
            else:
                output_func(f"授業を登録しました。授業ID={course_id}")
        elif choice == 2:
            try:
                _print_courses(connection, output_func)
            except sqlite3.Error as exc:
                output_func(f"DBエラー：{exc}")
        elif choice is not None:
            output_func("選択肢が正しくありません。")


def _enrollment_menu(
    connection: sqlite3.Connection,
    input_func: InputFunc,
    output_func: OutputFunc,
) -> None:
    while True:
        output_func("\n--- 履修管理 ---")
        output_func("1. 履修登録")
        output_func("2. 履修解除")
        output_func("3. 履修者一覧")
        output_func("0. 戻る")
        choice = _read_choice("選択：", input_func, output_func)
        if choice == 0:
            return
        if choice not in {1, 2, 3}:
            if choice is not None:
                output_func("選択肢が正しくありません。")
            continue

        course = _select_course(connection, input_func, output_func)
        if course is None:
            continue
        course_id = course["course_id"]

        if choice == 3:
            try:
                enrollments = list_enrollments(connection, course_id)
            except DatabaseError as exc:
                output_func(f"DBエラー：{exc}")
                continue
            if not enrollments:
                output_func("履修者はいません。")
            else:
                output_func(f"{course['course_name']}の履修者")
                for student in enrollments:
                    output_func(
                        f"  ID={student['student_id']}  名前={student['name']}  "
                        f"UID={student['card_uid']}"
                    )
            continue

        output_func("学生を選択してください。")
        student = _select_student(connection, input_func, output_func)
        if student is None:
            continue
        try:
            if choice == 1:
                enroll_student(connection, student["student_id"], course_id)
                output_func("履修登録しました。")
            else:
                unenroll_student(connection, student["student_id"], course_id)
                output_func("履修解除しました。")
        except DuplicateError:
            output_func("その学生はすでに履修登録されています。")
        except EnrollmentLockedError:
            output_func("授業回が作成済みのため、履修者を変更できません。")
        except NotFoundError as exc:
            output_func(f"対象が見つかりません：{exc}")
        except DatabaseError as exc:
            output_func(f"DBエラー：{exc}")


def _print_current_session(
    connection: sqlite3.Connection,
    output_func: OutputFunc,
) -> dict[str, Any] | None:
    session = get_open_session(connection)
    if session is None:
        output_func("現在の受付：なし")
        return None
    output_func(
        f"現在の受付：{_course_name(connection, session['course_id'])} "
        f"第{session['session_number']}回 "
        f"（開始 {_format_jst(session['started_at'])}）"
    )
    return session


def _print_attendance_result(
    result: dict[str, Any],
    output_func: OutputFunc,
) -> None:
    status = result.get("status")
    message = ATTENDANCE_MESSAGES.get(status, "不明な受付結果")
    student_name = result.get("student_name")
    if student_name:
        output_func(f"{student_name}：{message}")
    else:
        output_func(message)
    if status == ATTENDANCE_STORAGE_ERROR and result.get("error"):
        output_func(f"詳細：{result['error']}")


def _print_attendance_count(
    connection: sqlite3.Connection,
    session_id: int,
    output_func: OutputFunc,
) -> None:
    summary = get_attendance_summary(connection, session_id)
    output_func(
        f"受付人数：{summary['present_count']} / "
        f"{summary['enrolled_count']}"
    )


def _run_mock_attendance(
    connection: sqlite3.Connection,
    session: dict[str, Any],
    input_func: InputFunc,
    output_func: OutputFunc,
) -> None:
    """OPENセッションに対してMOCK UID受付を行う。"""

    reader = MockReader(input_func)
    output_func("\n================================")
    output_func(" 出席受付中（MOCK）")
    output_func("================================")
    output_func(
        f"授業：{_course_name(connection, session['course_id'])} "
        f"第{session['session_number']}回"
    )
    output_func(f"開始：{_format_jst(session['started_at'])}")
    _print_attendance_count(connection, session["session_id"], output_func)
    output_func("Q：読み取り画面を終了")

    while True:
        current = get_open_session(connection)
        if current is None or current["session_id"] != session["session_id"]:
            output_func("受付中セッションがありません。")
            return
        card_uid = reader.read_uid()
        if card_uid is None:
            output_func("受付画面を終了しました。受付はOPENのままです。")
            return
        result = register_attendance(
            connection,
            session["session_id"],
            card_uid,
        )
        _print_attendance_result(result, output_func)
        try:
            _print_attendance_count(connection, session["session_id"], output_func)
        except (DatabaseError, sqlite3.Error) as exc:
            output_func(f"集計取得エラー：{exc}")


def _run_serial_attendance(
    connection: sqlite3.Connection,
    session: dict[str, Any],
    serial_port: str | None,
    output_func: OutputFunc,
) -> None:
    """SERIALモードでUIDを受信し、既存の出席登録へ渡す。"""

    if not serial_port:
        output_func("SERIALモードのCOMポートが選択されていません。")
        return

    output_func("\n================================")
    output_func(" 出席受付中（SERIAL）")
    output_func("================================")
    output_func(
        f"授業：{_course_name(connection, session['course_id'])} "
        f"第{session['session_number']}回"
    )
    output_func(f"開始：{_format_jst(session['started_at'])}")
    _print_attendance_count(connection, session["session_id"], output_func)
    output_func(f"Arduino：{serial_port}")
    output_func("Q：読み取り画面を終了")

    try:
        with SerialReader(serial_port) as reader:
            while True:
                if _keyboard_q_pressed():
                    output_func("受付画面を終了しました。受付はOPENのままです。")
                    return

                current = get_open_session(connection)
                if current is None or current["session_id"] != session["session_id"]:
                    output_func("受付中セッションがありません。")
                    return

                event = reader.read_event()
                if event is None:
                    continue
                if event.kind == EVENT_UID:
                    result = register_attendance(
                        connection,
                        session["session_id"],
                        event.uid,
                    )
                    _print_attendance_result(result, output_func)
                    try:
                        _print_attendance_count(
                            connection,
                            session["session_id"],
                            output_func,
                        )
                    except (DatabaseError, sqlite3.Error) as exc:
                        output_func(f"集計取得エラー：{exc}")
                elif event.kind == EVENT_ERROR:
                    output_func(f"Arduinoエラー：{event.message}")
                    output_func("シリアル受付を終了します。受付はOPENのままです。")
                    return
                elif event.kind == EVENT_WARNING:
                    output_func(f"シリアル受信警告：{event.message}")
    except ReaderInitializationError as exc:
        output_func(f"Arduino準備エラー：{exc}")
    except ReaderConnectionError as exc:
        output_func(f"シリアル通信エラー：{exc}")


def _run_attendance(
    connection: sqlite3.Connection,
    session: dict[str, Any],
    mode: str,
    serial_port: str | None,
    input_func: InputFunc,
    output_func: OutputFunc,
) -> None:
    if mode == INPUT_MODE_SERIAL:
        _run_serial_attendance(
            connection,
            session,
            serial_port,
            output_func,
        )
    else:
        _run_mock_attendance(
            connection,
            session,
            input_func,
            output_func,
        )


def _close_current_session(
    connection: sqlite3.Connection,
    input_func: InputFunc,
    output_func: OutputFunc,
) -> bool:
    session = get_open_session(connection)
    if session is None:
        output_func("現在OPENの受付はありません。")
        return False

    output_func(
        f"終了対象：{_course_name(connection, session['course_id'])} "
        f"第{session['session_number']}回"
    )
    if not _confirm("受付を終了しますか", input_func, output_func):
        output_func("受付終了をキャンセルしました。")
        return False
    try:
        close_session(connection, session["session_id"])
        summary = get_attendance_summary(connection, session["session_id"])
    except (DatabaseError, sqlite3.Error) as exc:
        output_func(f"受付終了エラー：{exc}")
        return False
    output_func("受付を終了しました。")
    output_func(
        f"出席：{summary['present_count']}名 / "
        f"履修者：{summary['enrolled_count']}名、"
        f"欠席：{summary['absent_count']}名"
    )
    return True


def _start_or_resume_attendance(
    connection: sqlite3.Connection,
    input_func: InputFunc,
    output_func: OutputFunc,
    mode: str,
    serial_port: str | None,
) -> None:
    open_session = get_open_session(connection)
    if open_session is not None:
        output_func("OPENセッションが存在します。")
        output_func(
            f"{_course_name(connection, open_session['course_id'])} "
            f"第{open_session['session_number']}回"
        )
        output_func("1. 受付を再開")
        output_func("2. 受付を終了")
        output_func("3. メニューへ戻る")
        choice = _read_choice("選択：", input_func, output_func)
        if choice == 1:
            _run_attendance(
                connection,
                open_session,
                mode,
                serial_port,
                input_func,
                output_func,
            )
        elif choice == 2:
            _close_current_session(connection, input_func, output_func)
        elif choice != 3 and choice is not None:
            output_func("選択肢が正しくありません。")
        return

    course = _select_course(connection, input_func, output_func)
    if course is None:
        return
    try:
        session = start_session(connection, course["course_id"])
    except (DatabaseError, sqlite3.Error) as exc:
        output_func(f"受付開始エラー：{exc}")
        return
    output_func(
        f"受付を開始しました。{course['course_name']} "
        f"第{session['session_number']}回"
    )
    _run_attendance(
        connection,
        session,
        mode,
        serial_port,
        input_func,
        output_func,
    )


def _choose_serial_port(
    input_func: InputFunc,
    output_func: OutputFunc,
) -> str | None:
    """pySerialで列挙したCOMポートから1件選択する。"""

    try:
        ports = list_serial_ports()
    except (OSError, ValueError) as exc:
        output_func(f"COMポート一覧の取得に失敗しました：{exc}")
        return None

    if not ports:
        output_func("利用可能なCOMポートがありません。")
        return None

    while True:
        output_func("COMポート一覧")
        for index, port in enumerate(ports, start=1):
            description = port["description"] or "説明なし"
            output_func(f"{index}. {port['device']} - {description}")
        choice = _read_choice(
            "ポート番号（0で戻る）：",
            input_func,
            output_func,
        )
        if choice is None:
            continue
        if choice == 0:
            return None
        if 1 <= choice <= len(ports):
            selected = ports[choice - 1]["device"]
            output_func(f"COMポートを選択しました：{selected}")
            return selected
        output_func("存在しないポート番号です。")


def _settings_menu(
    mode: str,
    serial_port: str | None,
    input_func: InputFunc,
    output_func: OutputFunc,
) -> tuple[str, str | None]:
    while True:
        output_func("\n--- RFID接続・入力設定 ---")
        output_func(f"現在の入力モード：{mode}")
        output_func(f"現在のCOMポート：{serial_port or '未選択'}")
        output_func("1. MOCKモード")
        output_func("2. SERIALモード")
        output_func("3. COMポート選択")
        output_func("0. 戻る")
        choice = _read_choice("選択：", input_func, output_func)
        if choice == 0:
            return mode, serial_port
        if choice == 1:
            output_func("MOCKモードに設定しました。")
            return INPUT_MODE_MOCK, serial_port
        if choice == 2:
            if serial_port is None:
                serial_port = _choose_serial_port(input_func, output_func)
            if serial_port is not None:
                output_func(f"SERIALモードに設定しました（{serial_port}）。")
                return INPUT_MODE_SERIAL, serial_port
            output_func("COMポートが選択されていないため、モードを変更しません。")
        elif choice == 3:
            selected_port = _choose_serial_port(input_func, output_func)
            if selected_port is not None:
                serial_port = selected_port
                output_func(f"COMポートを設定しました：{serial_port}")
        elif choice is not None:
            output_func("選択肢が正しくありません。")


def _history_menu(
    connection: sqlite3.Connection,
    input_func: InputFunc,
    output_func: OutputFunc,
) -> None:
    course = _select_course(connection, input_func, output_func)
    if course is None:
        return

    try:
        sessions = list_sessions(connection, course["course_id"])
    except (DatabaseError, sqlite3.Error) as exc:
        output_func(f"授業回取得エラー：{exc}")
        return
    if not sessions:
        output_func("この授業には授業回がありません。")
        return

    output_func(f"--- {course['course_name']} 授業回一覧 ---")
    for session in sessions:
        closed = _format_jst(session["closed_at"])
        output_func(
            f"  ID={session['session_id']} 第{session['session_number']}回 "
            f"{session['status']} "
            f"開始={_format_jst(session['started_at'])} 終了={closed}"
        )
    while True:
        session_id = _read_choice(
            "確認する授業回ID（0で戻る）：",
            input_func,
            output_func,
        )
        if session_id is None:
            continue
        if session_id == 0:
            return
        selected = next(
            (session for session in sessions if session["session_id"] == session_id),
            None,
        )
        if selected is None:
            output_func("存在しない授業回IDです。")
            continue
        break

    try:
        report = get_attendance_report(connection, session_id)
        summary = get_attendance_summary(connection, session_id)
    except (DatabaseError, sqlite3.Error) as exc:
        output_func(f"出欠取得エラー：{exc}")
        return

    output_func(
        f"\n--- {course['course_name']} 第{selected['session_number']}回 ---"
    )
    output_func(f"受付開始：{_format_jst(selected['started_at'])}")
    output_func(f"受付終了：{_format_jst(selected['closed_at'])}")
    output_func(f"履修者：{summary['enrolled_count']}名")
    output_func(f"出席：{summary['present_count']}名")
    output_func(f"未受付：{summary['pending_count']}名")
    output_func(f"欠席：{summary['absent_count']}名")
    output_func("ID  名前  状態  時刻")
    for row in report:
        output_func(
            f"{row['student_id']}  {row['name']}  "
            f"{STATUS_LABELS.get(row['attendance_status'], row['attendance_status'])}  "
            f"{_format_jst(row['scanned_at'])}"
        )


def _exit_application(
    connection: sqlite3.Connection,
    input_func: InputFunc,
    output_func: OutputFunc,
) -> bool:
    """終了確認を行い、終了してよければTrueを返す。"""

    session = get_open_session(connection)
    if session is None:
        return True

    output_func("OPENセッションが残っています。")
    output_func(
        f"対象：{_course_name(connection, session['course_id'])} "
        f"第{session['session_number']}回"
    )
    output_func("1. 受付を終了してアプリ終了")
    output_func("2. OPENのままアプリ終了")
    output_func("3. 終了を取り消す")
    choice = _read_choice("選択：", input_func, output_func)
    if choice == 1:
        return _close_current_session(connection, input_func, output_func)
    if choice == 2:
        output_func("受付をOPENのままアプリを終了します。")
        return True
    if choice == 3:
        output_func("終了を取り消しました。")
        return False
    if choice is not None:
        output_func("選択肢が正しくありません。")
    return False


def run_app(
    connection: sqlite3.Connection,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> None:
    """接続済みDBでCLIを実行する。"""

    mode = INPUT_MODE_MOCK
    serial_port: str | None = None

    # 起動時に必ずOPENセッションを確認し、必要なら復旧メニューを出す。
    open_session = get_open_session(connection)
    if open_session is not None:
        output_func("前回終了時にOPENセッションが残っています。")
        output_func("1. 受付を再開")
        output_func("2. 受付を終了")
        output_func("3. メニューへ戻る")
        choice = _read_choice("選択：", input_func, output_func)
        if choice == 1:
            _run_attendance(
                connection,
                open_session,
                mode,
                serial_port,
                input_func,
                output_func,
            )
        elif choice == 2:
            _close_current_session(connection, input_func, output_func)

    while True:
        output_func("\n================================")
        output_func(" RFID出欠管理システム")
        output_func("================================")
        try:
            _print_current_session(connection, output_func)
        except sqlite3.Error as exc:
            output_func(f"DBエラー：{exc}")
        output_func(f"入力モード：{mode}")
        if mode == INPUT_MODE_SERIAL:
            output_func(f"COMポート：{serial_port or '未選択'}")
        output_func("1. 学生管理")
        output_func("2. 授業管理")
        output_func("3. 履修管理")
        output_func("4. 受付開始・受付再開")
        output_func("5. 受付終了")
        output_func("6. 出欠履歴確認")
        output_func("7. RFID接続・入力設定")
        output_func("8. アプリ終了")

        choice = _read_choice("選択：", input_func, output_func)
        try:
            if choice == 1:
                _student_menu(
                    connection,
                    input_func,
                    output_func,
                    mode,
                    serial_port,
                )
            elif choice == 2:
                _course_menu(connection, input_func, output_func)
            elif choice == 3:
                _enrollment_menu(connection, input_func, output_func)
            elif choice == 4:
                _start_or_resume_attendance(
                    connection,
                    input_func,
                    output_func,
                    mode,
                    serial_port,
                )
            elif choice == 5:
                _close_current_session(connection, input_func, output_func)
            elif choice == 6:
                _history_menu(connection, input_func, output_func)
            elif choice == 7:
                mode, serial_port = _settings_menu(
                    mode,
                    serial_port,
                    input_func,
                    output_func,
                )
            elif choice == 8:
                if _exit_application(connection, input_func, output_func):
                    output_func("アプリを終了します。")
                    return
            elif choice is not None:
                output_func("選択肢が正しくありません。")
        except (DatabaseError, sqlite3.Error) as exc:
            output_func(f"処理エラー：{exc}")


def main() -> int:
    """プロジェクト内のattendance.dbでCLIを起動する。"""

    connection: sqlite3.Connection | None = None
    try:
        connection = open_database(DB_PATH)
        run_app(connection)
    except (DatabaseError, sqlite3.Error) as exc:
        print(f"起動エラー：{exc}")
        return 1
    except (EOFError, KeyboardInterrupt):
        print("入力を終了しました。")
        return 0
    finally:
        if connection is not None:
            connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
