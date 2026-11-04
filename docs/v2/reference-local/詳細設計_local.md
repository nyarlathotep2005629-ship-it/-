# RFID出欠管理システム 詳細設計書

**バージョン：** 0.1（レビュー用）  
**作成日：** 2026年10月20日  
**基準文書：** 要件定義書 v1.0／基本設計書 v0.1  
**対象：** Ver.1.0 初期実装  
**状態：** 詳細設計レビュー待ち

---

## 1. 設計方針と実装範囲

### 1.1 基本方針

本システムは、Arduino UnoによるRFIDカード読み取りと、Pythonによるローカル出欠管理を組み合わせたシステムとする。

以下を基本原則とする。

1. ArduinoはUIDの読み取りと送信を担当する。
2. Pythonは学生照合・履修判定・出席管理を担当する。
3. SQLiteは学生・授業・履修・授業回・出席記録を永続化する。
4. RFIDの入力方法と出欠判定ロジックを分離する。
5. データの一意性・参照整合性は可能な限りDB側で保証する。
6. DBに対する複数の関連操作はトランザクションで管理する。
7. GUI、Web、AWS、認証機能などは実装しない。

### 1.2 実行環境

| 項目 | 仕様 |
|---|---|
| PC OS | Windows 11を想定 |
| 言語 | Python 3 |
| DB | SQLite |
| 通信 | USBシリアル |
| 通信ライブラリ | pySerial |
| マイコン | Arduino Uno |
| ファームウェア | Arduino Framework / C++ |
| ビルド環境 | PlatformIO |
| RFID | RC522系SPIモジュール |

正確なPythonバージョンとライブラリバージョンは実装環境確認時に固定する。

---

## 2. ディレクトリ・モジュール構成

```text
RFID-Attendance/
│
├── arduino/
│   ├── platformio.ini
│   └── src/
│       └── main.cpp
│
├── pc/
│   ├── main.py
│   ├── database.py
│   ├── reader.py
│   ├── schema.sql
│   └── requirements.txt
│
├── tests/
│   ├── test_database.py
│   └── test_attendance.py
│
└── README.md
```

### 2.1 各ファイルの責務

| ファイル | 役割 |
|---|---|
| `arduino/src/main.cpp` | RC522初期化・UID読み取り・シリアル送信 |
| `pc/main.py` | CLI表示、操作入力、機能呼び出し |
| `pc/database.py` | DB接続、登録処理、出欠判定、SQL実行 |
| `pc/reader.py` | シリアル受信、模擬入力、UIDの解析 |
| `pc/schema.sql` | DBテーブル・インデックス定義 |
| `pc/requirements.txt` | Pythonの外部依存関係 |
| `tests/` | DBと出欠処理の自動テスト |
| `README.md` | セットアップと実行方法 |

初期版ではRepositoryパターン、DIコンテナ、ORM、Web APIなどは導入しない。

必要になった場合にのみファイル分割や抽象化を追加する。

---

## 3. データベース詳細設計

### 3.1 SQLite接続設定

DB接続を作成した直後に、以下を実行する。

```sql
PRAGMA foreign_keys = ON;
```

外部キーは接続ごとに有効化する。

Python側ではDBファイルを明示的に指定できる構成とする。

- 通常実行：`attendance.db`
- 自動テスト：一時DB
- 手動テスト：専用の別DBを指定可能

通常DBに対する自動初期化・一括削除機能は設けない。

テーブル初期化処理は `CREATE TABLE IF NOT EXISTS` 等を使用し、既存の出席データを削除しない。

### 3.2 SQLスキーマ

以下を初期実装のスキーマ案とする。

