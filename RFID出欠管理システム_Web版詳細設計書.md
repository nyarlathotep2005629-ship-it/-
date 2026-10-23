# RFID出欠管理システム Web版 詳細設計書

| 項目 | 内容 |
|---|---|
| 文書種別 | 詳細設計書（新規） |
| 対象 | 既存 CUI と SQLite を維持する Windows ローカル Web 版 |
| 作成日 | 2026-10-21 |
| 前提文書 | `RFID出欠管理システム_Web版基本設計書.md` |
| 実装状態 | 設計のみ。Web ソース、DB、Arduino、CUI は未変更 |

## 1. 設計上の不変条件

1. CUI、Web は同じ `attendance.db` を使うが、**同時利用は非対応・運用禁止**とする。共通 Windows ファイルロックは実装しない。
2. ブラウザは FastAPI だけに接続し、COM と SQLite に直接接続しない。
3. DB の業務更新は既存 `pc.database` の公開関数を用いる。スキーマ、既存データ、Arduino プロトコルは変更しない。
4. OPEN 中の未受付は `PENDING`、CLOSED 後の未受付は `ABSENT` であり、欠席行は保存しない。
5. RFID 学生登録と出席受付は Web プロセス内で排他し、COM は最大 1 接続とする。
6. 通知は約 1 秒の HTTP polling のみとし、SSE/WebSocket は使わない。
7. セッション close は runtime が IDLE の場合だけ許可する。停止と close は別操作である。
8. 受付開始、受付停止、セッション close は同じ `operation_lock` で直列化する。状態参照・イベント取得はこのロックを長時間保持しない。

## 2. 実装ディレクトリと責務

```text
web/
├─ backend/
│  ├─ requirements-web.txt          # 実装時に承認済み版を固定
│  ├─ app/
│  │  ├─ main.py                    # FastAPI生成、router登録、127.0.0.1用設定
│  │  ├─ config.py                  # DBパス、poll/event/serial定数
│  │  ├─ dependencies.py            # 短命DB接続、controller取得
│  │  ├─ errors.py                  # Domain/API例外とJSON変換
│  │  ├─ schemas.py                 # Pydantic request/response
│  │  ├─ routers/
│  │  │  ├─ health.py
│  │  │  ├─ students.py
│  │  │  ├─ courses.py
│  │  │  ├─ sessions.py
│  │  │  └─ rfid.py
│  │  └─ services/
│  │     ├─ database_service.py     # pc.database呼出しと例外変換のみ
│  │     └─ rfid_runtime.py         # worker、排他、event buffer、停止
│  └─ tests/
│     ├─ conftest.py                # 一時DB、FakeSerial、依存性差替え
│     ├─ test_api_*.py
│     └─ test_rfid_runtime.py
└─ frontend/
   ├─ package.json                  # 実装時に承認済み版を固定
   └─ src/
      ├─ api/client.ts
      ├─ api/types.ts
      ├─ hooks/useRuntimePolling.ts
      ├─ pages/                     # 7画面
      ├─ components/                # 結果、確認、エラー、状態表示
      └─ App.tsx
```

`config.py` はプロジェクト root の `attendance.db` を既定値にし、テスト時だけ明示された一時 DB パスを依存性注入で渡す。存在しない DB を黙って別場所に作らない。具体的な FastAPI、React、Vite、Pydantic、pySerial の版番号は、実装開始時の Python/Node 環境と脆弱性情報を確認して固定する（現時点では要確認）。

## 3. バックエンド共通設計

### 3.1 DB 接続

| 用途 | 接続 | トランザクション/ロック |
|---|---|---|
| 通常 API | 要求ごとに `pc.database.open_database(db_path)`、finally で close | 既存読み取り、既存更新関数の `BEGIN IMMEDIATE` |
| RFID worker | worker 開始時に1本、終了 finally で close | 出席/学生登録の既存更新関数を使用 |
| Web wrapper | `PRAGMA foreign_keys=ON` は既存接続に従う。必要なら接続単位で `busy_timeout=3000` | スキーマ変更なし、無制限 retry なし |

