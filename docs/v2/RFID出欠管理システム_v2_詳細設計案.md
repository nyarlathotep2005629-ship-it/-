# RFID出欠管理システム v2 詳細設計案

| 項目 | 内容 |
|---|---|
| 状態 | v2ソフトウェア実装済み。自動検証一部PASS。実機・学校貸出PC受入は未完了 |
| 作成日 | 2026-11-04 |
| 対象 | Maker UNO + RC522、ESP32-DevKitC v4、Windows PC |
| 正本 | PC側SQLite |
| 展示対象 | 学校貸出Windows PC、専用ESP32 SoftAP、展示用ダミーデータ |

本書は既存のv1要件・基本設計・詳細設計・Web版設計を置き換えず、IoT構成と学校貸出PCでの展示配布を追加する。第1～16節は当初の設計案、第17節は2026-11-04時点の実装範囲・検証証拠・未確認事項であり、実装事実が異なる場合は第17節を優先する。作業は`C:\Users\hiroki\Documents\RFID出欠管理システム\v2-work`の隔離clone、`codex/rfid-v2-showcase` branchで行った。元の指定フォルダは変更していない。第17.1節は初回実装記録時点のGit状態を示し、その後の保存・公開状態はこの作業の最終報告を参照する。実機書込みは行っていない。

## 1. 調査結果と設計前提

### 1.1 既存システム

- Python CLI、React + TypeScript + Vite、FastAPI、SQLiteを持つ。Webバックエンドは既存の`pc.database`と`pc.reader`を利用する。
- SQLiteの既存テーブルは`students`、`courses`、`enrollments`、`sessions`、`attendance`。UIDの一意制約・形式CHECK、履修確認トリガー、OPENセッション一件制約、出欠の複合主キーがある。
- ArduinoはMaker UNO向けPlatformIO `uno`環境、RC522 SPI D10-D13、RST D9、シリアル115200 bps。`HELLO\n`に`READY\n`、カードに`UID:<大文字16進>\n`、故障時に`ERR:<コード>\n`を返す。診断出力は`DIAG:`行。
- Python ReaderはREADY確認、UID形式検証、64 byte超過行の拒否、短いread timeoutを持つ。既存MOCK/SERIALとCLI/Webの一部業務フローは再利用する。
- FastAPIは既定で`127.0.0.1:8000`、Viteは開発時`127.0.0.1:5173`。Viteのビルド成果物があればFastAPIで静的配信する既存経路がある。
- 既存設計は単一PC・認証なし・DBスキーマ変更なし・CUIとWebの同時利用禁止を前提にする。v2のデバイス同期はこれらの新要件を追加・更新する。

### 1.2 ローカルとGitHub mainの比較