```sql
CREATE TABLE IF NOT EXISTS students (
    student_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL
        CHECK (length(trim(name)) > 0),
    card_uid TEXT NOT NULL UNIQUE
        CHECK (length(card_uid) IN (8, 14, 20))
);

CREATE TABLE IF NOT EXISTS courses (
    course_id INTEGER PRIMARY KEY,
    course_name TEXT NOT NULL
        CHECK (length(trim(course_name)) > 0)
);

CREATE TABLE IF NOT EXISTS enrollments (
    student_id INTEGER NOT NULL,
    course_id INTEGER NOT NULL,

    PRIMARY KEY (student_id, course_id),

    FOREIGN KEY (student_id)
        REFERENCES students(student_id),

    FOREIGN KEY (course_id)
        REFERENCES courses(course_id)
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id INTEGER PRIMARY KEY,
    course_id INTEGER NOT NULL,
    session_number INTEGER NOT NULL
        CHECK (session_number >= 1),
    started_at TEXT NOT NULL,
    closed_at TEXT,

    UNIQUE (course_id, session_number),

    CHECK (
        closed_at IS NULL
        OR closed_at >= started_at
    ),

    FOREIGN KEY (course_id)
        REFERENCES courses(course_id)
);

CREATE TABLE IF NOT EXISTS attendance (
    session_id INTEGER NOT NULL,
    student_id INTEGER NOT NULL,
    scanned_at TEXT NOT NULL,

    PRIMARY KEY (session_id, student_id),

    FOREIGN KEY (session_id)
        REFERENCES sessions(session_id),

    FOREIGN KEY (student_id)
        REFERENCES students(student_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS
    uq_one_open_session
ON sessions ((1))
WHERE closed_at IS NULL;

CREATE INDEX IF NOT EXISTS
    ix_enrollments_course
ON enrollments (course_id, student_id);
```

#### 補足事項

- `INTEGER PRIMARY KEY` を内部IDとして使用する。
- IDの自動採番に `AUTOINCREMENT` は必須としない。
- UIDは文字列として保存する。
- UIDの長さは4、7、10バイトを想定し、16進文字列で8、14、20文字となる。
- カードUIDの形式はPython側でも検証する。
- 学生名の重複は許容する。
- 授業名の重複も許容し、内部IDで区別する。
- 日時は統一されたUTC形式の文字列として保存する。

UIDの一意制約は登録重複を防ぐためのものであり、実物のカードが偽造・複製できないことを保証するものではない。

### 3.3 日時の保存形式

Python側でUTC時刻を生成する。

```python
datetime.now(timezone.utc).isoformat(
    timespec="milliseconds"
)
```

保存例：

```text
2026-10-20T00:15:30.123+00:00
```

すべての日時カラムで同一形式を使用する。

CLIで表示する際は、Asia/Tokyoの日本時間に変換する。

時刻の取得にはPC側のシステム時計を使用する。

### 3.4 授業回数の採番

授業回数は授業ごとに独立して採番する。

```sql
SELECT COALESCE(MAX(session_number), 0) + 1
FROM sessions
WHERE course_id = ?;
```

この問い合わせと `sessions` へのINSERTは同一トランザクション内で実行する。

処理開始時に書き込み用トランザクションを確保し、採番途中で他の書き込み処理が割り込まないようにする。

データベースでは `(course_id, session_number)` の一意制約も設定する。

### 3.5 出欠一覧取得SQL

授業回IDを指定して、履修者と出席記録を結合する。

```sql
SELECT
    s.student_id,
    s.name,
    a.scanned_at,

    CASE
        WHEN a.student_id IS NOT NULL
            THEN 'PRESENT'

        WHEN se.closed_at IS NULL
            THEN 'PENDING'

        ELSE 'ABSENT'
    END AS attendance_status

FROM sessions AS se

JOIN enrollments AS e
    ON e.course_id = se.course_id

JOIN students AS s
    ON s.student_id = e.student_id

LEFT JOIN attendance AS a
    ON a.session_id = se.session_id
    AND a.student_id = s.student_id

WHERE se.session_id = ?

ORDER BY s.student_id;
```

判定値は次のとおり。

| 値 | 表示 |
|---|---|
| PRESENT | 出席 |
| PENDING | 未受付 |
| ABSENT | 欠席 |

欠席を示す行はattendanceに挿入しない。

### 3.6 DBトランザクション方針

以下の処理を、それぞれ単一トランザクションとして実行する。

- 学生登録
- 授業登録
- 履修登録・解除
- 受付開始
- 受付終了
- 出席登録

出席登録や受付開始など、事前確認と更新を組み合わせる処理は `BEGIN IMMEDIATE` を使用する方針とする。

処理が成功したらCOMMIT、失敗時にはROLLBACKを行う。

