#include <Arduino.h>
#include <MFRC522.h>
#include <SPI.h>
#include <string.h>

namespace {

constexpr byte RFID_SS_PIN = 10;   // RC522 SDA/SS -> Uno D10
constexpr byte RFID_RST_PIN = 9;   // RC522 RST -> Uno D9
constexpr unsigned long SERIAL_BAUD = 115200;

// 通常運用ではfalseのままにする。trueはセルフテスト失敗後の
// カード読み取り可否を切り分ける診断ビルド専用。
constexpr bool DIAGNOSTIC_ALLOW_SELF_TEST_FAILURE = true;

// PICC_HaltA()後の再検出を同一カードの連続受付とみなさないための時間。
constexpr unsigned long CARD_RELEASE_TIMEOUT_MS = 1500;

// ライブラリやモジュールによって離脱検出が期待どおりに働かない場合の
// 上限。これを超えれば、同じUIDでも再送を許可する。
constexpr unsigned long SAME_UID_MAX_SUPPRESSION_MS = 30000;

constexpr byte COMMAND_BUFFER_SIZE = 16;
constexpr byte MAX_UID_SIZE = 10;

const char HEX_DIGITS[] = "0123456789ABCDEF";

MFRC522 rfid(RFID_SS_PIN, RFID_RST_PIN);

bool rfidReady = false;
char commandBuffer[COMMAND_BUFFER_SIZE];
byte commandLength = 0;
bool commandOverflow = false;

bool suppressedUidValid = false;
byte suppressedUidSize = 0;
byte suppressedUid[MAX_UID_SIZE];
unsigned long lastCardSeenAt = 0;
unsigned long lastUidSentAt = 0;

void sendLine(const __FlashStringHelper* message) {
  Serial.print(message);
  Serial.write('\n');
}

bool isSupportedUidSize(byte uidSize) {
  return uidSize == 4 || uidSize == 7 || uidSize == 10;
}

bool isSameUid(const MFRC522::Uid& uid) {
  if (!suppressedUidValid || suppressedUidSize != uid.size) {
    return false;
  }
  for (byte index = 0; index < uid.size; ++index) {
    if (suppressedUid[index] != uid.uidByte[index]) {
      return false;
    }
  }
  return true;
}

void rememberUid(const MFRC522::Uid& uid, unsigned long now) {
  suppressedUidSize = uid.size;
  for (byte index = 0; index < uid.size && index < MAX_UID_SIZE; ++index) {
    suppressedUid[index] = uid.uidByte[index];
  }
  suppressedUidValid = true;
  lastCardSeenAt = now;
  lastUidSentAt = now;
}

void sendUid(const MFRC522::Uid& uid) {
  Serial.print(F("UID:"));
  for (byte index = 0; index < uid.size; ++index) {
    const byte value = uid.uidByte[index];
    Serial.write(HEX_DIGITS[(value >> 4) & 0x0F]);
    Serial.write(HEX_DIGITS[value & 0x0F]);
  }
  Serial.write('\n');
}

void releaseSuppressedUidIfCardIsAbsent(unsigned long now) {
  if (
      suppressedUidValid
      && static_cast<unsigned long>(now - lastCardSeenAt)
             >= CARD_RELEASE_TIMEOUT_MS
  ) {
    suppressedUidValid = false;
  }
}

void handleSerialCommand(const char* command) {
  if (strcmp(command, "HELLO") != 0) {
    return;
  }

  if (rfidReady) {
    sendLine(F("READY"));
  } else {
    sendLine(F("ERR:RFID_INIT"));
  }
}

void readSerialCommands() {
  while (Serial.available() > 0) {
    const char received = static_cast<char>(Serial.read());

    if (received == '\r') {
      continue;
    }

    if (received == '\n') {
      if (!commandOverflow) {
        commandBuffer[commandLength] = '\0';
        handleSerialCommand(commandBuffer);
      }
      commandLength = 0;
      commandOverflow = false;
      continue;
    }

    if (commandLength < COMMAND_BUFFER_SIZE - 1) {
      commandBuffer[commandLength++] = received;
    } else {
      commandOverflow = true;
    }
  }
}

void printHexByte(byte value) {
  if (value < 0x10) {
    Serial.write('0');
  }
  Serial.print(value, HEX);
}

void sendVersionDiagnostic(byte version) {
  Serial.print(F("DIAG:RC522_VERSION=0x"));
  printHexByte(version);
  Serial.write('\n');

  if (version == 0x91 || version == 0x92) {
    sendLine(F("DIAG:RC522_VERSION_CLASS=MFRC522"));
  } else if (version == 0x88) {
    sendLine(F("DIAG:RC522_VERSION_CLASS=COMPATIBLE"));
  } else if (version == 0x00 || version == 0xFF) {
    sendLine(F("DIAG:RC522_VERSION_CLASS=NO_SPI_RESPONSE"));
  } else {
    sendLine(F("DIAG:RC522_VERSION_CLASS=UNKNOWN"));
  }
}

bool initializeRfid() {
  sendLine(F("DIAG:RC522_INIT_START"));
  rfid.PCD_Init();
  sendLine(F("DIAG:RC522_INIT_DONE"));

  // PCD_Init()は戻り値を持たないため、VersionRegを読み取って
  // SPI越しに応答があるかを先に確認する。
  const byte version = rfid.PCD_ReadRegister(MFRC522::VersionReg);
  sendVersionDiagnostic(version);
  if (version == 0x00 || version == 0xFF) {
    sendLine(F("DIAG:SPI_READ=FAIL"));
    sendLine(F("DIAG:SELF_TEST=SKIPPED"));
    return false;
  }
  sendLine(F("DIAG:SPI_READ=OK"));

  const bool selfTestPassed = rfid.PCD_PerformSelfTest();
  sendLine(selfTestPassed ? F("DIAG:SELF_TEST=PASS") : F("DIAG:SELF_TEST=FAIL"));
  if (selfTestPassed) {
    return true;
  }

  if (DIAGNOSTIC_ALLOW_SELF_TEST_FAILURE) {
    sendLine(F("DIAG:SELF_TEST_BYPASS=ENABLED"));

    // MFRC522 1.4.12は未知のVersionReg値でセルフテストを中断し、
    // PCD_Init()による再初期化を実行せずにfalseを返す場合がある。
    // バイパス後に通常処理へ進む前に、必ず明示的に再初期化する。
    sendLine(F("DIAG:RC522_REINIT_START"));
    rfid.PCD_Init();
    const byte reinitializedVersion =
        rfid.PCD_ReadRegister(MFRC522::VersionReg);
    Serial.print(F("DIAG:RC522_REINIT_VERSION=0x"));
    printHexByte(reinitializedVersion);
    Serial.write('\n');

    if (reinitializedVersion == 0x00 || reinitializedVersion == 0xFF) {
      sendLine(F("DIAG:RC522_REINIT=FAIL"));
      return false;
    }

    sendLine(F("DIAG:RC522_REINIT=OK"));
    return true;
  }

  sendLine(F("DIAG:SELF_TEST_BYPASS=DISABLED"));
  return false;
}

void pollRfid() {
  const unsigned long now = millis();

  if (!rfid.PICC_IsNewCardPresent()) {
    releaseSuppressedUidIfCardIsAbsent(now);
    return;
  }

  if (!rfid.PICC_ReadCardSerial()) {
    return;
  }

  const bool sameUid = isSameUid(rfid.uid);
  const bool suppressionExpired =
      static_cast<unsigned long>(now - lastUidSentAt)
      >= SAME_UID_MAX_SUPPRESSION_MS;

  if (!isSupportedUidSize(rfid.uid.size)) {
    sendLine(F("ERR:UID_LENGTH"));
    rememberUid(rfid.uid, now);
  } else if (!sameUid || suppressionExpired) {
    sendUid(rfid.uid);
    rememberUid(rfid.uid, now);
  } else {
    // 同じカードをかざし続けている間は送信しない。
    // カード離脱後の再検出、または上限時間経過後の再送は許可する。
    lastCardSeenAt = now;
  }

  rfid.PICC_HaltA();
  rfid.PCD_StopCrypto1();
}

}  // namespace

void setup() {
  Serial.begin(SERIAL_BAUD);
  sendLine(F("DIAG:PIN_CONFIG=SS:D10,RST:D9,SPI:D11/D12/D13"));
  sendLine(F("DIAG:SPI_START"));
  SPI.begin();
  sendLine(F("DIAG:SPI_BEGIN=OK"));

  rfidReady = initializeRfid();
  if (rfidReady) {
    sendLine(F("READY"));
  } else {
    sendLine(F("ERR:RFID_INIT"));
  }
}

void loop() {
  readSerialCommands();

  if (rfidReady) {
    pollRfid();
  }
}
