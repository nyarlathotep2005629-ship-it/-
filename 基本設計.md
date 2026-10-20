よし、要件定義 v1.0を基準に、基本設計へ進もう。

今回はCodexにまだ実装させず、システムの内部構造と操作方法を具体化する段階だね。

特に重視するのは、DBの各テーブルがどう関係するか、カードを読み取ったときに何が起きるか、異常終了してもデータがどう守られるか、の3点。

# RFID出欠管理システム 基本設計 v0.1

基本設計・レビュー用

# RFID出欠管理システム 基本設計書

**文書バージョン：** 0.1\
**基準文書：** 要件定義書 v1.0\
**作成日：** 2026年10月20日\
**状態：** 基本設計レビュー待ち

---

## 1. 基本設計方針

### 1.1 設計目標

Arduino UnoとRFIDリーダーを使用した、ローカル出欠管理システムを構築する。

要件定義書 v1.0に記載された機能を満たしつつ、以下の方針を採用する。

- ハードウェア制御と業務処理を分離する。
- データベースは5テーブル構成を維持する。
- PCアプリは対話型CLIとして構築する。
- 通常操作でSQLを直接入力する必要はない。
- 実機未接続でも模擬UIDで検証可能にする。
- 依存関係やプログラム構造を必要以上に複雑化しない。
- 保存済みデータの整合性を優先する。

### 1.2 システム全体の役割

RFIDリーダーはカードUIDを読み取り、Arduinoを介してPCに送信する。

PC側のPythonアプリはUIDを受信し、現在の出席受付と学生情報を照合する。

有効な出席についてのみSQLiteへデータを保存する。

---

## 2. ソフトウェア構成

### 2.1 Arduino側

主要な責務は以下のとおり。

1. RC522の初期化
2. ICカードの検出
3. UIDの取得
4. 連続読み取りの抑制
5. UIDのシリアル送信
6. 通信準備状態の通知

Arduino側には学生名、授業名、出欠情報、データベースを持たせない。

### 2.2 PC側

PCアプリを機能単位で3つに分離する。

| モジュール    | 責務                  |
| -------- | ------------------- |
| main     | CLI操作、処理全体の制御       |
| database | SQLiteへの登録、参照、更新、判定 |
| reader   | UID取得（実機入力・模擬入力）    |

上記は論理的なモジュール分割を表す。詳細な関数やクラスは詳細設計で定義する。

### 2.3 処理の依存関係

- mainはreaderからUIDを取得する。
- mainはdatabaseに出席登録を要求する。
- databaseは受付状態・履修関係・重複を検証する。
- databaseは検証後にSQLiteへ保存する。
- mainはdatabaseからの結果をCLIへ表示する。

readerはSQLiteを直接操作しない。

databaseはRFIDハードウェアを直接操作しない。

---

## 3. データベース基本設計

### 3.1 使用DB

SQLiteを採用する。

初期版は単一PC・単一プロセスでの利用を想定する。

### 3.2 テーブル構成

**students：学生マスタ**

- student_id：内部識別子
- name：学生名
- card_uid：実験用カードUID

**courses：授業マスタ**

- course_id：内部識別子
- course_name：授業名

**enrollments：履修情報**

- student_id：学生への参照
- course_id：授業への参照

**sessions：出席受付**

- session_id：授業回の識別子
- course_id：対象授業
- session_number：第何回か
- started_at：受付開始日時
- closed_at：受付終了日時

**attendance：出席情報**

- session_id：対象授業回
- student_id：出席した学生
- scanned_at：出席記録日時

### 3.3 主なテーブル間の関係

- studentsとcoursesは多対多。
- 多対多の関係をenrollmentsで表現する。
- coursesとsessionsは1対多。
- sessionsとattendanceは1対多。
- studentsとattendanceは1対多。

### 3.4 一意性・参照整合性