接続は1つのPCアプリ内で管理する。

---

## 4. Pythonモジュール詳細設計

### 4.1 main.py

CLIの表示とユーザー操作を担当する。

主な処理は以下のとおり。

| 関数案 | 役割 |
|---|---|
| `main()` | 起動処理・メインループ |
| `show_main_menu()` | メニュー表示 |
| `student_menu()` | 学生管理 |
| `course_menu()` | 授業管理 |
| `enrollment_menu()` | 履修管理 |
| `start_attendance_ui()` | 受付開始 |
| `run_attendance_ui()` | RFID読み取り・結果表示 |
| `close_attendance_ui()` | 受付終了 |
| `show_attendance_report()` | 出欠一覧 |
| `recover_session_ui()` | 起動時の受付復旧 |

関数名は実装時に調整可能とする。

ただし、DBへの直接SQL発行をCLI処理内に分散させない。

### 4.2 database.py

DBの操作と出欠判定を担当する。

主要な関数仕様は以下とする。

| 関数案 | 引数 | 戻り値 |
|---|---|---|
| `create_student` | 名前、UID | 学生ID |
| `list_students` | なし | 学生一覧 |
| `create_course` | 授業名 | 授業ID |
| `list_courses` | なし | 授業一覧 |
| `enroll_student` | 学生ID、授業ID | 処理結果 |
| `unenroll_student` | 学生ID、授業ID | 処理結果 |
| `list_enrollments` | 授業ID | 履修者一覧 |
| `start_session` | 授業ID | 授業回情報 |
| `get_open_session` | なし | 受付中情報／なし |
| `close_session` | 授業回ID | 処理結果 |
| `register_attendance` | 授業回ID、UID | 出席登録結果 |
| `get_attendance_report` | 授業回ID | 出欠一覧 |

具体的な型定義、例外クラス、戻り値の構造はCodex実装前のインターフェース定義で統一する。

### 4.3 reader.py

UID入力の処理を担当する。

**SerialReader**

- COMポートを開く。
- Arduinoの準備完了を待つ。
- シリアルメッセージを読み取る。
- UID形式を検証する。
- 不正メッセージを拒否する。
- 通信エラーを通知する。
- 読み取り終了時にポートを閉じる。

**MockReader**

- CLIからUIDを入力する。
- 実機と同じUID検証処理に渡す。
- 読み取り終了コマンドを受け付ける。

両入力方式のUIDは、最終的に共通の出席登録関数へ渡す。

学生情報の検索やSQLiteへの保存をreader.py内で実施してはならない。

### 4.4 UID正規化関数

関数案：

```python
normalize_uid(raw_uid: str) -> str
```

以下の動作を要求する。

1. 先頭・末尾の空白を除去する。
2. 英小文字を英大文字へ変換する。
3. 16進文字のみで構成されていることを確認する。
4. 長さが8、14、20文字のいずれかであることを確認する。
5. 正しい場合は正規化UIDを返す。
6. 不正な場合は入力エラーとする。

例：

| 入力 | 結果 |
|---|---|
| `04a1b2c3` | `04A1B2C3` |
| `04A1B2C3` | `04A1B2C3` |
| `04A1ZZ99` | エラー |
| `123` | エラー |
| 空文字列 | エラー |

先頭ゼロを削除しない。

---

## 5. 出席登録処理の詳細

### 5.1 入力

- session_id
- card_uid

### 5.2 処理順序

1. UIDの形式を検証する。
2. DBトランザクションを開始する。
3. 指定された授業回が受付中か確認する。
4. UIDから学生を検索する。
5. 学生が対象授業の履修者か確認する。
6. 同じ授業回で既に登録済みか確認する。
7. 現在日時を取得する。
8. attendanceへINSERTする。
9. COMMITする。
10. 処理結果を返す。

検索結果がない場合や条件を満たさない場合は、出席を保存せず処理を終了する。

### 5.3 処理結果