SQLite 接続をスレッド間で共有しない。`check_same_thread=False` は用いない。`pc.database` が既に検査する `ValidationError`、`DuplicateError`、`NotFoundError`、`EnrollmentLockedError`、`ActiveSessionError`、`InvalidStateError` を API 例外へ写像する。

### 3.2 HTTP 共通形式

成功は資源 JSON をそのまま返す。業務失敗以外は次を返す。

```json
{
  "code": "RFID_BUSY",
  "message": "RFID は出席受付で使用中です。停止後に再試行してください。",
  "request_id": "uuid",
  "retryable": false
}
```

| code | HTTP | 発生条件 |
|---|---:|---|
| VALIDATION_ERROR | 422 | Pydantic、名前、UID、ID の不正 |
| NOT_FOUND | 404 | 学生、授業、session がない |
| DUPLICATE | 409 | UID、履修などの重複 |
| ACTIVE_SESSION | 409 | 別の OPEN が存在 |
| ENROLLMENT_LOCKED | 409 | 授業回開始済みの履修変更 |
| SESSION_NOT_OPEN | 409 | OPEN でない session に対する開始/close |
| RFID_BUSY | 409 | 別用途が RFID runtime を予約済み |
| RFID_RUNTIME_ACTIVE | 409 | runtime 中に close を要求 |
| RUNTIME_STOP_TIMEOUT | 503 | stop 待機内に worker が IDLE にならない |
| SERIAL_OPEN_FAILED | 503 | COM 不在、他プロセス占有、open 失敗 |
| RFID_NOT_READY | 503 | 5秒以内に READY を受信できない |
| SERIAL_READ_FAILED | 503 | 読取時の例外・切断 |
| DATABASE_BUSY | 503 | busy timeout 後も SQLite を取得できない |
| STORAGE_ERROR | 500 | 既存関数が返す保存失敗、想定外 DB 失敗 |

SQL 文、スタックトレース、UID 全文、個人情報はクライアント・通常ログに返さない。

## 4. Pydantic モデル

| モデル | 主なフィールド・検証 |
|---|---|
| `StudentCreateRequest` | `name: str`（strip、1文字以上）、`card_uid: str`（既存 `normalize_uid` で検証） |
| `RfidRegistrationStartRequest` | `name: str`、`port: str`、`mode: Literal['SERIAL']` |
| `CourseCreateRequest` | `course_name: str`（strip、1文字以上） |
| `EnrollmentCreateRequest` | `student_id: int > 0` |
| `SessionCreateRequest` | `course_id: int > 0` |
| `RuntimeStartRequest` | `session_id: int > 0`、`mode: Literal['MOCK','SERIAL']`、SERIAL のみ `port` 必須 |
| `MockScanRequest` | `card_uid: str`。runtime が MOCK/ATTENDING の場合だけ許可 |
| `RuntimeStopResponse` | `state`、`stopped`、`operation_id`、`last_error` |
| `RuntimeStatusResponse` | `server_instance_id`、`state`、`operation`、`operation_id`、`session_id`、`mode`、`port`、`updated_at`、`last_error`、`last_completed_operation_id`、`last_terminal_event_kind` |
| `EventBatchResponse` | `server_instance_id`、`first_sequence`、`last_sequence`、`next_after_sequence`、`reset_required`、`events` |
| `AttendanceResult` | `status`、`student_id?`、`student_name?`、`scanned_at?`。UID は通常イベントに含めない |

Pydantic の型検査だけでは UID 仕様を二重実装しない。最終判定は `pc.database.normalize_uid()` とし、失敗は `ValidationError` から 422 へ変換する。

## 5. API 詳細

### 5.1 ヘルス・RFID 状態