- students.card_uidは一意とする。
- enrollmentsの主キーは(student_id, course_id)とする。
- attendanceの主キーは(session_id, student_id)とする。
- sessionsの(course_id, session_number)は一意とする。
- 外部キー制約を有効化する。
- 受付中セッションが全体で最大1件になるよう、部分一意インデックスを使用する。

### 3.5 履修者固定

対象授業にsessionsが1件も存在しない場合だけ、履修登録の追加・解除を許可する。

sessionsが1件以上存在する場合は、すべての履修変更を拒否する。

授業回や出席記録を削除する機能は初期版で提供しない。

### 3.6 欠席判定

欠席の情報をDBに直接保存しない。

履修登録されている学生を基準とし、attendanceに該当レコードが存在しない場合に判定する。

- 受付中：未受付
- 受付終了後：欠席

過去の出欠一覧についても、同じ規則を使用する。

### 3.7 日時

すべての日時はUTCで保存する。

表示時にはJST（日本標準時）へ変換する。

保存値はオフセット付きISO 8601形式に統一する。

---

## 4. CLI画面・操作設計

### 4.1 通常メニュー

起動後、基本メニューを表示する。

1. 学生管理
2. 授業管理
3. 履修管理
4. 出席受付開始
5. 出欠履歴確認
6. RFID接続・設定
7. アプリ終了

### 4.2 学生登録

学生登録の操作順序は以下とする。

1. 学生名を入力する。
2. UIDの取得方法を選択する。
3. RFIDカードのスキャン、または模擬UID入力を行う。
4. UIDの重複を確認する。
5. 登録内容を表示する。
6. 利用者が確認する。
7. SQLiteに保存する。
8. 結果を表示する。

学生IDはDB側で自動採番する。

### 4.3 授業登録

1. 授業名を入力する。
2. 登録内容を確認する。
3. coursesに保存する。
4. 授業IDを表示する。

### 4.4 履修登録

1. 授業を選択する。
2. 学生を選択する。
3. 対象授業に受付履歴がないことを確認する。
4. 重複する履修登録がないことを確認する。
5. enrollmentsに保存する。

初回受付前に限り履修解除できる。

### 4.5 出席受付

1. 授業を選択する。
2. 同時に別の受付が開いていないことを確認する。
3. 次のsession_numberを取得する。
4. sessionsに受付を作成する。
5. RFID読み取り画面を表示する。
6. カードを読み取る。
7. 学生情報を照合して出席登録する。
8. 結果を表示する。
9. 利用者の終了操作で受付を閉じる。

### 4.6 履歴確認

授業と授業回を選択して、以下を表示する。

- 授業名
- 授業回数
- 受付開始・終了日時
- 履修者数
- 出席者数
- 未受付者数または欠席者数
- 学生別の出欠状態
- 出席時刻

---

## 5. 出席受付の状態設計

### 5.1 状態

受付の状態は次の2種類とする。

**OPEN**

- started_atに日時が存在する。
- closed_atはNULL。
- 出席登録を受け付ける。

**CLOSED**

- started_atとclosed_atに日時が存在する。
- 出席登録を受け付けない。

未作成状態はsessionsテーブルにレコードを持たない。

### 5.2 状態遷移

未作成 → OPEN → CLOSED

CLOSEDからOPENへの遷移は禁止する。

### 5.3 異常終了時

アプリの起動時にOPEN状態のセッションを検索する。

存在する場合、以下の選択画面を表示する。

- 受付を再開
- 受付を終了

再開は新規セッションの作成を意味せず、既存のsession_idを引き継ぐ。

再開時に過去の出席データを変更しない。

復旧前には新しい受付を開始させない。

---

## 6. 出席登録処理

### 6.1 入力

カードUIDを英大文字の16進文字列として受け取る。

### 6.2 検証

- UIDの形式が正しいか。
- 受付がOPENか。
- UIDに対応する学生が存在するか。
- 学生が対象授業を履修しているか。
- 同じ授業回ですでに登録されていないか。

### 6.3 保存

各検証結果に問題がない場合のみattendanceにレコードを追加する。

この処理はトランザクション内で行う。

### 6.4 戻り値

