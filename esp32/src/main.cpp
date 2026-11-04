#include <Arduino.h>
#include <ArduinoJson.h>
#include <LittleFS.h>
#include <Preferences.h>
#include <WebServer.h>
#include <WiFi.h>
#include <mbedtls/md.h>
#include <stdlib.h>
#include <sys/time.h>
#include <time.h>
#include <vector>
#include "secrets.h"

namespace {
constexpr int UNO_RX = 16;
constexpr int UNO_TX = 17;
constexpr uint32_t UART_BAUD = 9600;
constexpr size_t MAX_LINE = 64;
constexpr size_t MAX_QUEUE = 2000;
constexpr size_t MAX_BODY = 8192;
HardwareSerial uno(2);
WebServer server(80);
Preferences prefs;
String bootId;
String sessionGeneration;
String sessionId;
String uartState = "WAITING";
String lastDiagnostic = "";
String lineBuffer;
bool overflowLine = false;
bool readingEnabled = false;
bool unoReady = false;
bool storageReady = false;
bool timeSynced = false;
uint32_t sequenceNo = 0;
uint32_t ackedSequence = 0;
uint32_t cacheVersion = 0;
String cacheDigest;
std::vector<String> cacheUids;
std::vector<String> queueLines;
std::vector<String> seenNonces;
uint32_t crc32(const uint8_t* data, size_t length) {
  uint32_t crc = 0xffffffff;
  for (size_t i=0;i<length;i++) { crc ^= data[i]; for (int b=0;b<8;b++) crc = (crc >> 1) ^ (0xedb88320 & (-(int)(crc & 1))); }
  return ~crc;
}
String sha256Hex(const uint8_t* data, size_t length) {
  uint8_t out[32]; mbedtls_md(mbedtls_md_info_from_type(MBEDTLS_MD_SHA256), data, length, out);
  char hex[65]; for (int i=0;i<32;i++) sprintf(hex+i*2,"%02x",out[i]); hex[64]=0; return String(hex);
}
String hmacHex(const String& message) {
  uint8_t out[32]; const auto* info=mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
  mbedtls_md_hmac(info,(const uint8_t*)DEVICE_TOKEN,strlen(DEVICE_TOKEN),(const uint8_t*)message.c_str(),message.length(),out);
  char hex[65]; for (int i=0;i<32;i++) sprintf(hex+i*2,"%02x",out[i]); hex[64]=0; return String(hex);
}
bool secureEquals(const String& a,const String& b) { if(a.length()!=b.length()) return false; uint8_t v=0; for(size_t i=0;i<a.length();i++) v|=a[i]^b[i]; return v==0; }
bool authorized() {
  if(server.header("X-Device-Id")!=DEVICE_ID) return false;
  String timestamp=server.header("X-Timestamp"),nonce=server.header("X-Nonce");
  if(timestamp.length()<13||timestamp.length()>16||nonce.length()!=32) return false;
  for(size_t i=0;i<nonce.length();i++)if(!isHexadecimalDigit(nonce[i]))return false;
  for(const auto& seen:seenNonces)if(secureEquals(seen,nonce))return false;
  String body=server.arg("plain"); String digest=sha256Hex((const uint8_t*)body.c_str(),body.length());
  String method;
  switch (server.method()) {
    case HTTP_GET: method="GET"; break;
    case HTTP_POST: method="POST"; break;
    case HTTP_PUT: method="PUT"; break;
    case HTTP_DELETE: method="DELETE"; break;
    default: return false;
  }
  String target=server.uri();
  if(server.method()==HTTP_GET&&server.args()>0){target+="?";for(int i=0;i<server.args();i++){if(i)target+="&";target+=server.argName(i)+"="+server.arg(i);}}
  String message=method+"\n"+target+"\n"+digest+"\n"+timestamp+"\n"+nonce;
  if(!secureEquals(server.header("X-Signature"),hmacHex(message)))return false;
  if(timeSynced){uint64_t requestTime=strtoull(timestamp.c_str(),nullptr,10);uint64_t now=(uint64_t)time(nullptr)*1000ULL;if(requestTime>now+60000ULL||now>requestTime+60000ULL)return false;}
  if(seenNonces.size()>=64)seenNonces.erase(seenNonces.begin());
  seenNonces.push_back(nonce);
  return true;
}
bool writeQueue() {
  File f=LittleFS.open("/queue.tmp","w"); if(!f) return false;
  for(const auto& item:queueLines) { String row=item+"\t"+String(crc32((const uint8_t*)item.c_str(),item.length()),HEX)+"\n"; if(f.print(row)!=row.length()){f.close();return false;} }
  f.flush(); f.close();
  LittleFS.remove("/queue.bak");
  bool hadOld=LittleFS.exists("/queue.jsonl");
  if(hadOld && !LittleFS.rename("/queue.jsonl","/queue.bak")) return false;
  if(!LittleFS.rename("/queue.tmp","/queue.jsonl")) { if(hadOld) LittleFS.rename("/queue.bak","/queue.jsonl"); return false; }
  LittleFS.remove("/queue.bak"); return true;
}
bool loadQueue() {
  if(!LittleFS.exists("/queue.jsonl") && LittleFS.exists("/queue.bak")) LittleFS.rename("/queue.bak","/queue.jsonl");
  queueLines.clear(); File f=LittleFS.open("/queue.jsonl","r"); if(!f) return true;
  while(f.available()) {
    String row=f.readStringUntil('\n'); int tab=row.lastIndexOf('\t'); if(tab<0) { lastDiagnostic="QUEUE_CRC_FORMAT"; f.close(); return false; }
    String body=row.substring(0,tab), expected=row.substring(tab+1); String actual=String(crc32((const uint8_t*)body.c_str(),body.length()),HEX);
    if(!secureEquals(actual,expected) || queueLines.size()>=MAX_QUEUE) { lastDiagnostic="QUEUE_CRC_OR_CAPACITY"; f.close(); return false; }
    queueLines.push_back(body);
  }
  f.close();
  if(!queueLines.empty()) {
    JsonDocument d;
    if(deserializeJson(d,queueLines.back()) || !d["sequence"].is<uint32_t>() || !d["boot_id"].is<const char*>()) {
      lastDiagnostic="QUEUE_RECORD_INVALID"; return false;
    }
    sequenceNo=d["sequence"].as<uint32_t>(); bootId=d["boot_id"].as<String>();
    for(auto it=queueLines.begin();it!=queueLines.end();) {
      JsonDocument item;
      if(deserializeJson(item,*it) || !item["sequence"].is<uint32_t>()) { lastDiagnostic="QUEUE_RECORD_INVALID"; return false; }
      if(item["sequence"].as<uint32_t>()<=ackedSequence) it=queueLines.erase(it); else ++it;
    }
  }
  return true;
}
bool persistCache(const std::vector<String>& entries,uint32_t version,const String& digest) {
  JsonDocument d;d["version"]=version;d["sha256"]=digest;JsonArray a=d["entries"].to<JsonArray>();for(const auto& uid:entries)a.add(uid);
  String raw;serializeJson(d,raw);File f=LittleFS.open("/cache.tmp","w");if(!f)return false;
  if(f.print(raw)!=raw.length()){f.close();return false;}f.flush();f.close();
  LittleFS.remove("/cache.bak");bool old=LittleFS.exists("/cache.json");
  if(old&&!LittleFS.rename("/cache.json","/cache.bak"))return false;
  if(!LittleFS.rename("/cache.tmp","/cache.json")){if(old)LittleFS.rename("/cache.bak","/cache.json");return false;}
  LittleFS.remove("/cache.bak");return true;
}
bool loadCache(){
  if(!LittleFS.exists("/cache.json")&&LittleFS.exists("/cache.bak"))LittleFS.rename("/cache.bak","/cache.json");
  File f=LittleFS.open("/cache.json","r");if(!f)return true;
  JsonDocument d;auto err=deserializeJson(d,f);f.close();if(err||!d["entries"].is<JsonArray>()||!d["version"].is<uint32_t>()||!d["sha256"].is<const char*>()){lastDiagnostic="CACHE_CORRUPT";return false;}
  cacheVersion=d["version"].as<uint32_t>();cacheDigest=d["sha256"].as<String>();
  for(JsonVariant v:d["entries"].as<JsonArray>()){
    String uid=v.as<String>();uid.toUpperCase();
    if((uid.length()!=8&&uid.length()!=14&&uid.length()!=20)){lastDiagnostic="CACHE_CORRUPT";return false;}
    for(size_t i=0;i<uid.length();i++)if(!isHexadecimalDigit(uid[i])){lastDiagnostic="CACHE_CORRUPT";return false;}
    cacheUids.push_back(uid);
  }
  return true;
}
void sendJson(JsonDocument& doc,int status=200){ String out; serializeJson(doc,out); server.send(status,"application/json",out); }
String utcTimestamp() {
  if(!timeSynced) return "";
  time_t now=time(nullptr); if(now<1700000000) return "";
  struct tm value; gmtime_r(&now,&value); char text[25];
  if(strftime(text,sizeof(text),"%Y-%m-%dT%H:%M:%SZ",&value)==0) return "";
  return String(text);
}
void addEvent(const String& uid) {
  if(queueLines.size()>=MAX_QUEUE) { readingEnabled=false; uartState="QUEUE_FULL"; lastDiagnostic="QUEUE_CAPACITY_REACHED"; return; }
  if(!readingEnabled || !storageReady || sessionId.isEmpty() || sessionGeneration.isEmpty()) { uartState="PAUSED"; return; }
  bool registeredCandidate=false;for(const auto& cached:cacheUids)if(cached==uid){registeredCandidate=true;break;}
  JsonDocument d; d["event_id"]=String(DEVICE_ID)+":"+bootId+":"+String(++sequenceNo); d["device_id"]=DEVICE_ID;
  d["boot_id"]=bootId; d["sequence"]=sequenceNo; d["session_id"]=sessionId.toInt(); d["session_generation"]=sessionGeneration;
  d["uid"]=uid; d["registered_candidate"]=registeredCandidate; String captured=utcTimestamp();
  if(captured.isEmpty()){d["captured_at"]=nullptr;d["time_quality"]="UNKNOWN";}
  else {d["captured_at"]=captured;d["time_quality"]="SYNCED";}
  d["captured_mono_ms"]=millis();
  String item; serializeJson(d,item); queueLines.push_back(item);
  if(!writeQueue()) { queueLines.pop_back(); sequenceNo--; readingEnabled=false; uartState="STORAGE_ERROR"; lastDiagnostic="QUEUE_WRITE_FAILED"; return; }
  uartState="PROVISIONAL_SAVED";
}
void handleUartLine(const String& line) {
  if(line=="READY"){unoReady=true;if(readingEnabled)uartState="READY";else uartState="PAUSED";return;}
  if(line.startsWith("DIAG:")){lastDiagnostic=line.substring(5);return;}
  if(line.startsWith("ERR:")){unoReady=false;uartState="UART_ERROR";lastDiagnostic=line;return;}
  if(!line.startsWith("UID:")) return;
  String uid=line.substring(4); uid.toUpperCase();
  if((uid.length()!=8&&uid.length()!=14&&uid.length()!=20)) {lastDiagnostic="INVALID_UID_LENGTH";return;}
  for(size_t i=0;i<uid.length();i++) if(!isHexadecimalDigit(uid[i])) {lastDiagnostic="INVALID_UID_HEX";return;}
  addEvent(uid);
}
const char* indexHtml="<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'><title>RFID ESP32</title><h1>RFID端末状態</h1><p>状態・操作はPC管理画面から確認してください。</p><p>PCが切断中の読み取りは仮受付です。</p>";
void setupRoutes() {
  const char* requestHeaders[]={"X-Device-Id","X-Signature","X-Timestamp","X-Nonce"};
  server.collectHeaders(requestHeaders,sizeof(requestHeaders)/sizeof(requestHeaders[0]));
  server.on("/",HTTP_GET,[]{server.send(200,"text/html; charset=utf-8",indexHtml);});
  server.on("/api/v1/health",HTTP_GET,[]{if(!authorized()){server.send(401,"application/json","{}");return;} JsonDocument d; d["ok"]=true;d["device_id"]=DEVICE_ID;d["boot_id"]=bootId;sendJson(d);});
  server.on("/api/v1/status",HTTP_GET,[]{if(!authorized()){server.send(401,"application/json","{}");return;} JsonDocument d;d["device_id"]=DEVICE_ID;d["boot_id"]=bootId;d["firmware_version"]="2.0.0-dev";d["state"]=uartState;d["uno_ready"]=unoReady;d["reading_enabled"]=readingEnabled;d["storage_ready"]=storageReady;d["time_quality"]=timeSynced?"SYNCED":"UNKNOWN";d["pending_events"]=queueLines.size();d["last_sequence"]=sequenceNo;d["acked_through_seq"]=ackedSequence;d["cache_version"]=cacheVersion;d["cache_sha256"]=cacheDigest;d["cache_count"]=cacheUids.size();d["active_session_id"]=sessionId.isEmpty()?0:sessionId.toInt();d["session_generation"]=sessionGeneration;d["last_diagnostic"]=lastDiagnostic;sendJson(d);});
  server.on("/api/v1/events",HTTP_GET,[]{if(!authorized()){server.send(401,"application/json","{}");return;}long parsedAfter=server.arg("after_seq").toInt();long parsedLimit=server.arg("limit").toInt();if(parsedAfter<0||parsedLimit<1||parsedLimit>50){server.send(422,"application/json","{}");return;}uint32_t after=(uint32_t)parsedAfter;String bid=server.arg("boot_id");if(bid!=bootId){server.send(409,"application/json","{\"reset_required\":true}");return;}JsonDocument d;d["boot_id"]=bootId;JsonArray a=d["events"].to<JsonArray>();size_t count=0;for(auto& raw:queueLines){JsonDocument item;if(deserializeJson(item,raw)){server.send(500,"application/json","{}");return;}uint32_t seq=item["sequence"]|0;if(seq>after&&seq>ackedSequence){a.add(item);if(++count>=(size_t)parsedLimit)break;}}sendJson(d);});
  server.on("/api/v1/events/ack",HTTP_POST,[]{if(!authorized()){server.send(401,"application/json","{}");return;}JsonDocument d;if(deserializeJson(d,server.arg("plain"))||String((const char*)(d["boot_id"]|""))!=bootId||!d["ack_through_seq"].is<uint32_t>()){server.send(400,"application/json","{}");return;}uint32_t ack=d["ack_through_seq"].as<uint32_t>();if(ack<ackedSequence||ack>sequenceNo){server.send(409,"application/json","{}");return;}if(ack>ackedSequence){uint32_t expected=ackedSequence+1;for(const auto& raw:queueLines){JsonDocument e;if(deserializeJson(e,raw)){server.send(500,"application/json","{}");return;}uint32_t seq=e["sequence"]|0;if(seq<=ackedSequence)continue;if(seq!=expected||seq>ack)break;expected++;}if(expected!=ack+1){server.send(409,"application/json","{}");return;}if(prefs.putUInt("acked",ack)!=sizeof(uint32_t)){server.send(500,"application/json","{}");return;}ackedSequence=ack;for(auto it=queueLines.begin();it!=queueLines.end();){JsonDocument e;if(!deserializeJson(e,*it)&&(uint32_t)(e["sequence"]|0)<=ack)it=queueLines.erase(it);else ++it;}if(!writeQueue()){lastDiagnostic="QUEUE_COMPACTION_RETRY_REQUIRED";}}
    JsonDocument out;out["acked_through_seq"]=ackedSequence;sendJson(out);});
  server.on("/api/v1/commands",HTTP_POST,[]{if(!authorized()){server.send(401,"application/json","{}");return;}if(server.arg("plain").length()>MAX_BODY){server.send(413,"application/json","{}");return;}JsonDocument d;if(deserializeJson(d,server.arg("plain"))||!d["command"].is<const char*>()){server.send(400,"application/json","{}");return;}String cmd=d["command"].as<String>();if(cmd=="PAUSE_READING"){readingEnabled=false;uartState="PAUSED";}else if(cmd=="RESUME_READING"){if(!storageReady||sessionId.isEmpty()||queueLines.size()>=MAX_QUEUE){server.send(409,"application/json","{}");return;}readingEnabled=true;uartState=unoReady?"READY":"WAITING";uno.print("HELLO\n");}else if(cmd=="BIND_SESSION"){int requestedSession=d["session_id"]|0;String requestedGeneration=d["session_generation"]|"";if(!storageReady||requestedSession<=0||requestedGeneration.length()<16||requestedGeneration.length()>64){server.send(422,"application/json","{}");return;}String nextSession=String(requestedSession);if(!prefs.putString("session",nextSession)||!prefs.putString("generation",requestedGeneration)){server.send(500,"application/json","{}");return;}sessionId=nextSession;sessionGeneration=requestedGeneration;readingEnabled=true;uartState=unoReady?"READY":"WAITING";uno.print("HELLO\n");}else if(cmd=="CLEAR_SESSION"){readingEnabled=false;if(!prefs.remove("session")||!prefs.remove("generation")){server.send(500,"application/json","{}");return;}sessionId="";sessionGeneration="";uartState="PAUSED";}else if(cmd=="SYNC_TIME"){if(!d["epoch_ms"].is<uint64_t>()){server.send(422,"application/json","{}");return;}uint64_t ms=d["epoch_ms"].as<uint64_t>();if(ms<1577836800000ULL||ms>4102444800000ULL){server.send(422,"application/json","{}");return;}timeval tv{(time_t)(ms/1000),(suseconds_t)((ms%1000)*1000)};if(settimeofday(&tv,nullptr)!=0){server.send(500,"application/json","{}");return;}timeSynced=true;}else if(cmd=="DIAGNOSTIC"){uno.print("HELLO\n");lastDiagnostic="UART_DIAGNOSTIC_REQUESTED";}else{server.send(422,"application/json","{}");return;}JsonDocument out;out["accepted"]=true;out["request_id"]=d["request_id"];sendJson(out,202);});
  server.on("/api/v1/cache",HTTP_PUT,[]{if(!authorized()){server.send(401,"application/json","{}");return;}if(server.arg("plain").length()>MAX_BODY){server.send(413,"application/json","{}");return;}JsonDocument d;if(deserializeJson(d,server.arg("plain"))||!d["version"].is<uint32_t>()||!d["entries"].is<JsonArray>()||!d["sha256"].is<const char*>()){server.send(422,"application/json","{}");return;}uint32_t version=d["version"].as<uint32_t>();String digest=d["sha256"].as<String>();String canonical;serializeJson(d["entries"],canonical);if(digest.length()!=64||sha256Hex((const uint8_t*)canonical.c_str(),canonical.length())!=digest){server.send(422,"application/json","{}");return;}if(version<cacheVersion||(version==cacheVersion&&cacheDigest!=digest)){server.send(409,"application/json","{}");return;}if(d["entries"].as<JsonArray>().size()>512){server.send(413,"application/json","{}");return;}std::vector<String> next;for(JsonObject e:d["entries"].as<JsonArray>()){if(!e["student_ref"].is<int>()){server.send(422,"application/json","{}");return;}String uid=e["uid"]|"";uid.toUpperCase();if(uid.length()!=8&&uid.length()!=14&&uid.length()!=20){server.send(422,"application/json","{}");return;}for(size_t i=0;i<uid.length();i++)if(!isHexadecimalDigit(uid[i])){server.send(422,"application/json","{}");return;}next.push_back(uid);}if(!persistCache(next,version,digest)){server.send(500,"application/json","{}");return;}cacheUids.swap(next);cacheVersion=version;cacheDigest=digest;if(prefs.putUInt("cache_version",cacheVersion)!=sizeof(uint32_t)){server.send(500,"application/json","{}");return;}JsonDocument out;out["version"]=cacheVersion;out["count"]=cacheUids.size();out["sha256"]=cacheDigest;sendJson(out);});
}
void pollUart(){while(uno.available()){char c=(char)uno.read();if(c=='\r')continue;if(c=='\n'){if(!overflowLine)handleUartLine(lineBuffer);lineBuffer="";overflowLine=false;}else if(lineBuffer.length()<MAX_LINE)lineBuffer+=c;else overflowLine=true;}}
}
void setup(){
  Serial.begin(115200);uno.begin(UART_BAUD,SERIAL_8N1,UNO_RX,UNO_TX);
  if(strlen(AP_PASSWORD)<16||strlen(DEVICE_TOKEN)<32){Serial.println("Credential configuration invalid");return;}
  if(!LittleFS.begin(false)){Serial.println("LittleFS mount failed; refusing to format event storage");uartState="STORAGE_ERROR";return;}
  if(!prefs.begin("rfid-v2",false)){Serial.println("NVS mount failed");uartState="STORAGE_ERROR";return;}
  ackedSequence=prefs.getUInt("acked",0);
  sessionId=prefs.getString("session","");sessionGeneration=prefs.getString("generation","");cacheVersion=prefs.getUInt("cache_version",0);
  storageReady=loadQueue()&&loadCache();
  if(!storageReady){uartState="STORAGE_ERROR";}
  if(queueLines.empty()) { bootId=String((uint32_t)esp_random(),HEX)+String((uint32_t)esp_random(),HEX);sequenceNo=0;ackedSequence=0;prefs.putUInt("acked",0);writeQueue(); }
  else if(bootId.isEmpty()) { uartState="STORAGE_ERROR";lastDiagnostic="QUEUE_BOOT_ID_MISSING";return; }
  IPAddress apIp(192,168,4,1),gateway(192,168,4,1),subnet(255,255,255,0);
  WiFi.mode(WIFI_AP);if(!WiFi.softAPConfig(apIp,gateway,subnet)||!WiFi.softAP(AP_SSID,AP_PASSWORD,1,false,1)){Serial.println("SoftAP start failed");uartState="AP_ERROR";return;}
  setupRoutes();server.begin();for(int i=0;i<3;i++){uno.print("HELLO\n");delay(500);}Serial.println("ESP32 SoftAP ready");
}
void loop(){server.handleClient();pollUart();delay(2);}
