#include <WiFi.h>
#include "secrets.h"

// Display TX -> GPIO16. Display RX <- GPIO17.
// One task owns the network. A bell cuts off the picture wait and goes out first.
const char* WIFI_SSID = HAYALI_WIFI_SSID;
const char* WIFI_PASSWORD = HAYALI_WIFI_PASS;
const char* API_HOST = "192.168.0.195";
const uint16_t API_PORT = 8001;
const char* ROOM_ID = "404";
const char* LOG_HOST = "192.168.0.195";
const uint16_t LOG_PORT = 8091;

static const int DISPLAY_RX_PIN = 16;
static const int DISPLAY_TX_PIN = 17;
static const uint8_t GENIE_WRITE_OBJ = 1;
static const uint8_t GENIE_REPORT_EVENT = 0x07;
static const uint8_t GENIE_ACK = 0x06;
static const uint8_t GENIE_NAK = 0x15;
static const uint8_t GENIE_USER_IMAGES = 27;
static const uint8_t GENIE_ANI_BUTTON = 31;
static const uint8_t GENIE_USER_BUTTON = 33;
static const uint8_t BTN_LAUNDRY = 0;
static const uint8_t BTN_DND = 8;
static const uint8_t BTN_MAKEUP = 9;
static const uint8_t BTN_BELL = 21;
static const uint8_t IMG_STATUS_BAR = 0;
static const uint8_t IMG_LAUNDRY = 1;
static const uint8_t IMG_MAKEUP = 2;
static const unsigned long BELL_GAP_MS = 700;
static const unsigned long SCREEN_WAIT_MS = 20000;
static const unsigned long POST_WAIT_MS = 4000;
static const unsigned long STUCK_MS = 60000;

struct ScreenCmd {
  uint16_t bar;
  uint16_t murIcon;
  uint16_t laundryIcon;
  uint16_t bell;
  uint16_t dndBtn;
  uint16_t murBtn;
  uint16_t laundryBtn;
};

HardwareSerial Display(2);
WiFiClient api;
WiFiClient logClient;
QueueHandle_t bytesQ;
QueueHandle_t bellQ;
QueueHandle_t screenQ;

void consider(uint8_t next);
void sendLog(const char* text);

bool bellWaiting() {
  return uxQueueMessagesWaiting(bellQ) > 0;
}

void flushLog() {
  if (bellWaiting() || WiFi.status() != WL_CONNECTED) return;
  if (!logClient.connected()) {
    static unsigned long lastTry = 0;
    if (millis() - lastTry < 1000) return;
    lastTry = millis();
    logClient.stop();
    logClient.connect(LOG_HOST, LOG_PORT, 200);
    return;
  }
  uint8_t chunk[128];
  size_t n = 0;
  uint8_t byteIn = 0;
  while (n < sizeof(chunk) && xQueueReceive(bytesQ, &byteIn, 0) == pdTRUE) {
    chunk[n++] = byteIn;
  }
  if (n > 0 && logClient.write(chunk, n) != n) logClient.stop();
}

bool writeAll(const uint8_t* data, size_t len, unsigned long deadline) {
  size_t sent = 0;
  while (sent < len && (long)(deadline - millis()) > 0) {
    int wrote = api.write(data + sent, len - sent);
    if (wrote <= 0) {
      vTaskDelay(pdMS_TO_TICKS(10));
      continue;
    }
    sent += (size_t)wrote;
  }
  return sent == len;
}