| Method / Path | Request | Response | 処理 / DB / lock | status |
|---|---|---|---|---|
| GET `/health` | なし | `ok`, `db_path_configured`, `server_instance_id` | 接続確認は読み取りのみ。COMを開かない | 200 / 503 |
| GET `/rfid/ports` | なし | `[{device,description,hwid}]` | `pc.reader.list_serial_ports()`。lock不要 | 200 |
| GET `/rfid/runtime` | なし | `RuntimeStatusResponse` | controller の lock 下でスナップショットコピー | 200 |
| GET `/rfid/events?server_instance_id=<id>&after_sequence=0&limit=100` | 初回以外は `server_instance_id` 必須。sequence>=0、limit 1..100 | `EventBatchResponse` | state/event buffer lock 下でリングバッファ読取。ID不一致または保持切れは `reset_required`。DB不要 | 200 / 422 |
| POST `/rfid/runtime/stop` | なし | `RuntimeStopResponse` | `operation_lock`、stop_event、worker join（3秒上限）。完了 event の operation_id を返す | 200 / 409 / 503 |

`/health` は実DBの内容を変更しない。`/rfid/ports` の列挙結果はポートが開ける保証ではない。

### 5.2 学生・授業・履修

| Method / Path | Request | Response | 既存関数 / transaction | status |
|---|---|---|---|---|
| GET `/students` | なし | `[{student_id,name,card_uid}]` | `list_students` / 読取 | 200 |
| POST `/students` | `StudentCreateRequest` | 作成学生 | `create_student` / `BEGIN IMMEDIATE` | 201 / 409 / 422 / 503 |
| POST `/rfid/registration/start` | `RfidRegistrationStartRequest` | runtime 状態、`operation_id` | `operation_lock` で runtime を REGISTERING に予約。worker が UID 後 `create_student` | 202 / 409 / 422 / 503 |
| GET `/courses` | なし | course 一覧 | `list_courses` / 読取 | 200 |
| POST `/courses` | `CourseCreateRequest` | 作成 course | `create_course` / `BEGIN IMMEDIATE` | 201 / 409 / 422 |
| GET `/courses/{course_id}/enrollments` | path | 履修者一覧 | `list_enrollments` / 読取 | 200 / 404 |
| POST `/courses/{course_id}/enrollments` | `EnrollmentCreateRequest` | 作成結果 | `enroll_student` / `BEGIN IMMEDIATE` | 201 / 404 / 409 |
| DELETE `/courses/{course_id}/enrollments/{student_id}` | path | `{removed:true}` | `unenroll_student` / `BEGIN IMMEDIATE` | 200 / 404 / 409 |

RFID 学生登録は POST 中に COM 読取を待たず 202 を返す。画面は runtime/events を poll し、`REGISTRATION_SUCCEEDED`、`REGISTRATION_FAILED`、`RUNTIME_ERROR` を受ける。登録・出席・停止のすべてが同一 controller の lock を経由するため、登録待機中に出席開始できない。

### 5.3 セッション・出欠

| Method / Path | Request | Response | 既存関数 / transaction | status |
|---|---|---|---|---|
| GET `/sessions/open` | なし | session または `null` | `get_open_session` / 読取 | 200 |
| POST `/sessions` | `SessionCreateRequest` | OPEN session | `create_session` / `BEGIN IMMEDIATE` | 201 / 404 / 409 |
| POST `/sessions/{session_id}/close` | なし | CLOSED session | 先に runtime lock で IDLE 確認、次に `close_session` / `BEGIN IMMEDIATE` | 200 / 404 / 409 / 503 |
| GET `/courses/{course_id}/sessions` | path | 回一覧 | `list_sessions` / 読取 | 200 / 404 |
| GET `/sessions/{session_id}/attendance` | path | 学生別出欠一覧 | `get_attendance_report` / 読取 | 200 / 404 |
| GET `/sessions/{session_id}/summary` | path | 件数 | `get_attendance_summary` / 読取 | 200 / 404 |
| POST `/attendance/runtime/start` | `RuntimeStartRequest` | runtime 状態、`operation_id` | `operation_lock` 下で OPEN/IDLE を再検査し worker 起動 | 202 / 409 / 422 / 503 |
| POST `/attendance/runtime/scan` | `MockScanRequest` | `AttendanceResult` | MOCK/ATTENDING を lock 下で検査後 `register_attendance` | 200 / 409 / 422 / 500 |