処理結果は次のいずれかとする。

| 結果                 | 意味           |
| ------------------ | ------------ |
| SUCCESS            | 出席登録成功       |
| ALREADY_REGISTERED | すでに出席済み      |
| UNKNOWN_CARD       | 未登録カード       |
| NOT_ENROLLED       | 対象授業の履修者ではない |
| NO_ACTIVE_SESSION  | 受付なし         |
| INVALID_UID        | UID形式が不正     |
| STORAGE_ERROR      | DB処理失敗       |

PCのCLIは結果に応じた日本語メッセージを表示する。

---

## 7. 通信基本設計

### 7.1 物理通信

Arduino UnoとPCをUSBで接続する。

PC側はpySerialを使用する。

### 7.2 通信形式案

- 通信速度：115200 bps
- データ形式：8N1
- 文字コード：ASCII
- メッセージ区切り：LF（改行）
- UID表記：英大文字の16進数

### 7.3 メッセージ

**READY**

Arduino側の初期化が完了したことを通知する。

**UID**

カードUIDをPCに通知する。

UIDの部分は固定長とせず、実際のカードUIDのバイト数に対応する。

**ERR**

Arduino側のエラーを通知する。

具体的なエラーコードは詳細設計で定義する。

### 7.4 通信開始

1. PCがシリアルポートを開く。
2. Arduinoの初期化完了を待つ。
3. ArduinoからREADYを受信する。
4. PCはUIDメッセージを受け付ける。

Unoがシリアルポート接続によって再起動する場合を考慮し、準備完了までタイムアウトを設ける。

### 7.5 通信切断時

- PC側にエラーを表示する。
- 受付中セッションと保存済みデータを維持する。
- 再接続後、UID読み取りを再開可能とする。
- 未保存の読み取りデータについては再スキャンで対応する。

初期版ではメッセージごとの受信確認・再送制御は実装しない。

---

## 8. 入力モード

PCアプリはUID取得方法として、以下の2種類を持つ。

### 8.1 SERIALモード

Arduinoから送信されたUIDを受け取る。

学生登録と出席受付のどちらにも使用できる。

### 8.2 MOCKモード

CLIから仮のUIDを入力する。

学生登録、履修確認、出席登録のテストに使用する。

### 8.3 共通処理

SERIALとMOCKは、UID取得処理のみが異なる。

UIDの正規化、学生照合、出席登録、DB保存は共通化する。

---

## 9. エラー・異常終了処理

### 9.1 DBエラー

DB処理失敗時にはトランザクションをロールバックし、CLIにエラーを表示する。

### 9.2 通信エラー

シリアルポートの接続失敗、切断、タイムアウトを検出する。

通信エラーは出席登録成功として扱わない。

### 9.3 アプリの通常終了

OPEN状態の受付が存在する場合は警告し、受付を終了するか、未終了のままアプリを終了するかを選べるものとする。

未終了のまま終了した場合は、次回起動時に復旧処理を行う。

### 9.4 アプリの異常終了

保存済みの出席情報を保持する。

再起動時にOPEN状態の受付を検出して復旧を促す。

### 9.5 データ削除

Ver.1.0では、通常DBを初期化する機能や、出席情報を削除する機能を提供しない。

自動テストでは通常DBとは別の一時DBを使用する。

---

## 10. 開発時の構成案

Arduino側にはPlatformIOプロジェクトを作成する。

PC側は、CLI、DB操作、UID入力を分離する。

テストでは、DB操作・出欠判定・受付状態の処理をハードウェアから独立して検証する。

初期版では、Webサーバー、ORM、DIコンテナ、大規模なフレームワークを使用しない。

## 11. 基本設計の検証条件

- 5テーブルの関係が成立すること。
- SQLite制約により重複データが拒否されること。
- 受付中セッションを2つ作成できないこと。
- 受付状態に応じて未受付・欠席が正しく切り替わること。
- 同一学生の二重出席を防止できること。
- 異常終了後の復旧が可能な設計であること。
- SERIALとMOCKで出席判定処理を共有できること。
- RC522の詳細仕様を確定する前でもPC側の開発を進められること。