| コード | 条件 |
|---|---|
| SUCCESS | 新規出席登録成功 |
| ALREADY_REGISTERED | 当該授業回で出席済み |
| UNKNOWN_CARD | UIDが学生マスタにない |
| NOT_ENROLLED | 対象授業を履修していない |
| NO_ACTIVE_SESSION | 指定セッションが受付中ではない |
| INVALID_UID | UID形式が不正 |
| STORAGE_ERROR | DB処理に失敗 |

DBエラー時には、利用可能なエラー詳細を内部の例外情報として保持する。

CLIにはユーザーが理解できる日本語メッセージを表示する。

### 5.4 再スキャン時の扱い

DBへの書き込みが成功した後、PCアプリが結果表示前に異常終了する可能性がある。

再起動後に同じカードをかざした場合、既存の出席記録を検出して `ALREADY_REGISTERED` を返す。

新しい行を追加したり、最初の出席時刻を上書きしたりしない。

---

## 6. CLI詳細設計

### 6.1 メインメニュー

```text
================================
 RFID 出欠管理システム
================================

現在の受付：なし
入力モード：MOCK

1. 学生管理
2. 授業管理
3. 履修管理
4. 受付開始・受付再開
5. 受付終了
6. 出欠履歴確認
7. RFID接続・入力設定
0. アプリ終了

選択 >
```

受付中の場合は、現在の授業名と第何回かを表示する。

### 6.2 学生管理

```text
--- 学生管理 ---

1. 学生登録
2. 学生一覧
0. 戻る
```

学生登録では名前入力後に、カードのUIDを読み取る。

MOCKモードの場合はキーボード入力とする。

登録内容の確認後、DBへINSERTする。

### 6.3 授業管理

授業の新規作成と一覧表示を可能にする。

登録時に内部IDを自動生成する。

同名授業が存在する場合は注意を表示するが、別の授業IDでの作成は許可する。

### 6.4 履修管理

対象授業を選択してから学生を追加・解除する。

既に当該授業のsessionsが1件以上存在する場合は、追加・解除操作を拒否する。

### 6.5 出席受付画面

```text
================================
 出席受付中
================================

授業：電子工学
第2回
開始：2026/10/20 09:00

受付人数：2 / 3

[09:01:10] 田中 → 出席登録完了
[09:02:15] 佐藤 → 出席登録完了
[09:03:00] 田中 → 登録済み

カードをタッチしてください。

Q：読み取り画面を終了
```

読み取り画面を離れても、DBの受付状態はOPENのままとする。

受付終了はメインメニューの専用操作から実行する。

SERIALモードではWindowsのコンソール入力を非ブロッキングで確認し、UID受信中でもQで画面を抜けられるようにする。

MOCKモードでは、UID入力欄に終了コマンドを入力できるようにする。

### 6.6 受付終了

終了操作時には対象授業名・授業回数を表示し、確認を要求する。

承認後にclosed_atを更新し、受付終了とする。

### 6.7 出欠履歴画面

```text
--- 電子工学 第2回 ---

受付開始：2026/10/20 09:00
受付終了：2026/10/20 09:20

履修者：3名
出席：2名
欠席：1名

ID  名前    状態    時刻
1   田中    出席    09:01
2   佐藤    出席    09:02
3   鈴木    欠席    -
```

受付中は「欠席」ではなく「未受付」と表示する。

---

## 7. Arduinoファームウェア詳細設計

### 7.1 初期化処理

Arduinoの `setup()` では次を実施する。

1. シリアル通信を初期化する。
2. SPI通信を初期化する。
3. RC522を初期化する。
4. RFIDリーダーの初期化結果を確認する。
5. 成功時にREADYを通知する。
6. 失敗時にエラーを通知する。

SPIピン設定、リセットピン、ライブラリの初期化関数は実機の型番・資料を確認して確定する。

### 7.2 メインループ

`loop()` は以下を繰り返す。

1. PCからの制御メッセージを確認する。
2. 新しいカードの有無を確認する。
3. カードがあればUIDを取得する。
4. UIDを16進文字列へ変換する。
5. 連続読み取り抑制条件を確認する。
6. 必要に応じてPCへ送信する。
7. カード読み取り後の終了処理を行う。

長時間の `delay()` による待機は極力使用せず、必要なら `millis()` による経過時間判定を利用する。

### 7.3 連続読み取り対策

同一カードを短時間で繰り返し読み取る場合、不要な送信を抑制する。