`close` は `operation_lock` を取得してから worker の状態と対象 session を確認し、そのロックを保持したまま DB の `get_open_session` と `close_session` を実行する。IDLE 以外なら `close_session` を呼ばない。受付開始も同じロックを取得するため、close の確認直後に別の受付開始が割り込むことはない。状態参照とイベント取得は別の state/event lock だけを短時間取得し、close や worker join の完了を待たせない。DB 側でも対象 session が OPEN であることを再検査するため、「stop成功後だけclose」が API 操作用ロックと DB の二段で保持される。

## 6. RFID runtime 詳細

### 6.1 `RfidRuntimeController`

| 属性 | 内容 |
|---|---|
| 同期 | `threading.RLock` を controller の状態・イベントバッファ操作にだけ使用 |
| 操作用ロック | `operation_lock`。受付 start、runtime stop、session close、RFID登録 start を直列化。reader待機や通常pollでは保持しない |
| worker | `threading.Thread` 1本。`SerialReader` と worker DB 接続の所有者 |
| 停止 | `threading.Event`。`read_event(timeout=0.1)` の周回ごとに確認 |
| event buffer | `collections.deque(maxlen=200)`、server 内単調 sequence |
| server instance | 起動時 `uuid4`。プロセス再起動で更新 |
| 資源解放 | worker の `finally` で reader/DB を閉じ、完了 event を発行してから `last_completed_operation_id` を更新し、IDLE event を発行 |

既存 `SerialReader` には cancellation 引数や `stop()` はない。新 runtime は既存 API を変更せず、短い `read_event` timeout と `threading.Event` で停止可能にする。reader の open/read/close は同じ worker だけが行う。

### 6.2 SERIAL 出席 worker

```text
POST start
  operation_lock: IDLE と OPEN(session_id) を確認 → STARTING / operation_id / event
  worker 起動 → 202
worker
  open_database(同一DB) を開く
  SerialReader(port, 115200, 0.1, 5.0).open()  # HELLO/READY
  lock: ATTENDING / event
  stop_event が立つまで read_event(0.1)
    UID: register_attendance(session_id, uid) → ATTENDANCE_RESULT event
    WARNING: READER_WARNING event
    ERROR/例外: ERROR / RUNTIME_ERROR event → finally
finally
  reader.close(), DB close()
  terminal event(operation_id)を発行
  last_completed_operation_idを更新
  state lock: IDLE または ERROR→IDLE / event
```

`READY` 前の UID と `DIAG:` は既存 `reader.py` の解釈に従い、受付に登録しない。`ERR:`、接続切断、open 失敗では session を閉じず OPEN のままにする。登録 worker も同じ構造で、最初の有効 UID の `create_student(name, uid)` 後に IDLE へ戻る。

### 6.3 MOCK 出席

MOCK start は COM を開かないが、runtime を `ATTENDING` として予約する。その間、RFID 登録と SERIAL 開始を拒否する。`/scan` の UID は既存 `register_attendance` に渡し、以下を event と API 応答へ記録する。

| DB 結果 | HTTP | UI 表示 |
|---|---:|---|
| SUCCESS | 200 | 出席登録成功 |
| ALREADY_REGISTERED | 200 | 登録済み（最初の時刻を維持） |
| UNKNOWN_CARD | 200 | 未登録カード |
| NOT_ENROLLED | 200 | 履修対象外 |
| NO_ACTIVE_SESSION | 409 | OPEN セッションなし。停止して再同期 |
| INVALID_UID | 422 | UID形式不正 |
| STORAGE_ERROR | 500 | 保存失敗。再試行前に状態確認 |

### 6.4 停止と終了の擬似コード

```text
stop():
  operation_lock:
    IDLEなら {stopped:true} を返す
    STARTING/REGISTERING/ATTENDING → STOPPING; stop_event.set()
    join(3秒)
    workerが終了したら terminal event を確認できる状態で IDLE を返す
    終了しなければ STOPPING のまま 503 RUNTIME_STOP_TIMEOUT

close(session_id):
  operation_lock:
    runtime が IDLE 以外なら 409 RFID_RUNTIME_ACTIVE
    DB: get_open_session と対象IDを確認
    DB: close_session(session_id)
    成功時だけ CLOSED を返す
```

