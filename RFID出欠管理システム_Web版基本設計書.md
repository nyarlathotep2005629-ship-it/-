# RFID出欠管理システム Web版 基本設計書

| 項目 | 内容 |
|---|---|
| 文書種別 | 基本設計書（改訂） |
| 対象 | 既存 Python CUI を維持した、Windows ローカル専用 Web 版 |
| 構成 | React + TypeScript + Vite / Python FastAPI / 既存 SQLite / Arduino Uno + RC522 |
| 作成・改訂日 | 2026-10-21 |
| 状態 | 実ソース確認済み。未確定事項は「要確認」に限定して記載 |

## 1. 目的と前提

既存の CUI、`attendance.db`、Arduino Uno + RC522 の通信仕様を変更せず、同じ SQLite ファイルを FastAPI 経由で操作するローカル Web 版を追加する。ブラウザは COM ポートにも SQLite にも直接接続しない。

本書は実プロジェクト直下の `pc/`、`arduino/`、`tests/`、`README.md`、既存の CUI 用要件定義・基本設計・詳細設計を確認した上で改訂した。旧設計書にあったレビュー用スナップショット前提、CUI/Web の共通 Windows ファイルロック、SSE 選択肢、既存 DB 制約不足の記載は採用しない。

既存の CUI 用要件定義で Web 管理画面が対象外だった点は承知している。本 Web 版は、今回確定した Web 拡張の設計であり、既存 CUI 要件を削除・緩和するものではない。

## 2. 対象範囲

### 2.1 実装対象

- 学生、授業、履修、授業回、出欠履歴の閲覧・登録・変更。
- OPEN セッションの開始、MOCK/SERIAL による受付、停止、終了。
- RFID での学生登録、COM ポート列挙、接続状態表示。
- 受付中の約 1 秒ポーリングによる受付結果・集計・RFID 実行状態の反映。
- 既存 CUI と同じ `attendance.db`、既存 `pc.database`、`pc.reader` の再利用。

### 2.2 対象外

- LAN/インターネット公開、認証・複数利用者、スマートフォン対応。
- SQLite スキーマ変更、自動移行、既存データの補正・削除。
- Arduino ファームウェア、既存 CUI の業務仕様の変更。
- CUI と Web を技術的に協調起動させるための共通プロセス間ロック。
- SSE、WebSocket、ブラウザからの COM/SQLite 直接操作。

## 3. 確認済みの既存仕様

### 3.1 データと業務ルール

| 項目 | 確認結果 | Web 版の扱い |
|---|---|---|
| DB パス | プロジェクト直下の `attendance.db` | 同一ファイルを正本として利用する |
| UID | 8/14/20 桁の大文字16進数。`normalize_uid()` が正規化 | 全入力を既存関数へ渡す |
| 出席 | `attendance` 行がある場合のみ PRESENT | 既存 `register_attendance()` を利用する |
| 未受付 | OPEN かつ出席行なしは PENDING | 行を保存しない計算状態として表示する |
| 欠席 | CLOSED かつ出席行なしは ABSENT | 終了時に欠席行を追加しない |
| OPEN 制約 | 部分一意索引 `uq_one_open_session` | 同時開始は既存 DB 制約を最終防衛線とする |
| 履修制約 | `trg_attendance_requires_enrollment` | 非履修者の出席行を DB でも拒否する |
| 履修変更 | 最初の session 開始後は既存関数が拒否 | API は 409 を返す |

`students.card_uid` の UNIQUE と UID CHECK、出席の履修トリガーは現行 `pc/schema.sql` に実装済みである。ただし既存 DB に対する `CREATE TABLE IF NOT EXISTS` は既存の CHECK 制約を追加しない。Web 導入時は自動移行を行わず、稼働 DB のスキーマと既存データを導入前に確認する（要確認事項 14）。

### 3.2 RFID/Arduino 通信

| 項目 | 現行仕様 |
|---|---|
| ボード/ライブラリ | Arduino Uno / MFRC522 1.4.12 |
| シリアル | 115200 bps、8N1、LF 終端 |
| PC→Arduino | `HELLO\n` |
| Arduino→PC | `READY`、`UID:<UID>`、`ERR:<コード>`、診断 `DIAG:` |
| UID 長 | 4/7/10 byte（文字列 8/14/20 文字） |
| Reader | `SerialReader` は READY 待機 5 秒、読取 timeout 0.1 秒 |
| MOCK | `MockReader` が手入力 UID を既存正規化へ渡す |