ただし、カードを離して再度かざしたときに読み取れなくなるような実装にはしない。

カード離脱の検出方法、RC522の読み取り状態、ライブラリの動作は実機確認後に最終決定する。

---

## 8. シリアル通信詳細設計

### 8.1 通信パラメータ

| 項目 | 設定 |
|---|---|
| ボーレート | 115200 bps |
| データビット | 8 bit |
| パリティ | なし |
| ストップビット | 1 bit |
| 文字コード | ASCII |
| 改行 | LF |
| UID形式 | 英大文字の16進文字列 |

### 8.2 メッセージ定義

**PC → Arduino**

| メッセージ | 意味 |
|---|---|
| `HELLO` | Arduinoの準備状態を確認する |

**Arduino → PC**

| メッセージ | 意味 |
|---|---|
| `READY` | UID読み取り可能 |
| `UID:XXXXXXXX` | 読み取ったUID |
| `ERR:RFID_INIT` | RFID初期化失敗 |

メッセージ末尾にはLFを付ける。

`UID:` の後ろは実際のUID長に対応した8、14、20文字の16進文字列とする。

### 8.3 接続時の同期処理

1. PCがシリアルポートを開く。
2. Arduinoの初期化を待つ。
3. PCがHELLOを送信する。
4. ArduinoがREADYを返信する。
5. PCがREADYを確認する。
6. UID受信を開始する。

Arduinoは起動直後にもREADYを送信する。

接続時にREADYを取り逃がした場合にも対応できるよう、HELLOへの応答を用意する。

準備完了待ち時間は初期値5秒程度を候補とし、実機で確認する。

### 8.4 受信データの検証

PCはLFまでの1行をメッセージとして処理する。

- 最大受信行長を設定する。
- ASCII以外のデータを拒否する。
- 不完全な行をUIDとして扱わない。
- 未定義のメッセージを無視または警告する。
- `UID:` の形式と長さを検証する。
- `READY` と `ERR:` をUIDと混同しない。

初期の最大行長は64バイトとする。

### 8.5 シリアルポートの接続タイミング

SERIALモードでは、以下の操作を行うときだけポートを開く。

- 学生登録のカードスキャン
- 出席受付のカード読み取り

読み取り画面から離れた場合はポートを閉じる。

新たに読み取り画面へ入った際にはREADYを確認し、待機中に受信された古いデータを破棄する。

PC画面に「カードをタッチしてください」と表示した後から、新しいUIDを受け付ける。

これにより別の操作画面で読み取られたUIDが、次の出席処理に混入することを防ぐ。

---

## 9. 受付の状態管理と復旧

### 9.1 状態遷移

- 未作成 → OPEN
- OPEN → CLOSED

CLOSED → OPENの遷移は禁止する。

### 9.2 起動時の処理

PCアプリ起動時、次の問い合わせを実行する。

```sql
SELECT *
FROM sessions
WHERE closed_at IS NULL;
```

結果が0件なら通常メニューを表示する。

1件なら復旧メニューを表示する。

複数件が検出された場合はDB整合性エラーとし、自動的に変更しない。

### 9.3 復旧操作

**再開**

- 既存のsession_idを使用する。
- 受付開始日時を変更しない。
- 出席済み情報を保持する。
- RFIDまたはMOCK入力を再開する。

**終了**

- 既存セッションのclosed_atへ現在日時を保存する。
- 既存の出席情報を変更しない。
- 以後の出席登録を禁止する。

### 9.4 アプリ終了時

OPEN状態の受付が存在する場合は警告を表示する。

- 受付を終了してアプリを終了
- OPENのままアプリを終了
- 終了を取り消す

OPENのまま終了した場合、次回起動時に復旧画面を表示する。

---

## 10. 例外・障害処理

| 障害 | 処理 |
|---|---|
| DBファイルを開けない | 起動エラーを表示して終了 |
| DB制約違反 | 操作を拒否し、原因を表示 |
| DB保存失敗 | ロールバック |
| 未登録カード | 登録せず警告 |
| 二重出席 | 既存記録を維持 |
| 履修対象外 | 出席を拒否 |
| 不正UID | 処理しない |
| Arduino接続失敗 | エラー表示、再試行可能 |
| READYタイムアウト | 接続失敗として通知 |
| 通信途中の切断 | 読み取り停止、再接続を案内 |
| アプリ異常終了 | 次回起動時にOPENセッションを検出 |