---

**基本設計 v0.1 — レビュー待ち**

本書は要件定義 v1.0を具体化した基本設計案である。SQL全文、関数シグネチャ、詳細な例外処理、ハードウェア接続は詳細設計で確定する。

## 1. ER図

基本設計の中心になる5テーブルの関係を図にすると、こうなる。

\#chatgpt-mermaid-\_r_3kk\_{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;font-size:14px;fill:rgb(255, 255, 255);}@keyframes edge-animation-frame{from{stroke-dashoffset:0;}}@keyframes dash{to{stroke-dashoffset:0;}}#chatgpt-mermaid-\_r_3kk\_ .edge-animation-slow{stroke-dasharray:9,5!important;stroke-dashoffset:900;animation:dash 50s linear infinite;stroke-linecap:round;}#chatgpt-mermaid-\_r_3kk\_ .edge-animation-fast{stroke-dasharray:9,5!important;stroke-dashoffset:900;animation:dash 20s linear infinite;stroke-linecap:round;}#chatgpt-mermaid-\_r_3kk\_ .error-icon{fill:rgba(255, 255, 255, 0.03);}#chatgpt-mermaid-\_r_3kk\_ .error-text{fill:rgb(255, 255, 255);stroke:rgb(255, 255, 255);}#chatgpt-mermaid-\_r_3kk\_ .edge-thickness-normal{stroke-width:1px;}#chatgpt-mermaid-\_r_3kk\_ .edge-thickness-thick{stroke-width:3.5px;}#chatgpt-mermaid-\_r_3kk\_ .edge-pattern-solid{stroke-dasharray:0;}#chatgpt-mermaid-\_r_3kk\_ .edge-thickness-invisible{stroke-width:0;fill:none;}#chatgpt-mermaid-\_r_3kk\_ .edge-pattern-dashed{stroke-dasharray:3;}#chatgpt-mermaid-\_r_3kk\_ .edge-pattern-dotted{stroke-dasharray:2;}#chatgpt-mermaid-\_r_3kk\_ .marker{fill:rgba(255, 255, 255, 0.498);stroke:rgba(255, 255, 255, 0.498);}#chatgpt-mermaid-\_r_3kk\_ .marker.cross{stroke:rgba(255, 255, 255, 0.498);}#chatgpt-mermaid-\_r_3kk\_ svg{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;font-size:14px;}#chatgpt-mermaid-\_r_3kk\_ p{margin:0;}#chatgpt-mermaid-\_r_3kk\_ .entityBox{fill:rgb(26, 40, 61);stroke:rgb(31, 78, 148);}#chatgpt-mermaid-\_r_3kk\_ .relationshipLabelBox{fill:rgba(255, 255, 255, 0.03);opacity:0.7;background-color:rgba(255, 255, 255, 0.03);}#chatgpt-mermaid-\_r_3kk\_ .relationshipLabelBox rect{opacity:0.5;}#chatgpt-mermaid-\_r_3kk\_ .labelBkg{background-color:rgba(255, 255, 255, 0.5);}#chatgpt-mermaid-\_r_3kk\_ .edgeLabel{background-color:rgb(24, 24, 24);}#chatgpt-mermaid-\_r_3kk\_ .edgeLabel .label rect{fill:rgb(24, 24, 24);}#chatgpt-mermaid-\_r_3kk\_ .edgeLabel .label text{fill:rgb(255, 255, 255);}#chatgpt-mermaid-\_r_3kk\_ .edgeLabel .label{fill:rgb(31, 78, 148);font-size:14px;}#chatgpt-mermaid-\_r_3kk\_ .label{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:rgb(255, 255, 255);}#chatgpt-mermaid-\_r_3kk\_ .edge-pattern-dashed{stroke-dasharray:8,8;}#chatgpt-mermaid-\_r_3kk\_ .node rect,#chatgpt-mermaid-\_r_3kk\_ .node circle,#chatgpt-mermaid-\_r_3kk\_ .node ellipse,#chatgpt-mermaid-\_r_3kk\_ .node polygon{fill:rgb(26, 40, 61);stroke:rgb(31, 78, 148);stroke-width:1px;}#chatgpt-mermaid-\_r_3kk\_ .relationshipLine{stroke:rgba(255, 255, 255, 0.498);stroke-width:1px;fill:none;}#chatgpt-mermaid-\_r_3kk\_ .marker{fill:none!important;stroke:rgba(255, 255, 255, 0.498)!important;stroke-width:1;}#chatgpt-mermaid-\_r_3kk\_ [data-look=neo].labelBkg{background-color:rgba(255, 255, 255, 0.5);}#chatgpt-mermaid-\_r_3kk\_ .node .neo-node{stroke:rgb(31, 78, 148);}#chatgpt-mermaid-\_r_3kk\_ [data-look="neo"].node rect,#chatgpt-mermaid-\_r_3kk\_ [data-look="neo"].cluster rect,#chatgpt-mermaid-\_r_3kk\_ [data-look="neo"].node polygon{stroke:url(#chatgpt-mermaid-\_r_3kk\_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_3kk\_ [data-look="neo"].swimlane.cluster rect{filter:none;}#chatgpt-mermaid-\_r_3kk\_ [data-look="neo"].node path{stroke:url(#chatgpt-mermaid-\_r_3kk\_-gradient);stroke-width:1px;}#chatgpt-mermaid-\_r_3kk\_ [data-look="neo"].node .outer-path{filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_3kk\_ [data-look="neo"].node .neo-line path{stroke:rgb(31, 78, 148);filter:none;}#chatgpt-mermaid-\_r_3kk\_ [data-look="neo"].node circle{stroke:url(#chatgpt-mermaid-\_r_3kk\_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_3kk\_ [data-look="neo"].node circle .state-start{fill:#000000;}#chatgpt-mermaid-\_r_3kk\_ [data-look="neo"].icon-shape .icon{fill:url(#chatgpt-mermaid-\_r_3kk\_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_3kk\_ [data-look="neo"].icon-shape .icon-neo path{stroke:url(#chatgpt-mermaid-\_r_3kk\_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_3kk\_ :root{--mermaid-font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;}studentsINTEGERstudent_idPKTEXTnameTEXTcard_uidUKenrollmentsINTEGERstudent_idPK,FKINTEGERcourse_idPK,FKcoursesINTEGERcourse_idPKTEXTcourse_namesessionsINTEGERsession_idPKINTEGERcourse_idFKINTEGERsession_numberTEXTstarted_atTEXTclosed_atattendanceINTEGERsession_idPK,FKINTEGERstudent_idPK,FKTEXTscanned_atregistersincludeshascontainsrecords