Arduino の現在の実機確認は `README.md` の 2026-10-20 のユーザー確認を参照する。本書作成で実機、COM、Arduino ビルドは再実行していない。

## 4. 全体構成

```text
Browser (React + TypeScript + Vite)
  └─ HTTP/JSON, 127.0.0.1 のみ、約1秒ポーリング
       └─ FastAPI
           ├─ API / Pydantic / 例外変換
           ├─ Web RFID Runtime（プロセス内排他、イベントバッファ）
           ├─ 既存 pc.database（SQLite 業務処理）
           └─ 既存 pc.reader（MOCK / pySerial）
                └─ Arduino Uno + RC522 (COMx)

既存 Python CUI ───── 同じ attendance.db を別プロセスで利用可能
```

- FastAPI は `127.0.0.1` にのみ bind し、プロダクション相当では React のビルド成果物を同一オリジンで配信する。
- API ごとに短命 SQLite 接続を作成して必ず閉じる。連続読取ワーカーはワーカー専用接続を使用する。
- 既存 `pc/database.py` への直接 SQL 追加はしない。業務更新は既存関数を経由する。
- Web 専用の実行時状態はメモリー上のみであり、DB のセッション状態とは分離する。

## 5. CUI/Web と RFID の排他方針

### 5.1 確定事項

1. **CUI と Web の同時利用は非対応かつ運用上禁止する。** 同じ DB と COM を別プロセスから同時に扱わない。
2. CUI と Web の間に共通 Windows ファイルロックは導入しない。従って、他プロセス起動を Web が技術的に検出・防止する保証はない。
3. Web バックエンド内では学生 RFID 登録と出席受付（MOCK を含む）を同時に開始できない。`RfidRuntimeController` の状態ロックで資源状態を保護し、受付開始・受付停止・セッション終了・RFID登録開始は共通の**操作用ロック**で直列化する。
4. Web バックエンドが開く COM ポートは常に最大 1 本とする。ブラウザは COM を開かない。
5. 他プロセスに COM が占有されている場合、SERIAL 開始は `SERIAL_OPEN_FAILED`（503）として画面に原因と再試行手順を示す。推測で別ポート・別 DB に切り替えない。

この禁止事項は Web 起動案内、RFID 設定画面、Web 用 README 追記箇所に明記する。今回の成果物は設計書であり、既存 `README.md` の編集は実装フェーズで行う。

### 5.2 Web 内状態

| 状態 | 意味 | COM | 次に許可する主操作 |
|---|---|---|---|
| IDLE | 未使用 | 閉じている | 登録開始、受付開始 |
| STARTING | SERIAL の READY 待機 | 最大1本 | 停止 |
| REGISTERING | 学生登録用の一回読取 | 最大1本 | 停止 |
| ATTENDING | 出席の連続読取 | SERIAL は最大1本、MOCK は0本 | 停止 |
| STOPPING | 停止・解放処理中 | 解放待ち | 状態照会のみ |
| ERROR | 接続/読取失敗後 | 必ず閉じる方向 | 再試行、状態照会 |

Runtime の排他は Web プロセス内に限る。OPEN/CLOSED、出席、履修整合性は既存 SQLite のトランザクション・制約が保持する。

## 6. セッション終了の設計

受付停止とセッション終了は別操作である。

```text
利用者が「終了」を選ぶ
  → 確認ダイアログ
  → POST runtime/stop
  → runtime が IDLE になったことを確認
  → POST sessions/{id}/close
  → CLOSED として再読込
```

- `POST /sessions/{id}/close` は Web runtime が `STARTING` / `REGISTERING` / `ATTENDING` / `STOPPING` の場合に **409 `RFID_RUNTIME_ACTIVE`** を返す。
- 停止が失敗・timeout の場合は close を送らず、OPEN のまま再試行させる。
- close が失敗した場合も OPEN のままとし、停止済みの reader を勝手に再開しない。
- 「読取停止」だけでは欠席を確定しない。OPEN のままブラウザを閉じても再開可能である。
- UI は停止・終了のリクエスト中に二重送信を無効化する。バックエンドも状態を再検査して順序を強制する。

## 7. API と画面の基本方針

### 7.1 API