受付 start、stop、close は上記 `operation_lock` により同時に実行されない。stop が timeout なら UI は close を送信しない。ネットワーク再試行で stop/close が重複しても、UI のボタン無効化に加え、controller と DB の再検査で不正順序を防ぐ。

## 7. イベントとポーリング詳細

### 7.1 Event レコード

```json
{
  "sequence": 41,
  "occurred_at": "2026-10-21T12:00:00Z",
  "kind": "ATTENDANCE_RESULT",
  "operation_id": "uuid",
  "payload": {"status":"SUCCESS", "student_id":12, "student_name":"山田太郎"}
}
```

`kind` は `RUNTIME_STATE_CHANGED`、`ATTENDANCE_RESULT`、`REGISTRATION_SUCCEEDED`、`REGISTRATION_FAILED`、`READER_WARNING`、`RUNTIME_ERROR`、`STOP_COMPLETED` とする。通常の event payload に UID 全文を含めない。

### 7.2 Cursor 契約

| 条件 | API 応答 | クライアント動作 |
|---|---|---|
| after が最新 | 空 events、同じ next cursor | 状態のみ更新 |
| 新規がある | sequence 昇順の events | 順に reducer へ適用し cursor 更新 |
| after が保持開始より古い | `reset_required:true` | cursor破棄、runtime/OPEN/一覧/集計を全取得 |
| request の `server_instance_id` が異なる | `reset_required:true` | 同上。前サーバーの cursor を使用しない |
| browser 更新 | cursor 未保持で after=0 | 初期全取得後に追随 |
| server 再起動 | runtime は IDLE、DBのOPENは存続 | OPEN を復旧表示。自動 reader 再開しない |

### 7.3 React `useRuntimePolling`

- 起動条件: 出席受付画面が mount され、runtime が非 IDLE、または停止/開始要求の完了待ち。学生管理画面では RFID 登録の `operation_id` が存在し、完了未確認の間も起動する。
- 間隔: 1000 ms。API 応答待ち中は次の timer を予約しない。
- `AbortController` で前回 request を中断し、`requestSerial` が最新の応答だけ state へ反映する。
- 初回以外の event request には保存済み `server_instance_id` を付ける。応答の ID と比較し、不一致または `reset_required` なら全状態を再取得する。
- event は `sequence` が cursor より大きいものだけ採用する。重複 event は無視する。
- reset 時は `polling=false` にして全再取得を1回完了してからカーソルを再設定する。途中応答は requestSerial で破棄する。
- `operation_id` がある間は IDLE を検出しても直ちに timer を解除しない。対応する `REGISTRATION_SUCCEEDED`、`REGISTRATION_FAILED`、`RUNTIME_ERROR`、または stop の完了 event を確認してから停止する。
- IDLE なのに完了 event が取得できない場合は、学生管理画面なら学生一覧と runtime 状態を全再取得し、同じ `server_instance_id` で event を一度再取得する。runtime の `last_completed_operation_id` が一致すれば完了を復旧確認し、結果を表示して停止する。一致しなければ「結果不明」として再試行/再確認を促し、無音で成功扱いにしない。
- 完了確認済み、CLOSED、他ページへの遷移、component unmount で timer と fetch を必ず解除する。

これにより遅延応答、重複 poll、逆順到着、保持切れ、サーバー再起動を画面の事実にしない。

## 8. React 画面詳細