通信断の瞬間に読み取られたカードについては、正常保存が確認できない場合、利用者に再スキャンを要求する。

---

## 11. テスト詳細設計

### 11.1 DB単体テスト

- 学生登録・一覧取得
- UID一意制約
- 授業登録
- 履修登録・解除
- 二重履修の拒否
- 初回受付後の履修変更拒否
- 授業回数の採番
- 同時OPENセッションの拒否
- 二重出席の拒否
- 外部キー制約
- 未受付・欠席判定
- DB永続化

### 11.2 PC側処理テスト

- 正常な仮UIDの入力
- 不正なUIDの拒否
- 学生未登録の判定
- 履修対象外の判定
- 出席登録結果の正確性
- 模擬入力と実機入力の共通処理
- 異常終了後のセッション復旧
- DB失敗時のロールバック
- 終了済み受付への追加拒否

### 11.3 シリアル通信テスト

- READY受信
- HELLOへの応答
- 正常UID受信
- 不正なUIDメッセージ
- 不完全な行
- 通信タイムアウト
- 通信切断・再接続
- 古いUIDの破棄

### 11.4 Arduino実機テスト

- RC522初期化
- 実験用カード読み取り
- 実験用キーホルダー型タグ読み取り
- UIDの再現性
- 連続読み取り
- 同じカードの再タッチ
- 異なるカードの連続読み取り
- PC切断後の再接続
- 通信データの整合性

### 11.5 検証レベル

次の結果を区別して記録する。

1. 静的レビュー
2. SQLite単体試験
3. Python単体試験
4. PlatformIOビルド
5. シリアル通信試験
6. Arduino＋RC522実機試験
7. 統合動作試験

ビルド成功や模擬入力テスト成功だけでは、実機動作確認済みとは扱わない。

---

## 12. 実装順序

### Phase 1：DB基盤

- スキーマ作成
- 接続処理
- 学生・授業管理
- 履修管理
- DB単体テスト

### Phase 2：出欠管理

- 受付開始・終了
- 出席登録
- 欠席判定
- 履歴表示
- 異常終了時の復旧

### Phase 3：CLI・模擬入力

- 対話型メニュー
- MOCKによるUID入力
- 学生登録
- 出席受付操作
- 正常系・異常系テスト

### Phase 4：Arduino

- PlatformIO設定
- RC522初期化
- UID取得
- シリアル送信
- 連続送信抑制

### Phase 5：統合

- pySerial接続
- READY/HELLO同期
- 実機による学生登録
- 実機による出席登録
- 通信障害試験
- 最終受入テスト

---

## 13. 実装前に確認する事項

次の項目のみ、実機・環境に依存するため確認待ちとする。

- Arduino Unoの正確な型番とリビジョン
- RC522モジュールの正確な型番
- RC522とArduino間の電圧・信号レベル適合性
- SPIピン・RSTピンの配線
- 使用するMFRC522ライブラリとバージョン
- 実験用カード・タグのUID形式
- Windows上で使用するCOMポート
- PythonとPlatformIOのインストール環境

これらが未確認でも、PC側のDB・CLI・模擬入力部分は開発可能とする。

---

## 14. 詳細設計の完了条件

以下を満たした場合、詳細設計を完了とする。

1. 要件定義v1.0との対応に矛盾がない。
2. 5テーブルの構成と制約が定義されている。
3. PC側プログラムの責務が明確になっている。
4. CLIの主要操作が定義されている。
5. 受付の状態遷移が定義されている。
6. UIDの入出力・形式が定義されている。
7. 出席登録のトランザクションが定義されている。
8. 異常終了・通信障害への対応が定義されている。
9. テスト項目が定義されている。
10. 実機依存の未確認事項が明示されている。

**詳細設計 v0.1 — レビュー待ち**

本設計は、要件定義および基本設計を満たすための実装前仕様である。レビュー承認後、Codexに各Phaseの実装を依頼する。