- ベースパス: `/api/v1`、JSON/UTF-8、ローカル HTTP。
- `GET /health`、学生、授業、履修、セッション、履歴、RFID ポート/状態、runtime 操作を提供する。
- 受付結果コード `SUCCESS`、`ALREADY_REGISTERED`、`UNKNOWN_CARD`、`NOT_ENROLLED` は業務結果として 200 で返す。`NO_ACTIVE_SESSION` と状態矛盾は 409、入力不正は 422、COM/SQLite 一時障害は 503、保存失敗は 500 とする。
- SSE/WebSocket は使用しない。詳細 API 契約は詳細設計書に定義する。

### 7.2 画面

| 画面 | 主な役割 |
|---|---|
| ダッシュボード | OPEN セッション、受付状態、復旧案内 |
| 出席受付 | 開始/停止/終了、直近結果、集計、約1秒同期 |
| 学生管理 | 一覧、手入力、RFID 一回登録 |
| 授業管理 | 授業一覧・登録 |
| 履修管理 | 履修者の登録/解除、ロック状態表示 |
| 出欠履歴 | 回別の PRESENT/PENDING/ABSENT と集計 |
| RFID 設定 | MOCK/SERIAL、COM 選択、占有・同時利用禁止の注意 |

受付画面、または RFID 学生登録を開始した学生管理画面だけが周期ポーリングを行う。学生管理画面では登録操作の `operation_id` が完了イベントまたは復旧確認で確定するまでポーリングを継続する。その他の画面は操作直後の再読込のみ実施する。

## 8. ポーリングとイベント同期

受付画面が表示され、かつ runtime が `STARTING`、`ATTENDING`、`STOPPING` のとき、または学生管理画面で RFID 登録の `operation_id` が未完了のときだけ、約 1 秒間隔で次を取得する。

1. `GET /rfid/runtime`
2. `GET /rfid/events?server_instance_id=<最後に確認したID>&after_sequence=<最後に確定した連番>&limit=100`
3. OPEN セッションの出欠一覧・集計（イベント受信時、または定期同期時）

イベントは FastAPI の固定長リングバッファ（初期値 200 件、実装時に定数化）で保持する。各サーバー起動ごとに `server_instance_id` を発行し、単調増加 `sequence` を添える。

- クライアントは同一時点に複数の poll を送らない。前回を `AbortController` で中止し、要求番号が最新の応答だけを反映する。
- 初回以外のイベント要求には、最後に確認した `server_instance_id` を必ず付ける。ID がサーバーの現在値と異なる場合、または要求した連番が保持範囲外の場合、API は `reset_required: true` を返す。画面はカーソルを破棄し、runtime・セッション・出欠一覧・集計を全再取得する。
- ブラウザ更新ではカーソルを失っても全再取得で回復する。サーバー再起動後のイベント履歴は復元しないが、SQLite の OPEN セッションと出席記録は復元する。
- 読取イベントの順序はサーバー側 sequence を正とし、画面到着順を正としない。
- RFID 登録では `operation_id` に一致する `REGISTRATION_SUCCEEDED` または `REGISTRATION_FAILED` を確認してからポーリングを停止する。IDLE を先に検出しても、完了イベントの取得または runtime/学生一覧の全再取得による完了確認を行うまでは停止しない。

## 9. ディレクトリ基本設計

```text
RFID出欠管理システム/
├─ pc/                         # 既存 CUI、変更最小・維持
│  ├─ database.py
│  ├─ reader.py
│  ├─ main.py
│  └─ schema.sql
├─ arduino/                    # 既存ファームウェア、変更しない
├─ tests/                      # 既存 DB 回帰テスト
├─ attendance.db               # 既存の正本 DB
└─ web/                        # 新規 Web 実装
   ├─ backend/app/             # FastAPI、API、サービス、Pydantic
   ├─ backend/tests/           # 一時 DB / Fake Reader のテスト
   └─ frontend/src/            # React、画面、API client、hook
```

Web は `pc.database` と `pc.reader` を import する。CUI が依存するファイル名・CLI 起動・DB スキーマ・Arduino プロトコルを変えない。詳細なモジュール配置は詳細設計書に定義する。

## 10. 導入・移行・運用