| ID / 画面 | 表示 | 操作・制御 |
|---|---|---|
| SCR-01 ダッシュボード | OPEN session、runtime、集計、同時利用禁止 | OPENなら「受付へ」。周期 polling なし |
| SCR-02 出席受付 | session、PRESENT/PENDING、直近結果、runtime、開始/停止/終了 | runtime 中だけ約1秒同期。終了は確認→stop完了→close。二重操作無効 |
| SCR-03 学生管理 | 一覧、手入力フォーム、RFID登録フォーム、登録中/完了/失敗表示 | RFID登録の `operation_id` が完了 event または復旧確認されるまで約1秒同期。登録中は出席操作不可 |
| SCR-04 授業管理 | 授業一覧・追加 | 空白/重複をインライン表示 |
| SCR-05 履修管理 | 授業選択、学生一覧、登録/解除 | lock時は操作無効と理由表示 |
| SCR-06 出欠履歴 | course/session選択、一覧、集計 | CLOSED は ABSENT、OPEN は PENDING と明記 |
| SCR-07 RFID設定 | MOCK/SERIAL、COM一覧、runtime | 「CUIと同時起動・同時利用禁止」「他プロセス占有時は停止して再試行」を固定表示 |

共通コンポーネントは `RuntimeBadge`、`AttendanceResultPanel`、`ApiErrorNotice`、`ConfirmCloseDialog`、`BusyOverlay` とする。色に加えてラベルとアイコンを併用し、成功/警告/失敗を判別可能にする。

## 9. 主要シーケンス

### 9.1 SERIAL 受付開始・読取

```text
UI → POST attendance/runtime/start → FastAPI: 202 STARTING
UI → 約1秒 poll runtime/events
worker → SerialReader.open → HELLO/READY
worker → event ATTENDING
Arduino → UID → worker → register_attendance → SQLite commit
worker → event ATTENDANCE_RESULT(sequence n)
UI → events(after n-1) → 結果表示、一覧/集計を再取得
```

### 9.2 終了

```text
UI確認 → POST rfid/runtime/stop → IDLE確認
    ├─ timeout/error: OPENのまま停止再試行、closeしない
    └─ success → POST sessions/{id}/close → CLOSED再読込
                       └─ error: OPENのまま、停止済み、close再試行
```

### 9.3 CUI が誤って起動している場合

Web はプロセス間の起動を検出保証しない。COM open で失敗すれば `SERIAL_OPEN_FAILED` を表示し、利用者は Web/CUI のどちらかを終了してから再試行する。DB busy なら `DATABASE_BUSY` とし、両方を同時に使い続けない。これを「安全に同時使用できる」証拠とは扱わない。

## 10. DB、移行、互換性

| 観点 | 設計 |
|---|---|
| DBファイル | `attendance.db` を CUI/Web 共通の正本とする |
| スキーマ | 変更なし。migration/seed/DDL追加なし |
| 初回導入 | CUI停止、バックアップ、スキーマ/トリガー/索引/既存UIDを読み取り確認 |
| 旧DB | `CREATE TABLE IF NOT EXISTS` が既存 CHECK を足さない点を確認。自動修正しない |
| 後方互換 | CUIの `pc/main.py`、既存DB操作、Arduino通信を保持 |
| ロールバック | Web停止後にCUIへ戻る。DB変更を戻す目的の無確認コピー上書きは禁止 |

Web 専用 runtime/event は永続化しない。OPEN session と attendance だけが既存 DB に永続化されるため、ブラウザ/サーバー再起動後も DB 上の業務状態を読み直せる。

## 11. エラー、運用、セキュリティ

| 場面 | UI/運用動作 |
|---|---|
| COMなし・占有 | ポート名と安全な停止/再試行案内。別ポートへ自動変更しない |
| READY timeout | reader を close、OPEN維持、再接続/配線確認を案内 |
| USB切断 | ERROR event、reader/DBを finally で解放、OPEN維持 |
| SQLite busy | 503、同時CUI/Web操作を止め、状態再取得後に再試行 |
| 予期しないサーバー終了 | 再起動後は reader 自動再開しない。OPEN検出後に利用者が開始 |
| ブラウザ終了 | readerはバックエンドで継続可能。後で受付画面を開いて再同期 |
| ログ | 時刻、operation_id、結果コード、例外分類を記録。UID/氏名の不用意な出力を避ける |

ローカル専用・認証なしのため、FastAPI は `127.0.0.1` に限定する。開発時の Vite CORS は明示した origin だけを許可し、本番相当の同一オリジンでは CORS を不要にする。`0.0.0.0` bind、LAN公開、秘密情報のログ出力は本設計の対象外であり禁止する。

