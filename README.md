# RFID出欠管理システム

Phase 1・Phase 2のDB基盤、Phase 3のCLI/MOCK入力、Phase 4のArduinoファームウェア、Phase 5のシリアル通信統合です。

## 対象範囲

- SQLiteスキーマ
- 学生登録・一覧取得
- 授業登録・一覧取得
- 履修登録・解除・履修者一覧
- 授業回の最小限の開始・終了処理
- 出席登録（重複受付の抑止を含む）
- 出欠一覧・出席集計
- 授業回の履歴取得
- 日本語CLIによる学生・授業・履修管理
- MOCKモードによる出席受付
- OPENセッションの起動時復旧と終了時確認
- Arduino Uno向けMFRC522 UID送信ファームウェア
- pySerialによるArduinoとのREADY/UID通信
- MOCK/SERIAL入力モードの切り替え
- UID正規化と入力検証
- DB単体テスト

## 動作確認状況

### ユーザーによる実機確認（2026-10-20）

ユーザーの確認による結果です。今回のWorkでは実機を再接続しておらず、以下を独立に再検証していません。

- Maker UNO Rev 1.1とRC522によるRFIDカードのUID読み取り
- 同一カードの連続送信防止
- PythonとArduinoのシリアル通信
- 学生登録、授業登録、履修登録
- 出席受付、受付終了、出欠履歴確認

カードUID、学生情報、運用データは公開対象に含めません。
テストコード内の名前・UIDは動作確認用のダミー値であり、実在の学生・カードを表しません。

### 未検証・既知の課題

- 複数学生を対象とした一連の実機運用
- 通信切断時の再接続、通信エラーからの復旧、Arduino再起動を含む障害復旧
- RC522モジュールの正確な型番、電源条件、信号電圧、長時間運転
- `VersionReg=0x82`。MFRC522ライブラリの既知の参照値にないため、`UNKNOWN`として扱います。セルフテスト失敗だけでハードウェア故障とは判定しません。
- `arduino/src/main.cpp`の`DIAGNOSTIC_ALLOW_SELF_TEST_FAILURE`は現在`true`で、セルフテスト失敗後の診断用バイパスが有効です。これはセルフテストPASSを意味せず、通常運用の安全性を保証しません。

出席状態は、受付中の未受付を `PENDING`、受付済みを `PRESENT`、受付終了後の未受付を `ABSENT` として、履修情報と出席記録から算出します。

## 実行環境

- Python 3.15
- SQLite（Python標準ライブラリ）
- pySerial 3.5
- Windows

## テスト実行

プロジェクトルートで次を実行します。

    .venv\Scripts\python.exe -m unittest discover -s tests -v

テストは一時DBを使用し、通常運用DBは作成・変更しません。

## CLI起動

プロジェクトルートで次を実行します。

    .venv\Scripts\python.exe pc\main.py

DBは`pc/main.py`の場所を基準に解決され、プロジェクトルートの`attendance.db`へ保存されます。

出席受付画面ではUIDを入力します。MOCK入力を終了してメニューへ戻るには`Q`を入力します。入力されたUIDは既存の`normalize_uid()`で正規化され、出席登録は`register_attendance()`へ委譲されます。

SERIALモードでは、メインメニューの「RFID接続・入力設定」からCOMポートを選択してからモードを切り替えます。ポートはカード読み取り時だけ開き、受付画面終了時または通信エラー時に閉じます。Windowsコンソールでは受付中に`Q`を押すと、シリアル受信を終了してメニューへ戻ります。

## DB設計

スキーマは pc/schema.sql にあります。

- students
- courses
- enrollments
- sessions
- attendance

各接続で外部キーを有効化し、OPENセッションは部分一意インデックスで全体1件に制限します。

### F-01/F-02のDB保護

`students.card_uid` はDB側でも、8・14・20文字かつ大文字の`0-9A-F`だけを受け付けます。`attendance`には、授業回の授業に対象学生が履修登録されていることを確認する `trg_attendance_requires_enrollment` トリガーがあります。Python側のUID正規化と履修確認も引き続き実行します。

このスキーマを新規DBへ適用する場合は、通常どおり`open_database()`で初期化できます。既存DBに対して`CREATE TABLE IF NOT EXISTS`を実行しても、既存の`students`テーブルへCHECK制約は追加されません。一方、F-02のトリガーは未作成の場合に追加されますが、既存データの妥当性確認は別途必要です。

既存DBへF-01を適用する場合は、自動移行を行わず、次の手順を前提にした個別移行が必要です。

1. DBファイルを別名へコピーしてバックアップし、コピーに対して作業する。
2. 読み取り専用で、`card_uid IS NULL`、長さが8・14・20以外、`card_uid GLOB '*[^0-9A-F]*'`に該当する行を確認する。
3. `attendance`と`enrollments`・`sessions`を照合し、未履修出席などの既存不整合を確認する。
4. 問題がないことを確認した後、外部キーを考慮して`students`テーブルを新しい定義で再構築する。再構築はバックアップ、明示的なトランザクション、失敗時のロールバックを前提とする。
5. `PRAGMA foreign_key_check`、UID重複、件数、主要な関連データを確認し、検証後にだけ運用DBへ反映する。