- 指定フォルダ`C:\Users\hiroki\Documents\RFID出欠管理システム`には`.git`がなく、Git repositoryではない。`git status`、remote、ローカルHEAD、stage/unstagedの判定は実行不能。
- 読み取り専用で確認したGitHub `main` HEADは`df70f262590727d266e52535bcefec9bfa148c2a`（2026-10-23）。[GitHub main commit](https://github.com/nyarlathotep2005629-ship-it/-/commit/df70f262590727d266e52535bcefec9bfa148c2a)
- Git blob形式SHA-1によるローカル展開ファイルとmainの内容比較では、共通54ファイルが一致し、`基本設計.md`、`要件定義.md`、`詳細設計.md`、`arduino/.gitignore`の4ファイルは内容が異なる。比較対象ファイルのうちmainに存在しローカルにないものは確認されなかった。
- ローカルにのみ存在するファイルは74件。大半は`.pio`ビルド成果物、PlatformIO依存物、VS Code設定、テスト一時フォルダ等であり、Git上の未追跡か無視対象かは`.git`不在のため断定できない。ルートには`attendance.db`とPhase 5 ZIPもあるが、DB内容は読み取っておらず変更していない。
- 以上はファイル内容の比較であり、コミット単位の差分やローカル未コミット変更の確定ではない。
- 実装用cloneはGitHub `main`の`df70f262590727d266e52535bcefec9bfa148c2a`を基点に作成された。作業開始時点でbranchは`codex/rfid-v2-showcase`、tracked変更8ファイルと新規ファイル群があり、未commitである。これは元の指定フォルダの状態とは別に管理している。

### 1.3 既存設計との関係・矛盾

| 既存前提 | v2で追加する案 | 影響・扱い |
|---|---|---|
| UNOがUSBシリアルでPCへ直接UIDを送る | UNO UART → ESP32 → Wi-Fi → PC | 既存USB SERIALは残し、新しい`ESP32`入力モードを追加する。UNOの転送先はビルド構成で選択する案 |
| MOCK/SERIALのみ | MOCK/SERIAL/ESP32 | 既存モードの挙動を変えない |
| DB変更なし | 同期Inbox・デバイス状態等を追加 | バージョン付き加算migrationを設計。既存DBへ無確認の自動再構築はしない |
| Webはloopbackだけ | PC Web UIは引き続きloopback。PCがESP32へHTTP要求 | 学校PCで外向きローカル通信が制限される場合は学校管理者確認が必要 |
| 認証なし、ローカル単独利用 | AP保護、デバイストークン、操作認可 | v2の信頼境界を新設。公開APIや外部ネットワークへの露出は禁止 |
| 同時操作しない | 展示アプリを唯一のDB/デバイス操作主体とする | CUIとの同時利用禁止は維持 |

## 2. 再利用範囲と追加ファイル案

### 2.1 再利用範囲

- `pc.database.normalize_uid()`のUID形式（4/7/10 byte、8/14/20桁16進）と学生・授業・履修・出欠の判定規則。
- `start_session()`、`get_open_session()`、`close_session()`、`get_attendance_report()`等の業務ルール。イベント取込は一意なevent IDと既存セッション制約を含む新しい薄い入口を設ける。
- `arduino/src/main.cpp`のRC522初期化、UID読み取り、同一カード連続抑止、`READY/UID/ERR/DIAG`文法。出力先をUSB SerialまたはESP UARTに切替可能なtransport層を追加する案。
- `pc.reader.SerialReader`は既存USB直結SERIALで維持する。ESP32側では同等の行長制限・UID検証・タイムアウト方針を実装するが、PCのpySerial Readerを無理にネットワーク対応へ改造しない。
- FastAPI routers/services、React API client/types、既存CRUD・授業・履歴画面を拡張する。MOCK/SERIALテストを回帰対象として残す。
- 既存のテスト構造は維持し、新しいPython・TypeScriptのMock/統合テストを追加する。PlatformIO ESP32ビルド設定はUNO環境と分離する。

### 2.2 初期設計での追加・変更候補

**新規候補**

- `esp32/platformio.ini`、`esp32/src/main.cpp`、`esp32/include/`（UART、HTTP、cache、event queue、stateの小さな責務分離）。
- `esp32/data/`またはファームウェア埋込み静的資産（ESP簡易UI）。実際のLittleFS/パーティション量はDevKitCのFlash容量確定後に決める。
- `web/backend/app/services/device_sync.py`、`web/backend/app/routers/devices.py`、`web/backend/app/services/event_import.py`。
- `web/backend/migrations/`（version付き加算migration）、`web/backend/app/services/demo_seed.py`（展示専用DBへのダミーデータ投入）。
- `web/frontend/src/pages/DeviceManagement.tsx`、必要なAPI client/types/UI部品。
- `tests/`、`web/backend/tests/`、`esp32/test/`、`docs/v2/`の同期・状態・配布検証資料。
- 展示用配布定義（例：`packaging/showcase.spec`、`packaging/`ビルド手順、`Start-Showcase.cmd`または同等ランチャー）。名前・最終配置は承認後に確定する。

**変更候補**

- `arduino/platformio.ini`と`arduino/src/main.cpp`：USB Serial互換を保ったESP UARTビルド/接続切替。
- `pc/database.py`、`pc/schema.sql`：migration管理と冪等なイベント取込入口。
- `web/backend/app/config.py`、`main.py`：開発/展示設定、SQLiteパス、静的資産パス、起動ログ、同期サービス起動停止。
- `web/backend/app/routers/`とReact `App.tsx`：デバイス状態・同期・遠隔操作・展示モードを追加。
- `web/frontend`ビルド設定・配布手順、`.gitignore`、README、既存詳細設計・テスト資料：配布物除外、利用手順、設計追記。

この一覧は当初の候補であり、実際に追加・変更したファイルと未実装項目は第17節で対応付ける。

## 3. v2全体アーキテクチャ

```text
Maker UNO + RC522
  └─ UART行プロトコル（HELLO / READY / UID / ERR）
       ↓ 3.3V対応レベル変換を介す
ESP32-DevKitC v4（専用SoftAP、HTTP API、LittleFS耐久キュー）
  ├─ UID cache（氏名なし、版付きスナップショット）
  ├─ 未同期scan event（append-only、ACKまで保持）
  ├─ PCからのcommand / 同期 / 診断要求
  └─ ESP簡易Web UI（CDNなし）
       ↕ HTTP/JSON（PCが接続を開始し、取得・操作・ACKする）
Windows展示PC
  ├─ React静的ビルド + FastAPI（loopback、単一プロセス配布）
  ├─ 展示DB（ダミーデータ専用）または明示選択された開発DB
  ├─ 出席判定・正式保存・イベントInbox・同期状態
  └─ 展示ランチャー、ヘルス確認、ログ
```

**責務境界**：UNOはUID読取のみ。ESP32は読取状態、キャッシュ照合用の補助表示、永続キュー、再送を担当し、出席を確定しない。PCはUID/履修/セッションを再検査し、正式な出欠判定とSQLite確定を担当する。ESP32の「受付済み」は「端末で耐久保存済み」を意味し、正式な出席確定ではない。

**通信方向**：要件上の双方向HTTPは維持し、どちら向きの業務データもHTTPで運ぶ。学校PCの受信ポートや管理者権限を避けるため、通常運用ではPCがESP32のHTTP serverへ接続する。PCはevent/cache/statusをGETし、command/cache更新/ACKをPOSTまたはPUTする。ESP32からPCへの自発的HTTP POSTは行わない。将来push方式へ変える場合はWindows firewall・学校ポリシー評価を別途必須とする。

## 4. UNO–ESP32 UART・配線設計

### 4.1 行プロトコル

| 方向 | 行 | 意味 |
|---|---|---|
| ESP→UNO | `HELLO\n` | 接続確認。起動時と再接続時に送る |
| UNO→ESP | `READY\n` | RC522初期化済み |
| UNO→ESP | `UID:<HEX>\n` | 正規化前UID。4/7/10 byteを大文字16進で表す |
| UNO→ESP | `ERR:RFID_INIT\n`等 | 読取・初期化エラー |
| UNO→ESP | `DIAG:<KEY>=<VALUE>\n` | 起動診断。出席イベントにしない |

- 既存のメッセージ意味とLF終端は保つ。ESPは起動時に受信バッファを捨て、`HELLO`を最大3回・0.5秒間隔で送信し、5秒以内の`READY`だけを有効にする。READY前UIDは破棄する。
- 行長は既存Readerと同様に64 byte以下。ASCII以外、切れた行、不正なUID、未知の`ERR`、タイムアウトはエラー診断として扱い、出席データを作らない。
- 拡張版HELLO/能力交渉は将来候補。初期版は既存UNOを変えなくても通信確認できる最小プロトコルを優先する。ESPからUNOへのSTOP/PAUSE操作は既存UNOが未対応のため、ESP側で読取イベントの受付を停止する。UNOが送信を続ける場合もキューへ入れない。
- 接続後にUART無通信が続いてもUNOのハートビートがないため、物理断線を即時に判定できない。UART状態は「READY後・最後の有効行時刻」と診断結果で表示し、切断検知保証には周期PING応答を将来拡張として扱う。

### 4.2 ピン割当案と電気条件

| 接続 | 候補 | 条件 |
|---|---|---|
| RC522 SS / RST / SPI | UNO D10 / D9 / D11,D12,D13 | 現行スケッチを維持。RC522の型番・電源・ロジック許容値は未確認 |
| UNO ESP-UART RX / TX | UNO D2 / D3をSoftwareSerial候補 | 既存USB Serial（D0/D1、115200bps）との競合を避け、ESP向けビルドでは通信baudを9600または実測で決めた安定値へ別設定する。SoftwareSerialの負荷と欠落率は要測定 |
| ESP UART | ESP32 UART2 GPIO16 RX / GPIO17 TX候補 | UART0 GPIO1/3はUSB書込・ログ用途と重なるため避ける。DevKit配線・ピン使用状況を最終確認 |
| 電源・GND | UNOとESP32は各々の適切な電源、GND共通 | ESP32-WROOM-32Eは3.0–3.6V電源、外部電源は0.5A供給能力を前提とする。UNO 3.3V出力からESPを給電しない |

UNO TX(5V)→ESP RX(3.3V)とESP TX(3.3V)→UNO RX(5V)の両方向に、UART用途・向きを確認したレベル変換回路を入れる。抵抗分圧を使う場合はESP入力側のHigh/Low電圧を計算し、配線容量・baudで波形を確認する。ESP32入力へUNOの5V信号を直結しない。RC522側の電圧も別途3.3V適合を確認し、UNO 5V SPIを無条件に直結しない。USBとUARTで二つの送信源が同時にUNO RXへ出力しない配線にする。

UNO現行ファームウェアは`Serial`へプロトコルと診断を出すため、ESP UARTを使うビルドでは通信transportを切り替える小変更が必要。USB直結SERIALを残すビルド/環境は維持する。D2/D3とSoftwareSerial、RC522割込み・読取処理の同時動作は自動試験だけでは保証できず、実波形と連続読取の実機確認を残す。

## 5. HTTP API案

`/api/v1`をESP32 device APIのprefixとし、JSON UTF-8、UTC ISO-8601、最大request body/配列件数/timeoutを制限する。ESP32の固定IP案は`192.168.4.1/24`。APIはSoftAP側だけでlistenする。

| Method / Path | 要求・応答の要点 |
|---|---|
| `GET /api/v1/health` | `ok, firmware_version, boot_id, uptime_ms`。資格情報やUIDを返さない |
| `GET /api/v1/status` | 読取状態、UNO READY、last UART event、active session generation、cache version/stale、queue件数/最古event、最終同期/ACK、Wi-Fi接続数、時刻品質、診断コード |
| `GET /api/v1/events?boot_id=&after_seq=&limit=` | 最大50件、seq昇順。`boot_id`不一致、保持外cursor時は`reset_required`とする |
| `POST /api/v1/events/ack` | `{boot_id, ack_through_seq, request_id}`。PC DBに終端結果を永続化した連続範囲だけACK。重複ACKは成功として扱う |
| `PUT /api/v1/cache` | `{version, generated_at, sha256, entries:[{uid,student_ref}]}`。stagingへ全量保存し件数・形式・checksum検証後に原子的切替 |
| `POST /api/v1/commands` | `{request_id, command, session_id?, session_generation?, issued_at}`。受理時に202とcommand状態を返す。重複request_idは同じ結果を返す |
| `GET /api/v1/commands/{request_id}` | `ACCEPTED/RUNNING/SUCCEEDED/FAILED`と安全な結果コード |
| `GET /` | ESP内の静的Web UI。外部CDN、外部フォント、第三者analyticsなし |

command allowlistは`PAUSE_READING`、`RESUME_READING`、`SYNC_NOW`、`DIAGNOSTIC`、`REFRESH_CACHE`、`BIND_SESSION`、`CLEAR_SESSION`。任意のURL、任意コード実行、任意ファイル取得、遠隔ファームウェア書換えは提供しない。停止・再開・同期は冪等とし、`request_id`重複で二重操作しない。`BIND_SESSION`はPCがOPENを確認した時だけ許可する。

PC管理UIはFastAPIを通じてESP APIへ接続し、CORSは追加しない。ESPの全APIをPC browserから直接呼ぶ方式は採用しない。

## 6. UIDキャッシュ

- PCがSQLiteから登録済みUIDとopaqueな`student_ref`だけを生成する。氏名、出欠履歴、履修一覧は載せない。UIDは本人認証情報ではなく複製可能な識別子として扱う。
- cache snapshotは`version`単調増加、`generated_at`、件数、SHA-256 digestを持つ。PCのDB更新後に新snapshotを生成し、ESPへ更新指示を送る。転送失敗時は旧版を維持し、PC/ESP双方で`STALE`を表示する。
- ESPは新snapshotを一時領域へ書き、形式・件数・hashを全検査してから参照先を切り替える。電源断・破損時は最後に検証済みの版を保持する。空または壊れたsnapshotで古い正常cacheを上書きしない。
- キャッシュ照合は端末表示の「登録済み候補」だけに使う。最終判定はPCが現行DBで必ず再検査し、cache hitを出席確定にしない。
- UIDをRAM/flashに保持するため機密性リスクがある。表示は件数/版のみ、ログにUIDを出さない。保持するcacheは必要最小限とし、必要なら切断・展示終了後に管理操作で消去する。フラッシュ暗号化なしの物理解析耐性は保証しない。

## 7. 永続イベントキュー、ACK、重複排除

### 7.1 ESP32側保存

- 各UID読取から`event_id = device_id + boot_id + sequence`、`device_id`、`boot_id`、単調増加`sequence`、UID、`session_id`、`session_generation`、`captured_mono_ms`、時刻anchor ID、`time_quality`、format versionを作る。
- ESPがLittleFSへレコードとCRCを追記し、flush/commit確認した後だけ画面に「端末保存済み」を表示する。未ACKイベントは再起動後も残す。ACK前にリング領域を上書きしない。
- queueは容量上限を定め、空き容量不足・CRC不一致・書込み失敗時は新規読取受付を停止し、既存イベントを保持する。古いeventを黙って捨てない。空き容量・最古event時刻をUIに表示する。
- flashの書換寿命を考慮してバッチ化・連続追記・checkpointを行う。上限、LittleFSパーティション、電源断時の保証はFlash容量確定後に実測して決める。

### 7.2 PC同期と重複排除

1. PC同期serviceがESP statusを取得し、`boot_id`とPC cursorを照合する。
2. PCは`GET events`で未ACK eventを取得し、各eventをPC DBの`device_event_inbox`へまず記録する。UID/size/sequence/session/deviceを再検査する。
3. DB取込関数が、同じDBトランザクション内でevent_id重複を確認し、現行学生・履修・OPEN sessionを検査して既存出席ルールを適用する。既存attendanceの`(session_id,student_id)`主キーも二重登録防止として維持する。
4. Inboxが`APPLIED`、`DUPLICATE_ATTENDANCE`、`UNKNOWN_UID`、`NOT_ENROLLED`、`SESSION_CLOSED_REVIEW`等の永続終端状態になったeventだけ連続cursorとしてACKする。SQLite busy/IO error時はACKせず再送させる。
5. ESPは同じ`boot_id`の`ack_through_seq`までだけeventをACK済みcheckpointへ進める。途中の欠番がある場合は先へ進めない。応答消失後の再送も同じevent_idでPC側が冪等処理する。

この設計は通信上のexactly-onceを主張しない。配送はat-least-once、event取り込みの冪等性とattendanceの一意制約により、最終DBへの二重出席を防ぐ。

## 8. セッション・遅延イベント・時刻

- PCがSQLiteでOPEN sessionを作った後、ESPへ`BIND_SESSION(session_id, session_generation)`を送る。`session_generation`はsession ID再利用や古いqueue eventの誤結合を防ぐランダムUUID。ESPはACTIVE bindingがない場合にスキャンを保存せず、状態を明示する。
- PC/Webの終了操作は「停止要求 → ESPが新規受付停止 → 最終sequence/watermark応答 → PCが未同期queueを全pull・永続化・ACK → PCがsession CLOSED化」の順に実行する。同期中断・未ACK・ESP応答timeoutならsessionを閉じない。
- PCが停止/終了前に電源断・ネットワーク断となった場合、SQLiteのOPEN状態は維持する。再起動後はreaderを自動再開せず、ESP queueとsession状態を照合し、管理者が再開/回収/終了を選ぶ。
- PCがすでにsessionを閉じた後に届くeventは自動出席へ適用しない。`SESSION_CLOSED_REVIEW`としてInboxへ保存し、管理UIで要確認にする。手修正は監査記録付きの明示操作が必要。
- ESPはRTCを前提にしない。PC接続時にUTC時刻anchorとESP monotonic counterを渡す。オフラインscan時刻はanchorから推定し、`time_quality=SYNCED/ESTIMATED/UNKNOWN`と推定誤差情報を保存する。ESP再起動でmonotonicが戻ったeventは新boot_idで区別する。時刻不確実時は到着時刻で黙って置換せず、PC UIに推定/未確定を示す。
- 出席の正本時刻は既存スキーマのUTC ISO文字列に合わせる。時刻がUNKNOWNのeventを出席扱いにできるか、別途業務判断を要する。

## 9. PC SQLite追加テーブル・移行

既存5テーブルの意味と主キーは保持し、バージョン管理された加算migrationにする。

| テーブル案 | 主な列・制約 | 用途 |
|---|---|---|
| `devices` | `device_id` PK、`display_name`、`secret_hash`または保護済みcredential参照、`last_seen_at`、`firmware_version`、`created_at`、`disabled_at` | 登録端末と認証状態 |
| `device_sync_state` | `device_id` PK/FK、`boot_id`、`last_seen_seq`、`last_acked_seq`、`cache_version_sent`、`last_sync_at` | PC同期cursor、cache同期の復旧 |
| `device_event_inbox` | `event_id` PK、`device_id` FK、`boot_id`、`sequence`、`session_id` FK nullable、`session_generation`、UID、capture情報、受信時刻、`status`、`result_code`、処理時刻 | 重複排除、未処理復旧、監査・手動レビュー |
| `session_device_bindings`（案） | `session_id` FK、`session_generation` UNIQUE、`device_id` FK、作成/解除時刻 | DB sessionと端末bindingの照合 |

- `UNIQUE(device_id, boot_id, sequence)`も付け、event_id再利用や端末sequence衝突を検知する。
- InboxのUIDは必要な取込・監査範囲でのみ保存し、ログへ複写しない。個人情報保持期間と削除手順を定める。
- 現行`open_database()`の`CREATE TABLE IF NOT EXISTS`だけでは既存テーブルへ列を足せない。起動時migrationは`PRAGMA user_version`等の版確認、既存スキーマ検査、バックアップ、transaction内の加算DDL、再起動後の整合確認を備える。
- version未設定の現行DBはv1 baselineとしてテーブル/trigger/indexをread-only検査し、想定外・破損・旧schemaなら停止して案内する。自動的なcore table再構築、データ補完、上書きseedをしない。
- 展示DBにはmigration後に決定論的なダミー授業・学生・履修をseedする。seedは展示DBでのみ実行し、実在UID・実学生情報を持ち込まない。
- 取込は既存`register_attendance()`の業務規則を再利用するが、現関数の個別transactionとInbox記録を一体化できるよう、実装時にはDB境界を小さく整理する。例外でInboxだけACK可能になることは禁止。

## 10. ESP32・PC状態遷移

### 10.1 ESP32

```text
BOOT → AP_STARTING → AP_READY → UNO_WAIT_READY → IDLE
  IDLE → SESSION_BOUND → READING
  READING → PAUSED / QUEUE_FULL / UART_ERROR / STORAGE_ERROR
  PAUSED → READING（明示再開、session binding/cache再確認）
  いずれもPC未接続 → OFFLINE_SYNC_PENDING（読取可否はsession/cache/queue方針で決定）
  同期成功後 → 同じ読取状態へ復帰
```

`QUEUE_FULL`、storage破損、UNO未READYは安全側に停止。Wi-Fi切断だけでは未同期eventを消さない。APは稼働しているがPC不在という状態と、RC522/UART不調を分ける。

### 10.2 PC

```text
STOPPED → STARTING → DB_READY → DEVICE_DISCOVERY → ONLINE_SYNCING → READY
READY → SESSION_OPEN → DEVICE_BOUND → READING
READING ↔ OFFLINE_QUEUEING（ESP status/HTTP timeout）
OFFLINE_QUEUEING → RECONCILING → READY/READING（全cursor・session検査後）
任意状態 → DEGRADED（DB/データ破損、migration拒否、認証失敗）
任意状態 → STOPPING → STOPPED（停止確認・log flush）
```

PC server再起動時にSERIAL readerやESP読取を勝手に再開しない。DB OPEN session、ESP active session、pending queueが不一致なら`NEEDS_REVIEW`にし、出席確定操作をブロックする。

## 11. UI機能仕様

### 11.1 ESP簡易Web UI

- 状態トップ：AP IP、UNO READY/読取状態、PC最終同期、cache version/stale、queue件数/容量、時刻品質、最終診断結果。
- 操作：読取停止/再開、同期、診断、cache更新要求。request IDと完了状態を示し、連打を防ぐ。
- 診断：UART baud/handshake、RC522エラーコード、flash空き容量、CRC異常数、HTTP要求結果。氏名・UIDは表示しない。
- queue一覧：event IDの一部、sequence、時刻品質、session、処理結果のみ。UIDは通常UIでマスクし、手動レビューの権限ある画面でのみ必要最小限表示。
- 全資産をESP側またはPCパッケージに同梱し、CDNやInternetがなくても表示できる。

### 11.2 PC管理UI

- 既存dashboardへESP接続、UNO READY、cache同期、queue pending、最終ACK/同期時刻、時刻品質を追加。
- デバイス詳細画面：接続/読取状態、cache refresh、同期、診断、停止/再開、queue review、ファームウェア/boot_id。破壊的操作は確認ダイアログ。
- 授業回画面：OPEN sessionに端末をbindingし、未同期が0であることを確認してから終了可能にする。停止/同期失敗中は終了を無効化。
- 出欠履歴に`端末仮受付`、`同期済み`、`要確認`、時刻品質の区別を付ける。端末ローカル受付を正式なPRESENTと誤表示しない。
- 学生登録/授業登録/履修変更後にcache versionが必要なら再生成し、完了/失敗/staleを表示。
- 展示モードでは常時「展示用データ」bannerを出し、demo DB file pathと初期化状態を表示する。実運用DBを選択している場合は展示モードを開始させない。

## 12. 障害復旧・セキュリティ

| 異常 | 動作 |
|---|---|
| UNO/RC522未READY | 読取開始を拒否。診断と配線確認を表示。sessionはOPENのまま |
| UART切れ/不正行 | 不正eventを生成しない。通信エラーを記録し、再handshakeを試す。既存queueを保持 |
| PC/ESP Wi-Fi断 | ESPは対象OPEN session bindingに限って仮受付・永続化。cache/queueの容量と時刻品質を監視 |
| ESP再起動/停電 | CRC・boot_id・sequenceからqueueを復旧。破損部分は隔離し自動消去しない。session状態をPCと照合 |
| HTTP request/ACK応答消失 | 同じcursor/request_idで再試行。PC InboxとESP ACKは冪等 |
| SQLite busy/full/corrupt | Inbox/attendanceをACKしない。busyは限定指数backoff、破損は同期停止とバックアップ案内 |
| cache不一致 | stale警告。PCの現DB判定を優先し、session遅延eventは自動確定しない |
| 起動時DB migration拒否 | 起動を止め、DB path・版・ログを示す。旧DBへ自動fallbackしない |
| event sessionがCLOSED/不明 | `SESSION_CLOSED_REVIEW`。自動attendanceを作らない |

**ネットワーク**：APは専用SSID、WPA2-AES以上の利用可能モード、端末ごとのランダム長パスワードを使用。初期共通password、Internet bridging、APから学校LANへのroutingは設けない。端末数はPC 1台を標準とし、不要clientは拒否する。`192.168.4.1/24`は候補で、同一学校ネットワーク接続時のroute競合を避けるため学校Wi-Fiは切断してから接続する。

**認証/認可**：AP passwordに加え、ペアリングでPCとESPへ別端末用tokenを登録し、管理APIを認証する。HTTPはWPA2で保護されたisolated link内のみ。tokenをURL query、ログ、画面へ出さない。request ID/nonceで再送・replayを抑え、command allowlistと型/長さ/range検証を行う。カードUIDは本人認証ではない。

初期案は端末ごとの共有secretを使ったHMAC-SHA-256署名（method、path、body hash、timestamp、nonceを署名対象）とし、ESPは短い有効時間窓とRAM上のnonce重複拒否を行う。再起動を跨ぐcommand重複を永続`request_id`で抑えるのは将来の強化項目で、現実装のrequest_id重複排除ではない。ESP側secretはfirmwareへcompile-timeで組み込み、PC側credentialはユーザー単位のWindows DPAPIで保護する。AP接続はPC 1台に限定し、SSID passwordは展示来場者へ公開しない。HTTPはlink encryptionに依存しend-to-end TLSではないため、学校LANへbridgeせず、第三者clientを参加させない。PC loopback UI/APIには許可Host検査とCSRF対策が必要だが、現試作で人のログインやこの保護を追加していない。CORS origin制限を認証・認可として扱わない。

**Windows firewall/IP/権限**：PCはESPへ接続するHTTP clientであり、PCのLAN向けlisten portは作らないため、通常設計ではWindows Defender Firewallのinbound例外も管理者権限も不要。学校GPO/endpoint securityがoutbound private subnet通信やSoftAP接続を禁止する場合は管理者確認が必要。PCはIP固定を要求せずDHCPを使う。PCから常にESPの`192.168.4.1`へ接続するのでPC側固定IPは不要。学校ポリシーによりネットワークプロファイルやWi-Fi自動接続が制限される可能性は現地で確認する。

**ログ/データ**：初期配置案は`Data/Logs/`だったが、現試作は`Data/Showcase/Logs/`へ保存する。起動時刻、起動段階、例外、server状態を記録する。UID、token、SSID password、氏名は記録しない。launcher logは無制限追記であり、application logは最大2 MB・3 backup。デモDB・ログ・pairing credentialは実行ファイル外の書込可能領域に置く。

## 13. 学校貸出PC向けポータブル配布案

### 13.1 比較

| 方式 | 配布サイズ | 起動 | 管理者権限 | セキュリティソフト/制約 | 保守性 |
|---|---|---|---|---|---|
| PyInstaller `onedir`（試作採用） | 最終フォルダ実測43,158,241 byte（約41.16 MiB、85 files、EXE単体16,436,026 byte）。React assetsとPython/native依存、導入手順を含む | `Start-Showcase.cmd`で起動し、health確認後browserを開く | 通常はインストール不要。ユーザー書込可能場所で実行 | 未署名EXE/同梱DLLがSmartScreen/Defender/学校EDRに止められる可能性は未確認。onedirはファイル単位で検査しやすい | 既存Python/FastAPIを維持。build依存固定。配布ZIPのSHA-256を記録 |
| PyInstaller `onefile` | 圧縮配布はon-dirと同程度の可能性。実行時は一時展開領域を別途使用 | 1ファイルだが展開待ち、Tempへの書込が必要 | 通常不要だがTemp実行が禁止されたPCでは失敗 | 自己展開型として警告・隔離される可能性、起動遅延・終了後cleanup故障 | 配布は簡単、障害時に展開先/同梱DLLの診断が難しい |
| Python embeddable runtime + bundled site-packages | 中〜大。ランタイム・依存wheel・native DLLを同梱。未計測 | portable Pythonを呼ぶlauncher。React静的assetsも同梱 | 通常不要 | Python runtime自体の許可は必要。EDR/WDAC/AppLockerで禁止なら起動不可。標準pip環境ではなく同梱依存管理が要る | 透過性は高いが、`._pth`/site-packages/native DLLの構成、更新、manifest管理を自前で保守 |
| ソース + Python/Node等を利用者側で準備 | 小さめのソース配布 | 複数のsetup/起動工程 | インストール権限が必要になる場合がある | PCの既存環境差に依存 | 本要件に不適合。採用しない |

配布サイズはこの試作物で測定済み。cold start単体計測、Defender/EDR評価、クリーンWindows環境での確認は未実施。

### 13.2 推奨構成

- **初期配布方式はPyInstaller onedir**とし、FastAPI backendをWindows x64 executable化、React + TypeScriptは事前buildした`dist`を同梱する。試作パッケージは本資料記載サイズで構築・loopback smoke確認済み。実行時にNode.js/pnpmは不要。既存FastAPIの静的配信経路でWeb UI/APIをloopback同一originから提供する。学校PCでの最終的な動作確度は未検証で、EDR等で拒否される場合はPython embeddable比較へ戻る。
- Vite/TypeScript/PlatformIOは開発PCでのみ使用する。展示PCにはPython、Node.js、pnpm、VS Codeを要求しない。
- 目標は一操作で起動・終了できるlauncherとし、設定検査、demo DB選択、backend起動、health polling、既定browser表示を担当させる。現試作は`Start-Showcase.cmd`が同じフォルダのEXEを起動し、EXEがserverを同一process内のthreadで実行する。
- 現試作では準備状態確認後にbrowserを開き、起動consoleでEnterを押すとserver shutdownする。別server child processは作らない。終了前に未同期件数/結果を表示するUIは今後の改善事項。
- コピー展開先はユーザーが書ける`Documents`等を前提とする。現試作のDB/ログ/tokenは`Data/Showcase/`へ保存する。Program Filesや読み取り専用媒体への展開を成功前提にしない。書込失敗時の詳細UI案内は未実装。
- `dev`と`showcase`を別設定・別DB path・別seedで分離する。配布に開発DB、`attendance.db`、実学生データ、実tokenを含めない。デモ起動は常にデモDBを選び、ダミーデータseedは冪等とする。
- 目標としてDBの`quick_check`、schema版、書込権限、port利用可否、静的asset存在を検査する。現試作はFastAPI lifespanでDB初期化とv2追加migrationを実行し、health endpoint応答と静的assetを確認するが、DB integrity quick_checkやESP疎通を起動条件にはしていない。通常ユーザー権限での貸出PC実行は未検証。
- BrowserはPC内loopbackのURLだけを開く。React assets、font、icons、CSS/JS全て同梱し、外部CDNへ接続しない。app停止時はサーバーをgraceful shutdownし、DB connectionを閉じてlog flushする。
- 一時フォルダへの自己展開に依存しない`onedir`を選ぶ。PyInstaller資料もonefileはsupport filesを一時フォルダへ展開する動作を説明している。学校EDRがEXEを拒否した場合は、許可済み署名・管理者によるallowlist等を学校管理者と相談し、Defender/Firewall全体を無効化しない。

## 14. 自動テスト・展示環境受入計画

テスト計画は次の通り。実施済みCheckと未実施の受入項目は第17節に分けて記録する。

| 層/ID | 対象 | 主なケース・受入条件 |
|---|---|---|
| Unit `UART-*` | UNO/ESP frame parser | READY/UID/ERR/DIAG、CRLF/LF、4/7/10 byte UID、不正HEX、長行、切断、重複行、READY timeout |
| Unit `CACHE-*` | cache snapshot | version更新、同版再送、古い版拒否、hash/CRC破損、空配列、途中電源断、前版保持 |
| Unit `QUEUE-*` | event journal | append復旧、CRC不一致、boot_id変更、sequence重複/欠番、queue上限、ACK欠番拒否、ACK再送 |
| Unit `COMMAND-*` | command handler | allowlist、request_id重複、操作順序、timeout、停止/再開冪等 |
| DB `DB-MIG-*` | migration | v1 DB backup/版検査、正常加算移行、二重起動、制約保持、想定外DB拒否、rollback、PRAGMA foreign_key_check |
| DB `IMPORT-*` | Inbox + attendance | 1 event、同じevent再送、別event同一学生/session、未知UID、未履修、session閉鎖、DB busy/full、crash injection各境界、ACK可能範囲 |
| API `HTTP-*` | ESP/PC TestClient・FakeTransport | schema/size/auth、status、event cursor reset、command、cache refresh、offline/reconnect、error mapping |
| Integration `SYNC-*` | Mock ESP HTTP server + 一時SQLite | PC commit後ACK lost、再送、boot_id変化、PC/ESP再起動、queue全回収までsession close拒否 |
| UI `UI-*` | React component/browser automation | online/offline/stale、仮受付と正式出席の区別、要確認表示、二重操作防止、展示banner、失敗案内 |
| Package `PKG-*` | 展示成果物 | Python/Node/pnpm/VS Codeなし、CDN遮断、任意の書込可能フォルダへコピー、通常ユーザー、空Dataから起動、demo seed、2回起動時のport競合、ログ/終了 |
| Clean Windows `CLEAN-*` | fresh VM/Windows Sandbox相当 | PATHからPython/Nodeを除外、学校相当の標準user、Firewall既定、Wi-Fi接続、DHCP、再起動、起動停止、削除/再展開、Defender/EDR scan |
| Hardware `HW-*` | UNO+RC522+ESP実機 | 電圧/波形/baud、UART同時送信、カード連続読取、AP reconnect、停電・電源断、flash復旧、長時間、実際のDevKit Flash容量 |

テストはデモDBまたは一時DBのみを使い、展示用DB・実運用DBを自動suiteへ流用しない。Mock/Fake合格、package起動合格、実機合格を別々に記録する。学校所有PCのEDR/GPOと管理者権限の条件はVMだけで代替できず、学校ITの許可後に現地受入が必要。

## 15. 実装フェーズと依存関係

1. **設計承認・現物仕様確定**：UNO/Maker UNO revision、RC522型番、ESP32 board/flash、UART電気条件、offline session policy、学校PCのWindows/制限を確認。
2. **契約とDB案を実装可能状態へ確定**：HTTP schemas、event version、queue上限、session generation、migration/rollback、demo data内容、logging/retentionを決定。
3. **ESP mock + PC sync**：Mock ESP HTTP serverでInbox、冪等取込、ACK、状態表示、障害復旧を作り、DB/APIテストを通す。実機依存を先にしない。
4. **ESP32 firmware**：AP/API、UART parser、cache snapshot、LittleFS queue、状態診断、ESP簡易UIを実装し、PlatformIOビルドとsimulated transportテスト。
5. **UNO bridge mode**：既存USB SERIALを保つtransport分離、ESP UART向けビルド、Uno回帰テスト。最後に電気・baudの実測。
6. **PC React/FastAPI UI**：device management、session bind/stop/drain、offline/late review、展示モードを追加。
7. **portable packaging**：Vite dist同梱、PyInstaller onedir、起動/終了ランチャー、dev/showcase設定分離、demo DB、ログ、manifestを作成。
8. **検証・展示受入**：unit→component→integration→build/package→clean Windows→学校貸出PC→実機長時間の順に実行し、各層を別判定。

依存関係は1→2→3/4→5/6→7→8。特に3のMock同期・DB状態機械を先に仕上げることで、実機がなくても重複・再送・session境界を検証できる。

## 16. 未決定事項・リスク・開始前判断

| ID | 判断事項 | 推奨案 / リスク |
|---|---|---|
| D-01 | offline時の仮受付条件 | PCがOPEN sessionを作成済み、ESP binding済み、queue容量正常、session_generation一致の時だけ許可 |
| D-02 | CLOSED後に到着したevent | 自動出席へ入れず`SESSION_CLOSED_REVIEW`。展示時も一貫適用 |
| D-03 | event時刻がUNKNOWN | 要確認へ回すか受信時刻で出席可能か、業務承認が必要 |
| D-04 | Maker UNO UART実装 | D2/D3 SoftwareSerial低速案を推奨するが、実配線・欠落率・SPI共存を実測して確定 |
| D-05 | ESP32 Flash/partition | 実DevKitCのWROOM型番とFlash容量確認後にWeb asset、cache、queueの割当を決める |
| D-06 | queue/cache容量・保持日数 | 想定カード数、来場者数、オフライン時間、イベントレートから算出。容量不足は停止しdropしない |
| D-07 | 展示PCのWindows・制限 | Windows版/architecture、標準user、EDR、AppLocker/WDAC、Wi-Fi SoftAP接続、外向き通信制限を学校へ確認 |
| D-08 | AP credential配布 | 端末単位ランダムpassphrase/token、QR等の配布方法、失効/再pair手順を決める |
| D-09 | Demo DB/ログ保存先 | 展示フォルダ内`Data/Showcase`か`%LOCALAPPDATA%`か選択。コピー容易性と書込/消去性のbalanceを確認 |
| D-10 | PyInstaller EDR判定 | onedirを第一候補にするが、学校貸出PCのAV/署名/allowlistが未確認。現地起動前にpackage scanが必要 |
| D-11 | 複数端末/操作者 | 初期版はPC 1台、ESP 1台、展示担当者1名を上限にする案 |
| D-12 | UIDのプライバシー/脅威 | UIDは複製可能で個人情報と結びつき得る。出席の強い本人認証として表示せず、展示データに実UIDを使わない |

**実機接続・学校展示開始前に必ず確認する判断**：D-01/02（オフライン・終了後の業務扱い）、D-04/05（UART電気仕様・実Flash容量）、D-07/08/10（貸出PC制限と配布可否）、D-09（DB配置が書込可能か）。現状のコードは暫定値で動作するが、これらは実配線・運用・配布可否を確定する条件である。

## 付録A. 根拠と検証状態

- 現在の実装仕様はローカルのコード・資料とGitHub mainの一致ファイルを調査した結果。上記4つの差分ファイルのmain/ローカル間の内容差を特定したが、ローカルGit状態は取得不能。
- Maker UNO + RC522、USB serial、CUI/Web業務フローの既存実機確認はREADME記載の2026-10-20ユーザー確認であり、今回の再検証ではない。2026-11-04時点のESP32/UNO firmwareはビルド確認済みだが、実機flash、SoftAP接続、UART波形、オフラインqueue電源断復旧は未検証。ポータブルパッケージは同一開発PCでloopback smoke済みだが、学校貸出PCのEDR/GPO/クリーン環境受入は未実施。
- ESP32-WROOM-32E電源条件・GPIO電気特性は[Espressif公式データシート](https://documentation.espressif.com/esp32-wroom-32e_esp32-wroom-32ue_datasheet_en.html)、UNO D0/D1の5V TTL UART・USB共有は[Arduino UNO公式資料](https://docs.arduino.cc/retired/boards/arduino-uno-rev3-with-long-pins/)を参照。実機DevKit・Maker UNO revisionの個別回路は別途照合する。
- ESP32 SoftAPの自己完結LANと接続手順は[Espressif SoftAP例](https://docs.espressif.com/projects/esp-techpedia/en/latest/esp-friends/get-started/case-study/wifi-examples/softap-example.html)を参照。PCの静的IPは本案では不要とする。
- Windows inbound firewall例外が標準userや管理ポリシーで制約され得ることは[Microsoft Windows Firewall rules](https://learn.microsoft.com/en-us/windows/security/operating-system-security/network-security/windows-firewall/rules)を参照。本案はPCをlistenさせず、その例外を避ける。
- PyInstaller onefileの一時展開動作は[PyInstaller公式利用資料](https://www.pyinstaller.org/en/stable/usage.html)を参照。Windows embeddable Pythonは[Python公式Windows文書](https://docs.python.org/3/using/windows.html)に記載がある。サイズ・EDR挙動は未測定であり、方式比較は受入試験で確定する。

## 17. 実装・検証状況追補（2026-11-04）

### 17.1 Repository・変更範囲

- 初回実装記録時点の作業clone baselineはGitHub `main`と同じ`df70f262590727d266e52535bcefec9bfa148c2a`。branchは`codex/rfid-v2-showcase`で、その時点の実装変更は未commit・未pushだった。後続の保存操作は最終報告で区別する。
- 元の`C:\Users\hiroki\Documents\RFID出欠管理システム`はGit管理外のフォルダとして確認され、今回変更していない。作業cloneだけを変更対象とした。
- 追加・変更した主なファイルは次の通り。
  - UNO/ESP32: `arduino/platformio.ini`、`arduino/src/main.cpp`、`esp32/platformio.ini`、`esp32/src/main.cpp`、`esp32/include/secrets.h.example`。
  - PC: `web/backend/app/config.py`、`main.py`、`routers/sessions.py`、`routers/devices.py`、`services/device_sync.py`、`services/demo_seed.py`。
  - Web/配布: `web/frontend/src/App.tsx`、`src/api/types.ts`、`web/backend/showcase_entry.py`、`showcase.spec`、`requirements-packaging.txt`、`web/build-showcase.ps1`、`Start-Showcase.cmd`、`Pair-Device.cmd`、`SHOWCASE-README.txt`、`docs/v2/学校貸出PC_導入手順.md`。
  - 自動検証: `web/backend/tests/test_device_sync.py`、`test_mock_esp32_integration.py`、`tools/test_showcase_package.py`。
- ビルド用`esp32/include/secrets.h`にはcompile確認用のダミーcredentialのみを一時作成し、ビルド後に削除した。Git管理対象にはせず、`.gitignore`で除外している。実機用credentialは未設定。

### 17.2 実装された動作と設計案からの差

- UNOの既存USB用`uno`環境（115200 bps）は維持し、`uno_esp`環境を追加した。ESP向けはSoftwareSerial D2/D3、9600 bps。既存の`READY`、`UID:<HEX>`、`ERR:`、`DIAG:`行形式を共有する。
- ESP32は`192.168.4.1/24`のSoftAPを作り、DHCPでPCを接続する。標準client数は1。UNOはGPIO16/17 UART2 9600 bps。最大本文8,192 byte、cache最大512件、未ACK queue最大2,000件。
- ESP APIは`/api/v1/health`、`status`、`events`、`events/ack`、`commands`、`cache`を実装。ESPの簡易Web UIは現状、仮受付とPC管理画面への案内を出す静的HTMLであり、状態表示・遠隔操作UIはPC React画面に実装している。
- PC APIは`/api/v1/devices/status`、`sync`、`commands`、`events`、`session/{id}/bind`。授業回終了前に読取停止、最大2,000件のevent drain、queueゼロ確認、端末session解除を行う。未同期やbinding不整合時は授業回を閉じない。
- SQLite device migrationは`PRAGMA user_version=2`を使い、`devices`、`device_sync_state`、`session_device_bindings`、`device_event_inbox`とindexを加算する。既存5業務テーブルを再作成しない。起動時に自動migrationするが、migration前の自動backup/restoreは実装していない。テストは一時DBだけで実行し、利用者のDBは開いていないため、既存運用DBへ適用する前のbackup/restore手順は別途必要。
- ESP eventは`device_id:boot_id:sequence`をevent IDとし、PCは同じevent ID・内容の再送を冪等に扱う。同IDで内容が異なる場合は拒否する。配送はat-least-onceであり、PC Inbox/attendance transactionと連続sequence ACKで重複出席・未確定データの削除を防ぐ。
- ESP queueはLittleFSへCRC32付きの全queue snapshotを書き、tmp/backup/renameで置換する。ACK watermarkはNVSへ先に保存し、その後にqueueをcompactする。設計案の単純なappend-only journalとは実装方式が異なる。flash書換頻度、電源断、最大queue時の時間は実機測定が必要。
- PC cacheはUIDと学生ID参照のみを送る。digest不一致時に更新し、ESP側で形式/件数/hashを検査する。cache更新失敗はstale状態として残し、永続eventの同期処理自体は継続する。512件と8KB本文上限があるため、実データ規模で容量確認が必要。
- PC→ESPはHTTP HMAC-SHA256（method、request-target、body SHA-256、timestamp、nonce）を検証する。nonce重複防止はESP RAM内の直近64件で、電源再起動を跨いだreplay防止ではない。pairing tokenはWindowsユーザーDPAPIで`Data/Showcase`へ保存する。通信はWPA2保護SoftAP内のHTTPでありTLSではない。
- 遠隔commandは状態操作のallowlistに限定する。`request_id`は要求に含むが、ESP側で永続的なcommand履歴を保存して重複排除する実装ではない。現コマンドは主に冪等状態変更だが、将来非冪等操作を追加する前に永続dedupを実装する。
- ESPのAP passwordとHMAC tokenはビルド時にローカル除外headerからfirmwareへ組み込む。配布firmwareからcredentialを抽出される可能性があるため、展示専用に生成し、他環境で再利用せず、展示後に失効/再生成する。Windows側DPAPI保護はpairing tokenの保存を補助するが、ESP側flashやfirmware binaryの物理解析耐性を与えない。
- 時刻はPC同期でESP clockを設定する。再起動後、再同期前はeventを`UNKNOWN`とする。現firmwareは`SYNCED`/`UNKNOWN`を生成し、設計案にある独立した`ESTIMATED`算出は未実装。UNKNOWNや終了後イベントは自動出席にしない。
- PyInstaller `onedir`でReact build、FastAPI依存、`pc/schema.sql`を同梱する。`Data/Showcase`にDB/log/pairingを置き、DBが空の場合だけダミー3学生・1授業・3履修を投入する。開発DBとはpathとmodeを分離する。`Start-Showcase.cmd`で起動し、consoleでEnterを押すと終了する。
- PC UIは接続状態・同期/queue/cache状態、同期・診断・停止/再開、展示bannerを扱う。PC APIはloopback bind。CORS origin制限だけで人のログインや認可を提供するものではなく、同一PCユーザー境界に依存する。

### 17.3 検証結果

| 判定 | 実施範囲・結果 |
|---|---|
| PASS | PC backend pytest: 17 passed、1件はStarlette/AnyIO deprecation warning。Mock ESP32実HTTP同期、migration、重複排除、ACK、session close、queue上限2,000件の回収を含む |
| PASS | 既存CUI/SQLite unittest: 19 passed |
| PASS | React TypeScript typecheckとVite production build。29 modulesをbundleし、JS 159.86 kB、CSS 3.23 kB |
| PASS | PlatformIO: UNO `uno`、`uno_esp`、ESP32 build。compileのみでupload/flashは未実施 |
| PASS | 既存PyInstaller onedir成果物を再利用し、React build、FastAPI依存、`pc/schema.sql`、起動/ペアリングスクリプト、学校PC手順を含む最終フォルダを構成。全体再buildは今回の手順書追加後には再実行していない |
| PASS | 実配布フォルダのEXE/CMD両経路と、ZIPを一時展開した後のEXE/CMD両経路でsmoke。health、React HTML/assets応答、Enter終了、再起動間のDB保持、launcher/application logを確認。DBは3 demo students、1 course、3 enrollments |
| PASS | in-app browserで管理画面を表示し、ダッシュボード上の学生3名・授業1件を確認 |
| PASS | 配布ZIP構成検査: 90 entries、実行ファイル・依存・React JS/CSS・手順書を確認。DB/log/secret/pairing fileは含めない。ZIP 26,988,309 byte、SHA-256 `B512B130B9E5B1D9E6C84BB2F10A0B10D6A7988D838D8B619BB26E7F9C1008A5` |
| PASS | Python `compileall`。起動/ペアリングCMDはUTF-8・CRLFで保存し、日本語を含む作業パスでsmokeを再通過 |
| PARTIAL | `git diff --cached --check`は原文保持の`reference-local` Markdownにある意図的な行末ハード改行を警告。実装コードと新規導入手順書に同警告なし |
| NOT RUN | クリーンWindows/貸出PCでPython・Node未導入を確認する試験、学校Defender/EDR/GPO/AppLocker、Wi-Fi自動接続、固定IP/管理者制限の現地確認 |
| NOT RUN | Maker UNO/RC522/ESP32実機、レベル変換、UART波形・欠落率、SoftAP接続、queue電源断復旧、長時間/flash寿命試験 |

同一Windows開発機のpackage smokeは配布先PCのclean-room試験を代替しない。sandbox内ではloopback socketが制約されたため、実packageのHTTP smokeとbackend integrationはloopback利用を許可した実行環境で確認した。

PlatformIOのESP32 buildは日本語を含むworkspace内でmap file出力に失敗したため、`PLATFORMIO_BUILD_DIR`をASCII-onlyのTemp pathへ変更して成功を確認した。Toolchainのどの段階が日本語pathを拒否したかは未特定であり、PlatformIO一般の制限とは断定しない。[PlatformIO build directory](https://docs.platformio.org/en/latest/projectconf/sections/platformio/options/directory/build_dir.html)、[environment variables](https://docs.platformio.org/en/latest/envvars.html)。

### 17.4 配布方式の決定・残存リスク

- 現段階の推奨は`PyInstaller onedir`。展示PCへPython/Node/pnpm/VS Codeを要求せず、onefileの一時展開とcleanupを避け、実行ファイルと別の`Data/Showcase`へDBを書ける。最終フォルダは43,158,241 byte（85 files）、EXEは16,436,026 byte。配布ZIPは26,988,309 byteで、SHA-256は17.3節に記録した。
- Python embeddable runtime同梱は、Python実行ファイルと依存フォルダの見通しがよい一方、native package/site-packages配置と更新手順を独自保守する必要がある。現時点ではonedirより配布信頼性が高い根拠がない。
- PyInstaller onefileは1ファイル配布だが、一時領域への展開が必要であり、貸出PCのTemp実行制限、起動遅延、EDR誤検知のリスクから推奨しない。ソース/開発ツール方式は必須要件に合わない。
- 展示PCではWindows Wi-Fi設定からSSIDへ手動接続し、IPv4はDHCPのまま使う。SSID接続を行うGUI自動化やPC側固定IP設定は実装していない。onefileの一時展開特性は[PyInstaller公式利用資料](https://www.pyinstaller.org/en/stable/usage.html)を参照。
- 学校PCでの実行確度はまだ比較評価できない。未署名EXEがEDR/SmartScreenにより停止する場合は、学校管理者の承認済み実行手順・allowlistを確認し、セキュリティ機能を無効化しない。
- 起動ログ、DB配置、React配信、Enter終了、同一PCでのDB保持、展開後ZIP起動は確認した。読み取り専用配置では起動時に書込失敗をコンソールへ表示する実装だが、この経路の動的試験とログ保存は未確認。別WindowsユーザーへのDPAPI pairing fileコピー、未署名実行警告、学校貸出PCは未検証。

**実装・展示開始前に確認する事項**：実物のUNO/RC522と3.3Vレベル変換回路、ESP32 board/Flash容量、貸出PC Windows/CPU architecture、学校EDR/GPOとSoftAP許可、書込可能な展開場所、オフライン仮受付・終了後eventの業務扱い、実データを展示へ持ち込まない手順。カードUIDを強い本人認証と扱わない原則は維持する。