## 12. テスト詳細

### 12.1 現在の基準

| 対象 | 結果 | 根拠 |
|---|---|---|
| 既存 DB テスト数 | 19件 | `tests/test_database.py` の `test_` メソッドを確認 |
| 既存 DB テスト実行 | **PASS** | 2026-10-21、隔離一時DBで `python -m unittest discover -s tests -v`、19件成功 |
| 実DB/COM/Arduino | NOT RUN | 本書作成では非破壊を優先 |

### 12.2 実装後の必須テスト

| ID | 対象 | 期待結果 |
|---|---|---|
| T-API-01 | 学生/授業/履修/セッション API | 既存関数と同じ成功・例外 HTTP 写像 |
| T-API-02 | session close 中の runtime | 409、`close_session` が呼ばれない |
| T-RT-01 | 登録中に出席開始、出席中に登録開始 | 片方だけ 202、他方は 409 RFID_BUSY |
| T-RT-02 | SERIAL start/stop | FakeSerial で HELLO/READY、UID、finally close、COM最大1本 |
| T-RT-03 | stop timeout | 503、session OPEN、closeを拒否 |
| T-RT-04 | SERIAL error | ERROR event、session OPEN、resource解放 |
| T-POLL-01 | 出席受付・RFID登録画面の polling | 登録中の `operation_id` が完了 event または復旧確認されるまで停止しない |
| T-POLL-02 | 同時/遅延/逆順応答 | 最新 requestSerial だけが表示を更新 |
| T-POLL-03 | 重複 sequence/保持切れ/server id変更 | 重複無視、reset後の全再取得 |
| T-POLL-04 | IDLE先行・完了event欠落 | 学生一覧/runtime/event再取得、`last_completed_operation_id`一致時のみ復旧完了 |
| T-LOCK-01 | start/stop/close の競合 | 同じ `operation_lock` で直列化し、close中のstartを409または順番待ちで処理 |
| T-DB-01 | 既存 `tests/test_database.py` | 一時DBで19件 PASS を維持 |
| T-UI-01 | 7画面 | 二重送信無効、エラー/復旧文、PENDING/ABSENT 表示 |
| T-HW-01 | 実COM | COM open/READY/read/stop/close を利用者が確認 |
| T-HW-02 | Arduino/RC522 | ビルド、カード読取、切断・再接続は別々に判定 |

API と DB は必ず一時 DB を使用し、実 `attendance.db` は読み取り確認以外に用いない。FakeSerial は READY、UID、ERR、無応答、open失敗を再現する。実機テストの PASS はコンパイル/APIテストの PASS から推論しない。

## 13. 実装順序と受入条件

1. backend/frontend の雛形、読み取り専用 API、既存19件回帰。
2. CRUD/session API と一時 DB API テスト。
3. MOCK runtime と cursor polling、停止/close の E2E。
4. SERIAL runtime と FakeSerial の排他/失敗テスト。
5. 実 COM・RC522、UI手動受入、README/起動案内への同時利用禁止の明記。

受入完了には、少なくとも「CUI/Web同時利用禁止の表示」「Web内の登録/受付排他」「COM最大1本」「出席受付とRFID登録画面の約1秒poll」「server_instance_idによる再起動再同期」「operation_idの完了event確認」「start/stop/closeの直列化」「stop成功後だけclose」「既存19件回帰」「実機確認」を個別の evidence で満たすことが必要である。

## 14. 要確認事項

- 実運用 `attendance.db` が現行 CHECK、UNIQUE、trigger、partial index を実際に備えるか。
- Python/Node の実装対象バージョンと、依存パッケージの固定版。
- 配布形態、固定 HTTP ポート、起動スクリプトの名称。
- 実機 COM 番号、USB 切断からの再接続・利用者手順。
- 実運用が単一端末・単一操作者であること（認証なし前提）。

これらは環境・導入の確認事項であり、既存 CUI/DB/Arduino を変更する許可ではない。