既存DBを自動再構築・初期化・上書きする機能は実装していません。正式な運用DBに適用する前に、バックアップからの復元確認を含む移行手順を別途作成してください。

## Arduino / PlatformIO

Arduino側のプロジェクトは`arduino`にあります。PlatformIO Coreを使用して、プロジェクトルートから次を実行します。

    pio run -d arduino

対象はArduino Framework、シリアル速度は115200bpsです。ユーザー確認に使用した実機はMaker UNO Rev 1.1とRC522です。MFRC522ライブラリは`miguelbalboa/MFRC522@1.4.12`に固定しています。書き込み・カード読取の実機結果は、上記のユーザー確認として記録しています。

### RC522初期化診断

Arduino起動時には、通常の`READY`または`ERR:RFID_INIT`に加えて、次の診断行を出力します。`DIAG:`で始まる行は既存のPython側パーサーでは警告扱いとなり、`READY`・`UID:`・`ERR:`の通信仕様を変更しません。

- `DIAG:PIN_CONFIG=SS:D10,RST:D9,SPI:D11/D12/D13`：ソフトウェア上のピン設定。
- `DIAG:SPI_START` / `DIAG:SPI_BEGIN=OK`：`SPI.begin()`の呼び出し段階。これだけでは配線正常を断定しません。
- `DIAG:RC522_VERSION=0xNN`：`PCD_Init()`後に読み取った`VersionReg`の値。
- `DIAG:RC522_VERSION_CLASS=MFRC522`：0x91または0x92。
- `DIAG:RC522_VERSION_CLASS=COMPATIBLE`：0x88。互換チップの可能性があります。
- `DIAG:RC522_VERSION_CLASS=NO_SPI_RESPONSE`：0x00または0xFF。電源、GND、SS、SPI信号、レベル変換などを疑います。
- `DIAG:RC522_VERSION_CLASS=UNKNOWN`：その他の値（例：0x82）。ライブラリのセルフテスト参照データがない可能性があり、`SELF_TEST=FAIL`だけではハードウェア故障と判定できません。
- `DIAG:SPI_READ=OK/FAIL`：VersionReg読み取りの結果。`OK`でも通信全体の正常性を保証しません。
- `DIAG:SELF_TEST=PASS/FAIL/SKIPPED`：MFRC522ライブラリのデジタルセルフテスト結果。
- `DIAG:SELF_TEST_BYPASS=DISABLED/ENABLED`：セルフテスト失敗時の扱い。現在のソースでは診断用バイパスが有効です。

`DIAGNOSTIC_ALLOW_SELF_TEST_FAILURE`は`arduino/src/main.cpp`内の診断専用設定です。現在のソースでは`true`に設定されており、セルフテスト失敗後に`PCD_Init()`を再実行し、`DIAG:RC522_REINIT=OK`を確認してからUID読み取りを試します。SPIのVersionRegが読めたがセルフテストだけ失敗する互換モジュールについて、カード読み取り可否を切り分ける目的に限る既知の診断課題です。再初期化後も`0x00`または`0xFF`なら通常処理へ進みません。

### シリアルプロトコル

ArduinoからPCへ送信する行はLF終端です。

- 初期化成功：`READY`
- 初期化失敗：`ERR:RFID_INIT`
- UID読取：`UID:XXXXXXXX...`

PCから`HELLO`とLFを受信すると、初期化成功時だけ`READY`を返信します。初期化失敗時は`ERR:RFID_INIT`を返信し、`READY`は送信しません。

UIDは4・7・10バイトに対応し、各バイトを大文字16進数2桁で出力します。例：`04A1B2C3`。

### RC522配線と電源上の注意

RC522の正確なモジュール型番は未確認です。以下は一般的なSPI接続の候補であり、実配線を確定するものではありません。

| RC522信号 | Arduino Uno候補 |
| --- | --- |
| SDA/SS | D10 |
| RST | D9 |
| MOSI | D11 |
| MISO | D12 |
| SCK | D13 |
| IRQ | 未接続 |

RC522 ICや多くのモジュールは3.3V系ですが、モジュールごとの電源入力条件・レギュレータ・ロジック許容電圧は確認が必要です。Arduino Unoの5V SPI出力をRC522へ直接接続してよいとは判断できません。RC522側のMOSI、SCK、SS、RSTなどの入力には、モジュール仕様に従ったレベル変換または電圧分圧が必要になる場合があります。MISO側も含め、購入したモジュールのデータシートと基板表示を確認してから接続してください。

ユーザー確認ではUID読取、同一カードの連続送信防止、Python-Arduino間のシリアル通信を確認しています。ただし、電源条件、信号電圧、RC522の正確な型番、通信切断時の復旧、複数学生での運用、長時間運転は未検証です。

### PC側シリアル通信

PC側は`.venv`内のpySerial 3.5を使用します。Arduinoのポートオープン後、残留入力を破棄して起動通知を待ち、必要に応じて`HELLO\n`を送信して`READY\n`を確認します。`READY`確認前のUID、64バイト超過行、ASCII以外の行、不正UID、仕様外メッセージは出席登録へ渡しません。

ユーザー確認ではCOMポート経由の`READY`応答とUID通信を確認しています。USB抜去、通信切断、再接続、Arduino自動リセット後の復旧、RC522の電源・信号電圧は未検証です。