ここで重要なのは、`attendance` は `sessions` と `students` に対応するレコードを参照する一方、その学生が該当授業を履修しているかはPython側で検証するということ。

## 2. 受付状態の遷移図

\#chatgpt-mermaid-\_r_3kt\_{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;font-size:14px;fill:rgb(255, 255, 255);}@keyframes edge-animation-frame{from{stroke-dashoffset:0;}}@keyframes dash{to{stroke-dashoffset:0;}}#chatgpt-mermaid-\_r_3kt\_ .edge-animation-slow{stroke-dasharray:9,5!important;stroke-dashoffset:900;animation:dash 50s linear infinite;stroke-linecap:round;}#chatgpt-mermaid-\_r_3kt\_ .edge-animation-fast{stroke-dasharray:9,5!important;stroke-dashoffset:900;animation:dash 20s linear infinite;stroke-linecap:round;}#chatgpt-mermaid-\_r_3kt\_ .error-icon{fill:rgba(255, 255, 255, 0.03);}#chatgpt-mermaid-\_r_3kt\_ .error-text{fill:rgb(255, 255, 255);stroke:rgb(255, 255, 255);}#chatgpt-mermaid-\_r_3kt\_ .edge-thickness-normal{stroke-width:1px;}#chatgpt-mermaid-\_r_3kt\_ .edge-thickness-thick{stroke-width:3.5px;}#chatgpt-mermaid-\_r_3kt\_ .edge-pattern-solid{stroke-dasharray:0;}#chatgpt-mermaid-\_r_3kt\_ .edge-thickness-invisible{stroke-width:0;fill:none;}#chatgpt-mermaid-\_r_3kt\_ .edge-pattern-dashed{stroke-dasharray:3;}#chatgpt-mermaid-\_r_3kt\_ .edge-pattern-dotted{stroke-dasharray:2;}#chatgpt-mermaid-\_r_3kt\_ .marker{fill:rgba(255, 255, 255, 0.498);stroke:rgba(255, 255, 255, 0.498);}#chatgpt-mermaid-\_r_3kt\_ .marker.cross{stroke:rgba(255, 255, 255, 0.498);}#chatgpt-mermaid-\_r_3kt\_ svg{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;font-size:14px;}#chatgpt-mermaid-\_r_3kt\_ p{margin:0;}#chatgpt-mermaid-\_r_3kt\_ .label{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:rgb(255, 255, 255);}#chatgpt-mermaid-\_r_3kt\_ .cluster-label text{fill:rgb(255, 255, 255);}#chatgpt-mermaid-\_r_3kt\_ .cluster-label span{color:rgb(255, 255, 255);}#chatgpt-mermaid-\_r_3kt\_ .cluster-label span p{background-color:transparent;}#chatgpt-mermaid-\_r_3kt\_ .label text,#chatgpt-mermaid-\_r_3kt\_ span{fill:rgb(255, 255, 255);color:rgb(255, 255, 255);}#chatgpt-mermaid-\_r_3kt\_ .node rect,#chatgpt-mermaid-\_r_3kt\_ .node circle,#chatgpt-mermaid-\_r_3kt\_ .node ellipse,#chatgpt-mermaid-\_r_3kt\_ .node polygon,#chatgpt-mermaid-\_r_3kt\_ .node path{fill:rgb(26, 40, 61);stroke:rgb(31, 78, 148);stroke-width:1px;}#chatgpt-mermaid-\_r_3kt\_ .rough-node .label text,#chatgpt-mermaid-\_r_3kt\_ .node .label text,#chatgpt-mermaid-\_r_3kt\_ .image-shape .label,#chatgpt-mermaid-\_r_3kt\_ .icon-shape .label{text-anchor:middle;}#chatgpt-mermaid-\_r_3kt\_ .node .katex path{fill:#000;stroke:#000;stroke-width:1px;}#chatgpt-mermaid-\_r_3kt\_ .rough-node .label,#chatgpt-mermaid-\_r_3kt\_ .node .label,#chatgpt-mermaid-\_r_3kt\_ .image-shape .label,#chatgpt-mermaid-\_r_3kt\_ .icon-shape .label{text-align:center;}#chatgpt-mermaid-\_r_3kt\_ .node.clickable{cursor:pointer;}#chatgpt-mermaid-\_r_3kt\_ .root .anchor path{fill:rgba(255, 255, 255, 0.498)!important;stroke-width:0;stroke:rgba(255, 255, 255, 0.498);}#chatgpt-mermaid-\_r_3kt\_ .arrowheadPath{fill:rgba(255, 255, 255, 0.498);}#chatgpt-mermaid-\_r_3kt\_ .edgePath .path{stroke:rgba(255, 255, 255, 0.498);stroke-width:1px;}#chatgpt-mermaid-\_r_3kt\_ .flowchart-link{stroke:rgba(255, 255, 255, 0.498);fill:none;}#chatgpt-mermaid-\_r_3kt\_ .edgeLabel{background-color:rgb(24, 24, 24);text-align:center;}#chatgpt-mermaid-\_r_3kt\_ .edgeLabel p{background-color:rgb(24, 24, 24);}#chatgpt-mermaid-\_r_3kt\_ .edgeLabel rect{opacity:0.5;background-color:rgb(24, 24, 24);fill:rgb(24, 24, 24);}#chatgpt-mermaid-\_r_3kt\_ .labelBkg{background-color:rgba(24, 24, 24, 0.5);}#chatgpt-mermaid-\_r_3kt\_ .cluster rect{fill:rgba(255, 255, 255, 0.03);stroke:rgba(255, 255, 255, 0.082);stroke-width:1px;}#chatgpt-mermaid-\_r_3kt\_ .cluster text{fill:rgb(255, 255, 255);}#chatgpt-mermaid-\_r_3kt\_ .cluster span{color:rgb(255, 255, 255);}#chatgpt-mermaid-\_r_3kt\_ div.mermaidTooltip{position:absolute;text-align:center;max-width:200px;padding:2px;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;font-size:12px;background:rgba(255, 255, 255, 0.03);border:1px solid rgba(255, 255, 255, 0.082);border-radius:2px;pointer-events:none;z-index:100;}#chatgpt-mermaid-\_r_3kt\_ .flowchartTitleText{text-anchor:middle;font-size:18px;fill:rgb(255, 255, 255);}#chatgpt-mermaid-\_r_3kt\_ rect.text{fill:none;stroke-width:0;}#chatgpt-mermaid-\_r_3kt\_ .icon-shape,#chatgpt-mermaid-\_r_3kt\_ .image-shape{background-color:rgb(24, 24, 24);text-align:center;}#chatgpt-mermaid-\_r_3kt\_ .icon-shape p,#chatgpt-mermaid-\_r_3kt\_ .image-shape p{background-color:rgb(24, 24, 24);padding:2px;}#chatgpt-mermaid-\_r_3kt\_ .icon-shape .label rect,#chatgpt-mermaid-\_r_3kt\_ .image-shape .label rect{opacity:0.5;background-color:rgb(24, 24, 24);fill:rgb(24, 24, 24);}#chatgpt-mermaid-\_r_3kt\_ .label-icon{display:inline-block;height:1em;overflow:visible;vertical-align:-0.125em;}#chatgpt-mermaid-\_r_3kt\_ .node .label-icon path{fill:currentColor;stroke:revert;stroke-width:revert;}#chatgpt-mermaid-\_r_3kt\_ .node .neo-node{stroke:rgb(31, 78, 148);}#chatgpt-mermaid-\_r_3kt\_ [data-look="neo"].node rect,#chatgpt-mermaid-\_r_3kt\_ [data-look="neo"].cluster rect,#chatgpt-mermaid-\_r_3kt\_ [data-look="neo"].node polygon{stroke:url(#chatgpt-mermaid-\_r_3kt\_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_3kt\_ [data-look="neo"].swimlane.cluster rect{filter:none;}#chatgpt-mermaid-\_r_3kt\_ [data-look="neo"].node path{stroke:url(#chatgpt-mermaid-\_r_3kt\_-gradient);stroke-width:1px;}#chatgpt-mermaid-\_r_3kt\_ [data-look="neo"].node .outer-path{filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_3kt\_ [data-look="neo"].node .neo-line path{stroke:rgb(31, 78, 148);filter:none;}#chatgpt-mermaid-\_r_3kt\_ [data-look="neo"].node circle{stroke:url(#chatgpt-mermaid-\_r_3kt\_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_3kt\_ [data-look="neo"].node circle .state-start{fill:#000000;}#chatgpt-mermaid-\_r_3kt\_ [data-look="neo"].icon-shape .icon{fill:url(#chatgpt-mermaid-\_r_3kt\_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_3kt\_ [data-look="neo"].icon-shape .icon-neo path{stroke:url(#chatgpt-mermaid-\_r_3kt\_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_3kt\_ .node text{font-size:14px;font-weight:600;letter-spacing:normal;fill:rgb(153, 206, 255);}#chatgpt-mermaid-\_r_3kt\_ .edgeLabels text{font-size:13px;font-weight:600;letter-spacing:-0.08px;fill:rgb(153, 206, 255);}#chatgpt-mermaid-\_r_3kt\_ .node tspan[font-weight="normal"],#chatgpt-mermaid-\_r_3kt\_ .edgeLabels tspan[font-weight="normal"]{font-weight:600;}#chatgpt-mermaid-\_r_3kt\_ .edgeLabel .label rect{opacity:1;rx:13px;ry:13px;fill:rgb(0, 14, 26);stroke:rgb(26, 62, 95);stroke-width:1px;}#chatgpt-mermaid-\_r_3kt\_ .node rect,#chatgpt-mermaid-\_r_3kt\_ .node circle,#chatgpt-mermaid-\_r_3kt\_ .node ellipse,#chatgpt-mermaid-\_r_3kt\_ .node polygon,#chatgpt-mermaid-\_r_3kt\_ .node path{fill:rgb(0, 40, 77);stroke:rgba(255, 255, 255, 0.1);stroke-width:1px;}#chatgpt-mermaid-\_r_3kt\_ .node rect{rx:16px;ry:16px;}#chatgpt-mermaid-\_r_3kt\_ .node.mermaid-decision .label-container{fill:rgb(0, 14, 26);stroke:rgb(26, 62, 95);stroke-dasharray:2,2;}#chatgpt-mermaid-\_r_3kt\_ .edgePaths .flowchart-link{stroke:rgba(255, 255, 255, 0.498);stroke-width:1px;stroke-linecap:round;stroke-linejoin:round;}#chatgpt-mermaid-\_r_3kt\_ .marker{fill:rgba(255, 255, 255, 0.498);stroke:rgba(255, 255, 255, 0.498);}#chatgpt-mermaid-\_r_3kt\_ :root{--mermaid-font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;}未作成OPEN：受付中CLOSED：終了次回起動時に復旧判定受付開始・DB保存受付終了アプリ停止再開終了