int httpExchange(const char* method, const char* path, const char* body, bool interruptForBell, char* response, size_t responseCap) {
  if (!api.connect(API_HOST, API_PORT, 3000)) return -1;
  char head[320];
  int bodyLen = body ? (int)strlen(body) : 0;
  int headLen = body
    ? snprintf(
        head,
        sizeof(head),
        "%s %s HTTP/1.1\r\nHost: %s:%u\r\nContent-Type: application/json\r\nContent-Length: %d\r\nConnection: close\r\n\r\n",
        method,
        path,
        API_HOST,
        API_PORT,
        bodyLen
      )
    : snprintf(
        head,
        sizeof(head),
        "%s %s HTTP/1.1\r\nHost: %s:%u\r\nConnection: close\r\n\r\n",
        method,
        path,
        API_HOST,
        API_PORT
      );
  unsigned long deadline = millis() + 3000;
  if (headLen <= 0 || !writeAll(reinterpret_cast<uint8_t*>(head), (size_t)headLen, deadline)) {
    api.stop();
    return -1;
  }
  if (bodyLen > 0 && !writeAll(reinterpret_cast<const uint8_t*>(body), (size_t)bodyLen, deadline)) {
    api.stop();
    return -1;
  }

  unsigned long budget = interruptForBell ? SCREEN_WAIT_MS : POST_WAIT_MS;
  unsigned long started = millis();
  char raw[1024];
  size_t n = 0;
  while (millis() - started < budget) {
    if (interruptForBell && bellWaiting()) {
      api.stop();
      return -2;
    }
    flushLog();
    while (api.available() && n + 1 < sizeof(raw)) raw[n++] = (char)api.read();
    if (!api.connected() && !api.available()) break;
    vTaskDelay(pdMS_TO_TICKS(20));
  }
  api.stop();
  raw[n] = 0;
  const char* marker = strstr(raw, "HTTP/1.");
  if (!marker) return -1;
  const char* space = strchr(marker, ' ');
  if (!space) return -1;
  int code = atoi(space + 1);
  const char* split = strstr(raw, "\r\n\r\n");
  const char* payload = split ? split + 4 : "";
  if (response && responseCap > 0) {
    snprintf(response, responseCap, "%s", payload);
  }
  return code;
}

int jsonInt(const char* payload, const char* key) {
  char needle[24];
  snprintf(needle, sizeof(needle), "\"%s\":", key);
  const char* at = strstr(payload, needle);
  if (!at) return 0;
  at += strlen(needle);
  while (*at == ' ') at++;
  return atoi(at);
}

void rejoinWifi() {
  WiFi.disconnect();
  vTaskDelay(pdMS_TO_TICKS(200));
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
}

void linkTask(void*) {
  int applied = -1;
  int failures = 0;
  unsigned long lastOk = millis();
  const char bellBody[] = "{\"button\":\"Bell\",\"type\":31,\"index\":21,\"value\":1}";
  for (;;) {
    if (millis() - lastOk > STUCK_MS) ESP.restart();
    if (WiFi.status() != WL_CONNECTED) {
      static unsigned long lastJoin = 0;
      if (millis() - lastJoin > 5000) {
        lastJoin = millis();
        rejoinWifi();
      }
      vTaskDelay(pdMS_TO_TICKS(300));
      continue;
    }

    uint8_t flag = 0;
    if (xQueueReceive(bellQ, &flag, 0) == pdTRUE) {
      char path[48];
      snprintf(path, sizeof(path), "/api/rooms/%s", ROOM_ID);
      int code = httpExchange("POST", path, bellBody, false, nullptr, 0);
      Serial.print("Bell -> ");
      Serial.println(code);
      if (code > 0) {
        lastOk = millis();
        failures = 0;
      } else if (++failures >= 3) {
        rejoinWifi();
      }
      continue;
    }

    char path[64];
    char payload[512];
    snprintf(path, sizeof(path), "/api/rooms/%s/screen?rev=%d&wait=12", ROOM_ID, applied);
    int code = httpExchange("GET", path, nullptr, true, payload, sizeof(payload));
    if (code == -2) continue;
    if (code != 200) {
      Serial.print("Screen wait -> ");
      Serial.println(code);
      if (++failures >= 3) rejoinWifi();
      for (int i = 0; i < 20 && !bellWaiting(); i++) vTaskDelay(pdMS_TO_TICKS(100));
      continue;
    }
    lastOk = millis();
    failures = 0;
    int rev = jsonInt(payload, "screen_rev");
    if (applied >= 0 && rev == applied) continue;
    ScreenCmd cmd = {
      (uint16_t)jsonInt(payload, "bar"),
      (uint16_t)jsonInt(payload, "mur_icon"),
      (uint16_t)jsonInt(payload, "laundry_icon"),
      (uint16_t)jsonInt(payload, "bell"),
      (uint16_t)jsonInt(payload, "dnd_btn"),
      (uint16_t)jsonInt(payload, "mur_btn"),
      (uint16_t)jsonInt(payload, "laundry_btn"),
    };
    xQueueOverwrite(screenQ, &cmd);
    applied = rev;
  }
}

