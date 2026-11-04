RFID出欠管理システム 展示PC向けポータブル版

■起動
1. フォルダ全体を、書き込み可能な場所（例: デスクトップまたはドキュメント）へコピーします。
2. 展示前に「Pair-Device.cmd」でESP32を一度ペアリングします。
3. 「Start-Showcase.cmd」をダブルクリックします。
4. 終了するときは、起動した黒い画面でEnterを押します。

展示PCにPython、Node.js、pnpm、VS Codeをインストールする必要はありません。
PC側Web画面は http://127.0.0.1:8000/ のみで動作し、インターネット/CDNは使いません。

■ESP32の準備と接続
ESP32のSoftAP設定値は、ファームウェアのビルド時に管理者が設定します。
SSID「RFID-Showcase」へ学校貸出PCを接続し、PCのIPv4設定は自動取得（DHCP）のままにしてください。
通常、PC側の固定IP設定やWindows Defender Firewallの受信例外は不要です。
学校のセキュリティ製品がWi-Fi接続または192.168.4.1への送信通信を制限する場合は、学校管理者へ確認してください。
展示PCではファイアウォール、ウイルス対策、学校ポリシーを無効化・変更しないでください。

「Pair-Device.cmd」を実行し、ESP32のDevice IDと64桁のtokenを入力します。
tokenは画面へエコーされず、Windows DPAPIで現在のWindowsユーザー用に暗号化して
Data\Showcase\device-pairing.jsonへ保存します。別ユーザーのWindowsアカウントで使う場合は、
そのPC/アカウント上で再度ペアリングしてください。暗号化ファイルだけを別ユーザーへコピーしても復号できません。

■データとログ
展示DB: Data\Showcase\attendance.db
ランチャーログ: Data\Showcase\Logs\launcher.log
アプリログ: Data\Showcase\Logs\application.log（最大2 MB、世代バックアップ3個）
展示用DBが空のときだけダミー学生・授業・履修を登録します。通常起動時にDBを削除・初期化しません。
このフォルダには実運用DB、実在学生データ、実カードUIDを入れないでください。

■トラブル時
- 起動しない: Data\Showcase\Logs\launcher.log と application.log を確認してください。
- 「ポート8000」エラー: 同じPC上の別アプリが使用していないか確認して終了後に再起動してください。
- ESP32へ接続できない: SSID、Device ID、pairing設定、端末の電源を確認してください。
- セキュリティソフトが実行ファイルを隔離した場合、無効化や除外登録を行わず、学校管理者へ確認してください。

■検証上の制約
未署名のWindows実行ファイルはSmartScreen、Defenderまたは学校のEDR/WDAC/AppLockerにより
警告・実行拒否される可能性があります。貸出PCでの実行可否は現地のクリーン環境試験で確認が必要です。
この資料だけでは学校PCでの実行許可、Wi-Fi接続、実機RFID通信を保証しません。