再開は `sessions` に新規レコードを作るのではなく、既存セッションで受付処理を続行するだけ。

## 3. SQL制約の事前検証

基本設計の重要部分については、こちらで実際にSQLiteのインメモリDBを使い、試験用スキーマで動作を確認した。

受付中のセッションを2件作成すると、一意制約で拒否される

受付中は出席記録のない学生が「未受付」と判定される

受付終了後は同じ学生が「欠席」と判定される

受付終了後は次の授業回を作成できる

SQLiteの制約とSQL問い合わせに対する限定的な実行検証。完成予定アプリの動作検証やArduino実機試験ではない。

特に「同時に受付中の授業回は最大1件」という制約は、次のSQLで実現できることを確認した。

```

CREATE UNIQUE INDEX uq_one_open_session
ON sessions ((1))
WHERE closed_at IS NULL;

```

ここでは、受付中のレコードだけに固定値 `1` の一意性を要求している。通常の `closed_at` カラムにUNIQUEを設定するのとは異なり、NULLが複数許可される問題を回避できる。

## 4. 次の工程で扱う内容

基本設計でシステムの大枠が決まったので、詳細設計では次を具体化する。

| 項目      | 詳細設計で作成するもの         |
| ------- | ------------------- |
| データベース  | 完全なSQLスキーマ、制約、主要クエリ |
| Python  | モジュール・関数仕様、引数、戻り値   |
| CLI     | メニュー遷移、入力・表示仕様      |
| 出席処理    | トランザクションの開始・終了条件    |
| RFID通信  | メッセージ解析、タイムアウト、再接続  |
| Arduino | 初期化・読み取り・送信処理の詳細    |
| テスト     | 正常系・異常系のテストケース      |

現段階では基本設計 v0.1としてレビューに回せる状態だと考えている。大きな仕様変更がなければ、この基本設計を承認してから詳細設計へ進む流れにしよう。

今回はCodexに渡す前の設計なので、次の工程でも実装コードを大量生成するのではなく、まずSQLと各関数の仕様を固める。