1. CUI を終了し、`attendance.db` のバックアップを取得する。
2. 対象 DB のパス、SQLite の `foreign_keys`、テーブル/索引/トリガー、既存 UID データを読み取り確認する。
3. Web の初回起動では DB をコピー・再作成・自動移行しない。
4. MOCK と一時 SQLite で API/UI を確認する。
5. 実機では CUI を終了した状態で COM 接続、READY、読取、停止、終了を確認する。
6. 問題時は Web プロセスを停止して CUI 運用へ戻す。DB はバックアップから復旧する前に状況を確認する。

通常運用では「CUI または Web のどちらか一方だけを起動する」「終了時は停止後に close する」「OPEN が残った場合は再開または明示的に終了する」を守る。

## 11. 非機能要件

| 観点 | 設計 |
|---|---|
| 対応 OS | Windows 専用。COM 表示は pySerial の列挙結果を使う |
| 安全性 | `127.0.0.1` のみ。外部公開・認証なしを前提にし、共有 PC 以外で運用しない |
| データ保全 | 同一 DB、既存トランザクション/制約、事前バックアップ。黙った別DB化は禁止 |
| 可用性 | COM 未接続でも Web は起動可能。SERIAL 開始で明示エラー |
| 性能 | 受付中のみ約1秒同期。1イベントごとの DB 更新は既存トランザクション |
| ログ | UID 全文や個人情報を通常ログへ出さない。エラーコードと時刻を記録 |
| アクセシビリティ | 色だけに依存しない結果表示、ボタン無効化、明瞭なエラー文 |

## 12. テスト・検証方針

| 区分 | 内容 | 現時点 |
|---|---|---|
| 既存 DB 回帰 | `tests/test_database.py` | **PASS: 19件**（2026-10-21、一時 DB） |
| API | FastAPI TestClient + 一時 SQLite | 未実装 / NOT RUN |
| Reader | FakeSerial、MOCK、READY/UID/ERR、停止と排他 | 未実装 / NOT RUN |
| ポーリング | 出席受付・RFID登録、重複、逆順応答、保持切れ、サーバー再起動、完了イベント | 未実装 / NOT RUN |
| 操作競合 | 受付開始/停止/close の直列化、close中の開始拒否 | 未実装 / NOT RUN |
| 実 COM | COM open/READY/解放 | 未実装 / NOT RUN |
| Arduino | PlatformIO build、実カード読取 | 本書では NOT RUN |
| UI 受入 | 7画面、停止→終了、エラー再試行 | 未実装 / NOT RUN |

テスト数は現行 `tests/test_database.py` の `test_` メソッド 19 件を静的確認し、隔離した一時フォルダーを `TEMP`/`TMP` に指定して実行した。実 DB `attendance.db`、Arduino、COM ポートには触れていない。

## 13. 実装段階

1. Web 骨格、読み取り専用 API、既存 DB 回帰。
2. 学生/授業/履修/セッション API と一時 DB API テスト。
3. MOCK runtime、イベントカーソル、ポーリング UI。
4. SERIAL runtime、プロセス内排他、停止・close 順序。
5. RFID 学生登録、実 COM、UI・実機受入、README/起動案内の反映。

各段階で CUI の回帰、DB 互換、実DB非破壊を個別に検証する。ビルド成功を実機読取成功と扱わない。

## 14. 要確認事項

| ID | 要確認事項 | 影響 |
|---|---|---|
| Q-01 | 実運用で使用する `attendance.db` が現行 `schema.sql` の CHECK/トリガーを備えるか | 導入前の読み取り検査が必要 |
| Q-02 | Web の起動方法（開発 Vite とローカル配布版）および固定ポート | README/起動スクリプトに反映 |
| Q-03 | 実際に選ぶ COM 番号、USB 切断時の再接続手順 | RFID 設定と受入試験 |
| Q-04 | 受付中の画面を常時開く端末・利用者が単一であること | 本設計の認証なし前提 |
| Q-05 | 依存パッケージの具体的な固定バージョン | 実装開始時に Python/Node 環境と合わせて確定 |

上記は未確認の環境・導入条件であり、確定済みの CUI/Web 同時利用禁止、約1秒ポーリング、停止後 close の順序を覆すものではない。

## 15. 参照した実資産

- `要件定義.md`、`基本設計.md`、`詳細設計.md`
- `README.md`
- `pc/schema.sql`、`pc/database.py`、`pc/reader.py`、`pc/main.py`
- `arduino/platformio.ini`、`arduino/src/main.cpp`
- `tests/test_database.py`