void sendGenie(const uint8_t* bytes, int count) {
  uint8_t checksum = 0;
  for (int i = 0; i < count; i++) {
    checksum ^= bytes[i];
    Display.write(bytes[i]);
  }
  Display.write(checksum);
}

void pauseForAck() {
  unsigned long start = millis();
  while (millis() - start < 12) {
    while (Display.available()) consider(Display.read());
    delay(1);
  }
}

void writeObj(uint8_t object, uint8_t index, uint16_t value) {
  uint8_t msg[] = {
    GENIE_WRITE_OBJ,
    object,
    index,
    (uint8_t)((value >> 8) & 0xFF),
    (uint8_t)(value & 0xFF),
  };
  sendGenie(msg, 5);
  pauseForAck();
}

void applyScreen(const ScreenCmd& cmd) {
  writeObj(GENIE_USER_IMAGES, IMG_STATUS_BAR, cmd.bar);
  writeObj(GENIE_USER_IMAGES, IMG_LAUNDRY, cmd.laundryIcon);
  writeObj(GENIE_USER_IMAGES, IMG_MAKEUP, cmd.murIcon);
  writeObj(GENIE_ANI_BUTTON, BTN_BELL, cmd.bell);
  writeObj(GENIE_USER_BUTTON, BTN_DND, cmd.dndBtn);
  writeObj(GENIE_USER_BUTTON, BTN_MAKEUP, cmd.murBtn);
  writeObj(GENIE_USER_BUTTON, BTN_LAUNDRY, cmd.laundryBtn);
  char line[96];
  snprintf(
    line,
    sizeof(line),
    "Screen bar %u, laundry %s, make up %s\n",
    cmd.bar,
    cmd.laundryIcon ? "on" : "off",
    cmd.murIcon ? "on" : "off"
  );
  sendLog(line);
}

void sendLog(const char* text) {
  while (*text) {
    uint8_t byteOut = (uint8_t)*text++;
    xQueueSend(bytesQ, &byteOut, 0);
  }
}

void noteFrame(const uint8_t* frame) {
  uint8_t checksum = 0;
  for (int i = 0; i < 6; i++) checksum ^= frame[i];
  if (checksum != 0) return;
  if (frame[0] != GENIE_REPORT_EVENT || frame[1] != GENIE_ANI_BUTTON || frame[2] != BTN_BELL) return;

  static unsigned long lastBellMs = 0;
  unsigned long now = millis();
  if (now - lastBellMs < BELL_GAP_MS) return;
  lastBellMs = now;
  uint8_t flag = 1;
  xQueueSend(bellQ, &flag, 0);
  sendLog("Bell\n");
}

void consider(uint8_t next) {
  static uint8_t frame[6];
  static int length = 0;
  static unsigned long lastByteMs = 0;
  unsigned long now = millis();
  if (length > 0 && now - lastByteMs > 40) length = 0;
  lastByteMs = now;
  if (length == 0 && (next == GENIE_ACK || next == GENIE_NAK)) return;

  frame[length++] = next;
  if (length < 6) return;
  noteFrame(frame);
  for (int i = 0; i < 5; i++) frame[i] = frame[i + 1];
  length = 5;
}

void setup() {
  Serial.begin(115200);
  Display.begin(115200, SERIAL_8N1, DISPLAY_RX_PIN, DISPLAY_TX_PIN);
  bytesQ = xQueueCreate(1024, sizeof(uint8_t));
  bellQ = xQueueCreate(4, sizeof(uint8_t));
  screenQ = xQueueCreate(1, sizeof(ScreenCmd));
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  xTaskCreatePinnedToCore(linkTask, "link", 16384, nullptr, 1, nullptr, 0);
  Serial.println("Serial log. One link, bell first, then the picture.");
}

void loop() {
  while (Display.available()) consider(Display.read());
  ScreenCmd cmd;
  if (xQueueReceive(screenQ, &cmd, 0) == pdTRUE) applyScreen(cmd);
}
