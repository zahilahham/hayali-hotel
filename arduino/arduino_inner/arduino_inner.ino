// Hayali room screen. The door screen is arduino/arduino.ino.
// Display TX -> GPIO16. Display RX <- GPIO17. Do not swap these pins.

#include "hayali.h"
#include "secrets.h"
#include <WiFi.h>
#include <Preferences.h>
#include <Update.h>
#include <strings.h>
#include "esp_task_wdt.h"
#include "esp_ota_ops.h"
#include "esp_system.h"
#include "esp_random.h"

#define PANEL_INNER 1
#define FW_PANEL "inner"
#define FW_VERSION "2"

static const char FW_MARK[] = "hayali-firmware:" FW_PANEL ":" FW_VERSION;

// A program installed from the server is confirmed only after the server answers.
// Until then a bad program falls back to the previous one on its own.
extern "C" bool verifyRollbackLater() {
  return true;
}

static const int DISPLAY_RX_PIN = 16;
static const int DISPLAY_TX_PIN = 17;

static const uint8_t GENIE_WRITE_OBJ = 1;
static const uint8_t GENIE_REPORT_EVENT = 7;
static const uint8_t GENIE_ACK = 0x06;
static const uint8_t GENIE_NAK = 0x15;
static const uint8_t GENIE_USER_IMAGES = 27;
static const uint8_t GENIE_ANI_BUTTON = 31;
static const uint8_t GENIE_USER_BUTTON = 33;

static const char* DEFAULT_SSID = HAYALI_WIFI_SSID;
static const char* DEFAULT_PASS = HAYALI_WIFI_PASS;
static const char* DEFAULT_HOST = HAYALI_SERVER_HOST;
static const uint16_t DEFAULT_PORT = 8001;

static const unsigned long HOLD_SECONDS = 20;
static const unsigned long GET_BUDGET_MS = 28000;
static const unsigned long POST_BUDGET_MS = 8000;
static const unsigned long QUIET_RESTART_MS = 60000;
static const unsigned long PRESS_KEEP_MS = 25000;
static const unsigned long PRESS_GAP_MS = 700;
static const unsigned long REDRAW_MS = 30000;
static const unsigned long ACK_MS = 500;

static const uint8_t PRESS_BELL = 0;
static const uint8_t PRESS_DND = 1;
static const uint8_t PRESS_MUR = 2;
static const uint8_t PRESS_LAUNDRY = 3;

struct PressInfo {
  const char* name;
  uint8_t type;
  uint8_t index;
  const char* said;
};

static const PressInfo PRESS_INFO[] = {
  {"Bell", GENIE_ANI_BUTTON, 21, "Bell"},
  {"Do Not Disturb", GENIE_USER_BUTTON, 8, "Do not disturb"},
  {"Make Up Room", GENIE_USER_BUTTON, 9, "Make up"},
  {"Laundry Pickup", GENIE_USER_BUTTON, 0, "Laundry"},
};

static Settings cfg;
static HardwareSerial Display(2);
static WiFiClient api;
static QueueHandle_t pressQ;
static QueueHandle_t pictureQ;
static portMUX_TYPE pressMux = portMUX_INITIALIZER_UNLOCKED;

static uint32_t bootId = 0;
static int resetReason = 0;
static char sketchMd5[40];
static uint16_t pressSeq = 0;
static int appliedRev = -1;
static volatile uint32_t lastServerMs = 0;
static volatile uint32_t restartAt = 0;
static bool wdtLong = false;
static bool programConfirmed = false;

static char httpRaw[1400];
static char httpBody[1200];
static char httpRequest[768];

static bool panelSends(uint8_t kind) {
#if PANEL_INNER
  return kind == PRESS_DND || kind == PRESS_MUR || kind == PRESS_LAUNDRY;
#else
  return kind == PRESS_BELL;
#endif
}

static bool pressWaiting() {
  return uxQueueMessagesWaiting(pressQ) > 0;
}

static void feedWdt() {
  if (wdtLong) {
    esp_task_wdt_reset();
  }
}

static void copyText(char* dest, size_t cap, const char* src) {
  if (cap == 0) {
    return;
  }
  size_t n = 0;
  if (src != nullptr) {
    while (src[n] != 0 && n + 1 < cap) {
      dest[n] = src[n];
      n++;
    }
  }
  dest[n] = 0;
}

static void loadSettings() {
  Preferences prefs;
  prefs.begin("hayali", true);
  if (!prefs.getString("room", cfg.room, sizeof(cfg.room))) {
    cfg.room[0] = 0;
  }
  if (!prefs.isKey("ssid") || !prefs.getString("ssid", cfg.ssid, sizeof(cfg.ssid))) {
    copyText(cfg.ssid, sizeof(cfg.ssid), DEFAULT_SSID);
  }
  // An open network saves an empty password, which must not fall back to the default.
  if (!prefs.isKey("pass")) {
    copyText(cfg.pass, sizeof(cfg.pass), DEFAULT_PASS);
  } else if (!prefs.getString("pass", cfg.pass, sizeof(cfg.pass))) {
    cfg.pass[0] = 0;
  }
  if (!prefs.isKey("host") || !prefs.getString("host", cfg.host, sizeof(cfg.host))) {
    copyText(cfg.host, sizeof(cfg.host), DEFAULT_HOST);
  }
  cfg.port = prefs.isKey("port") ? prefs.getUShort("port", DEFAULT_PORT) : DEFAULT_PORT;
  if (!cfg.port) {
    cfg.port = DEFAULT_PORT;
  }
  if (!prefs.getString("key", cfg.key, sizeof(cfg.key))) {
    cfg.key[0] = 0;
  }
  if (!prefs.getString("tried", cfg.tried, sizeof(cfg.tried))) {
    cfg.tried[0] = 0;
  }
  prefs.end();
}

static void saveString(const char* key, const char* value) {
  Preferences prefs;
  prefs.begin("hayali", false);
  prefs.putString(key, value);
  prefs.end();
}

static void savePort(uint16_t port) {
  Preferences prefs;
  prefs.begin("hayali", false);
  prefs.putUShort("port", port);
  prefs.end();
}

static void scheduleRestart() {
  restartAt = millis() + 3000;
  if (restartAt == 0) {
    restartAt = 1;
  }
}

static void snapshot(Net* net) {
  copyText(net->room, sizeof(net->room), cfg.room);
  copyText(net->host, sizeof(net->host), cfg.host);
  copyText(net->key, sizeof(net->key), cfg.key);
  net->port = cfg.port;
}

static bool queuePress(uint8_t kind) {
  Press press;
  press.kind = kind;
  press.born = millis();
  portENTER_CRITICAL(&pressMux);
  press.seq = ++pressSeq;
  portEXIT_CRITICAL(&pressMux);
  if (xQueueSend(pressQ, &press, 0) == pdTRUE) {
    return true;
  }
  Serial.println("Press not taken, the queue is full");
  return false;
}

static uint8_t frame[6];
static int frameLen = 0;
static uint32_t lastByteMs = 0;
static uint32_t lastPressMs[4] = {0, 0, 0, 0};
static uint32_t quietUntil[4] = {0, 0, 0, 0};

static void noteFrame() {
  if (frame[0] != GENIE_REPORT_EVENT) {
    return;
  }
  uint8_t kind = 255;
  if (frame[1] == GENIE_ANI_BUTTON && frame[2] == 21) {
    kind = PRESS_BELL;
  } else if (frame[1] == GENIE_USER_BUTTON && frame[2] == 8) {
    kind = PRESS_DND;
  } else if (frame[1] == GENIE_USER_BUTTON && frame[2] == 9) {
    kind = PRESS_MUR;
  } else if (frame[1] == GENIE_USER_BUTTON && frame[2] == 0) {
    kind = PRESS_LAUNDRY;
  }
  if (kind == 255 || !panelSends(kind)) {
    return;
  }
  uint32_t now = millis();
  if ((int32_t)(quietUntil[kind] - now) > 0) {
    return;
  }
  if (now - lastPressMs[kind] < PRESS_GAP_MS) {
    return;
  }
  lastPressMs[kind] = now;
  if (queuePress(kind)) {
    Serial.println(PRESS_INFO[kind].said);
  }
}

static int takeByte(uint8_t next) {
  uint32_t now = millis();
  if (frameLen > 0 && now - lastByteMs > 40) {
    frameLen = 0;
  }
  lastByteMs = now;
  if (frameLen == 0 && (next == GENIE_ACK || next == GENIE_NAK)) {
    return next == GENIE_ACK ? 1 : -1;
  }
  frame[frameLen++] = next;
  if (frameLen < 6) {
    return 0;
  }
  uint8_t sum = 0;
  for (int i = 0; i < 6; i++) {
    sum ^= frame[i];
  }
  if (sum == 0) {
    noteFrame();
    frameLen = 0;
    return 0;
  }
  for (int i = 0; i < 5; i++) {
    frame[i] = frame[i + 1];
  }
  frameLen = 5;
  return 0;
}

static int waitReply(uint32_t budget) {
  uint32_t started = millis();
  while (millis() - started < budget) {
    feedWdt();
    while (Display.available()) {
      int reply = takeByte(Display.read());
      if (reply != 0) {
        return reply;
      }
    }
    vTaskDelay(pdMS_TO_TICKS(2));
  }
  return 0;
}

static void quietWrite(uint8_t object, uint8_t index) {
  int kind = -1;
  if (object == GENIE_ANI_BUTTON && index == 21) kind = PRESS_BELL;
  else if (object == GENIE_USER_BUTTON && index == 8) kind = PRESS_DND;
  else if (object == GENIE_USER_BUTTON && index == 9) kind = PRESS_MUR;
  else if (object == GENIE_USER_BUTTON && index == 0) kind = PRESS_LAUNDRY;
  if (kind >= 0) quietUntil[kind] = millis() + 1500;
}

static bool writeObj(uint8_t object, uint8_t index, uint16_t value) {
  quietWrite(object, index);
  uint8_t msg[6] = {
    GENIE_WRITE_OBJ,
    object,
    index,
    (uint8_t)((value >> 8) & 0xFF),
    (uint8_t)(value & 0xFF),
    0,
  };
  msg[5] = msg[0] ^ msg[1] ^ msg[2] ^ msg[3] ^ msg[4];
  for (int attempt = 0; attempt < 2; attempt++) {
    Display.write(msg, sizeof(msg));
    int reply = waitReply(ACK_MS);
    if (reply == 1) {
      return true;
    }
  }
  return false;
}

static bool drawPicture(const Picture& picture) {
  // Every object is written, even when one of them does not answer.
#if PANEL_INNER
  bool dnd = writeObj(GENIE_USER_BUTTON, 8, picture.dndBtn);
  bool mur = writeObj(GENIE_USER_BUTTON, 9, picture.murBtn);
  bool laundry = writeObj(GENIE_USER_BUTTON, 0, picture.laundryBtn);
  return dnd && mur && laundry;
#else
  bool bar = writeObj(GENIE_USER_IMAGES, 0, picture.bar);
  bool laundry = writeObj(GENIE_USER_IMAGES, 1, picture.laundryIcon);
  bool mur = writeObj(GENIE_USER_IMAGES, 2, picture.murIcon);
  bool bell = writeObj(GENIE_ANI_BUTTON, 21, picture.bell);
  return bar && laundry && mur && bell;
#endif
}

static bool sameView(const Picture& a, const Picture& b) {
  return a.bar == b.bar && a.murIcon == b.murIcon && a.laundryIcon == b.laundryIcon
    && a.bell == b.bell && a.dndBtn == b.dndBtn && a.murBtn == b.murBtn
    && a.laundryBtn == b.laundryBtn;
}

static void displayTask(void*) {
  if (wdtLong) {
    esp_task_wdt_add(nullptr);
  }
  Picture shown = {};
  bool have = false;
  bool dirty = false;
  bool onScreen = false;
  bool told = false;
  uint32_t lastDraw = 0;
  uint32_t lastAttempt = 0;
  for (;;) {
    feedWdt();
    while (Display.available()) {
      takeByte(Display.read());
    }
    Picture next;
    if (xQueueReceive(pictureQ, &next, 0) == pdTRUE) {
      if (!have || !sameView(shown, next)) {
        dirty = true;
      }
      shown = next;
      have = true;
    }
    if (have && !dirty && millis() - lastDraw > REDRAW_MS) {
      dirty = true;
    }
    if (dirty && millis() - lastAttempt > 1000) {
      lastAttempt = millis();
      if (drawPicture(shown)) {
        dirty = false;
        lastDraw = millis();
        if (!onScreen) {
          Serial.println("Picture is on the screen");
          onScreen = true;
          told = true;
        }
      } else if (!told || onScreen) {
        Serial.println("The screen did not confirm the picture");
        onScreen = false;
        told = true;
      }
    }
    vTaskDelay(pdMS_TO_TICKS(5));
  }
}

static bool jsonInt(const char* json, const char* key, int* out) {
  char needle[28];
  snprintf(needle, sizeof(needle), "\"%s\":", key);
  const char* at = strstr(json, needle);
  if (at == nullptr) {
    return false;
  }
  at += strlen(needle);
  while (*at == ' ') {
    at++;
  }
  if (*at != '-' && (*at < '0' || *at > '9')) {
    return false;
  }
  *out = atoi(at);
  return true;
}

static bool jsonText(const char* json, const char* key, char* out, size_t cap) {
  char needle[28];
  snprintf(needle, sizeof(needle), "\"%s\":", key);
  const char* at = strstr(json, needle);
  if (at == nullptr) {
    return false;
  }
  at += strlen(needle);
  while (*at == ' ') {
    at++;
  }
  if (*at != '"') {
    return false;
  }
  at++;
  size_t n = 0;
  while (*at != 0 && *at != '"' && n + 1 < cap) {
    out[n++] = *at++;
  }
  out[n] = 0;
  return *at == '"' && n > 0;
}

static bool parsePicture(const char* json, Picture* picture) {
  int rev = 0;
  int bar = 0;
  int mur = 0;
  int laundry = 0;
  int bell = 0;
  int dnd = 0;
  int murBtn = 0;
  int laundryBtn = 0;
  if (!jsonInt(json, "screen_rev", &rev) || !jsonInt(json, "bar", &bar)
      || !jsonInt(json, "mur_icon", &mur) || !jsonInt(json, "laundry_icon", &laundry)
      || !jsonInt(json, "bell", &bell) || !jsonInt(json, "dnd_btn", &dnd)
      || !jsonInt(json, "mur_btn", &murBtn) || !jsonInt(json, "laundry_btn", &laundryBtn)) {
    return false;
  }
  picture->rev = rev;
  picture->bar = (uint16_t)bar;
  picture->murIcon = (uint16_t)mur;
  picture->laundryIcon = (uint16_t)laundry;
  picture->bell = (uint16_t)bell;
  picture->dndBtn = (uint16_t)dnd;
  picture->murBtn = (uint16_t)murBtn;
  picture->laundryBtn = (uint16_t)laundryBtn;
  return true;
}

static void boardHeader(char* out, size_t cap) {
  snprintf(
    out,
    cap,
    "fw=%s;boot=%08lx;up=%lu;rssi=%d;reset=%d",
    FW_VERSION,
    (unsigned long)bootId,
    (unsigned long)(millis() / 1000),
    WiFi.RSSI(),
    resetReason
  );
}

static int headerValue(const char* headers, const char* name) {
  size_t nameLen = strlen(name);
  const char* p = headers;
  while (*p != 0 && !(p[0] == '\r' && p[1] == '\n' && p[2] == '\r')) {
    if (strncasecmp(p, name, nameLen) == 0 && p[nameLen] == ':') {
      p += nameLen + 1;
      while (*p == ' ') {
        p++;
      }
      return atoi(p);
    }
    while (*p != 0 && *p != '\n') {
      p++;
    }
    if (*p == '\n') {
      p++;
    }
  }
  return -1;
}

static bool writeAll(const uint8_t* data, size_t len) {
  size_t sent = 0;
  uint32_t deadline = millis() + 3000;
  while (sent < len && (int32_t)(deadline - millis()) > 0) {
    feedWdt();
    int wrote = api.write(data + sent, len - sent);
    if (wrote < 0) {
      return false;
    }
    if (wrote == 0) {
      vTaskDelay(pdMS_TO_TICKS(10));
      continue;
    }
    sent += (size_t)wrote;
  }
  return sent == len;
}

static int exchange(const Net& net, const char* method, const char* path, const char* body, bool interrupt) {
  if (interrupt && pressWaiting()) {
    return -2;
  }
  api.stop();
  feedWdt();
  if (!api.connect(net.host, net.port, 2000)) {
    return -1;
  }
  api.setNoDelay(true);
  char board[96];
  boardHeader(board, sizeof(board));
  int written;
  if (body != nullptr) {
    written = snprintf(
      httpRequest,
      sizeof(httpRequest),
      "%s %s HTTP/1.1\r\nHost: %s:%u\r\nContent-Type: application/json\r\nContent-Length: %u\r\n"
      "X-Hayali-Panel: %s\r\nX-Hayali-Board: %s\r\n%s%s%sConnection: close\r\n\r\n%s",
      method,
      path,
      net.host,
      net.port,
      (unsigned)strlen(body),
      PANEL_INNER ? "small" : "big",
      board,
      net.key[0] ? "X-Hayali-Key: " : "",
      net.key[0] ? net.key : "",
      net.key[0] ? "\r\n" : "",
      body
    );
  } else {
    written = snprintf(
      httpRequest,
      sizeof(httpRequest),
      "%s %s HTTP/1.1\r\nHost: %s:%u\r\nX-Hayali-Panel: %s\r\nX-Hayali-Board: %s\r\n%s%s%sConnection: close\r\n\r\n",
      method,
      path,
      net.host,
      net.port,
      PANEL_INNER ? "small" : "big",
      board,
      net.key[0] ? "X-Hayali-Key: " : "",
      net.key[0] ? net.key : "",
      net.key[0] ? "\r\n" : ""
    );
  }
  if (written <= 0 || (size_t)written >= sizeof(httpRequest)
      || !writeAll(reinterpret_cast<uint8_t*>(httpRequest), (size_t)written)) {
    api.stop();
    return -1;
  }

  uint32_t budget = body != nullptr ? POST_BUDGET_MS : GET_BUDGET_MS;
  uint32_t started = millis();
  size_t n = 0;
  size_t headerEnd = 0;
  int length = -1;
  while (millis() - started < budget) {
    feedWdt();
    if (interrupt && pressWaiting()) {
      api.stop();
      return -2;
    }
    while (api.available() && n + 1 < sizeof(httpRaw)) {
      httpRaw[n++] = (char)api.read();
    }
    httpRaw[n] = 0;
    if (headerEnd == 0) {
      char* split = strstr(httpRaw, "\r\n\r\n");
      if (split != nullptr) {
        headerEnd = (size_t)(split - httpRaw) + 4;
        length = headerValue(httpRaw, "content-length");
      }
    }
    if (headerEnd > 0 && length >= 0 && n >= headerEnd + (size_t)length) {
      break;
    }
    if (!api.connected() && !api.available()) {
      break;
    }
    vTaskDelay(pdMS_TO_TICKS(20));
  }
  api.stop();
  if (headerEnd == 0) {
    return -1;
  }
  const char* payload = httpRaw + headerEnd;
  size_t payloadLen = n - headerEnd;
  if (length >= 0 && (size_t)length < payloadLen) {
    payloadLen = (size_t)length;
  }
  if (payloadLen >= sizeof(httpBody)) {
    payloadLen = sizeof(httpBody) - 1;
  }
  memcpy(httpBody, payload, payloadLen);
  httpBody[payloadLen] = 0;
  const char* marker = strstr(httpRaw, "HTTP/1.");
  const char* space = marker == nullptr ? nullptr : strchr(marker, ' ');
  if (space == nullptr) {
    return -1;
  }
  return atoi(space + 1);
}

static void confirmProgram() {
  if (programConfirmed) {
    return;
  }
  programConfirmed = true;
  const esp_partition_t* running = esp_ota_get_running_partition();
  esp_ota_img_states_t state;
  if (running != nullptr && esp_ota_get_state_partition(running, &state) == ESP_OK
      && state == ESP_OTA_IMG_PENDING_VERIFY) {
    if (esp_ota_mark_app_valid_cancel_rollback() == ESP_OK) {
      Serial.println("Program confirmed");
    }
  }
  if (cfg.tried[0] != 0 && strcmp(cfg.tried, sketchMd5) == 0) {
    saveString("tried", "");
    cfg.tried[0] = 0;
  }
}

static int pullFirmware(const Net& net, const char* md5) {
  api.stop();
  feedWdt();
  if (!api.connect(net.host, net.port, 3000)) {
    return 0;
  }
  api.setNoDelay(true);
  char board[96];
  boardHeader(board, sizeof(board));
  int written = snprintf(
    httpRequest,
    sizeof(httpRequest),
    "GET /api/firmware/%s HTTP/1.1\r\nHost: %s:%u\r\nX-Hayali-Panel: %s\r\nX-Hayali-Board: %s\r\n%s%s%sConnection: close\r\n\r\n",
    PANEL_INNER ? "small" : "big",
    net.host,
    net.port,
    PANEL_INNER ? "small" : "big",
    board,
    net.key[0] ? "X-Hayali-Key: " : "",
    net.key[0] ? net.key : "",
    net.key[0] ? "\r\n" : ""
  );
  if (written <= 0 || (size_t)written >= sizeof(httpRequest)
      || !writeAll(reinterpret_cast<uint8_t*>(httpRequest), (size_t)written)) {
    api.stop();
    return 0;
  }

  size_t n = 0;
  size_t headerEnd = 0;
  int length = -1;
  uint32_t started = millis();
  while (millis() - started < 15000 && headerEnd == 0) {
    feedWdt();
    if (pressWaiting()) {
      api.stop();
      return -1;
    }
    while (api.available() && n + 1 < sizeof(httpRaw)) {
      httpRaw[n++] = (char)api.read();
    }
    httpRaw[n] = 0;
    char* split = strstr(httpRaw, "\r\n\r\n");
    if (split != nullptr) {
      headerEnd = (size_t)(split - httpRaw) + 4;
      length = headerValue(httpRaw, "content-length");
    } else if (!api.connected() && !api.available()) {
      break;
    } else {
      vTaskDelay(pdMS_TO_TICKS(10));
    }
  }
  int status = 0;
  const char* marker = strstr(httpRaw, "HTTP/1.");
  const char* space = marker == nullptr ? nullptr : strchr(marker, ' ');
  if (space != nullptr) {
    status = atoi(space + 1);
  }
  if (headerEnd == 0 || status != 200 || length < 200000 || length > 0x140000) {
    api.stop();
    Serial.printf("Program not taken (status %d, size %d)\n", status, length);
    return 0;
  }
  if (!Update.begin((size_t)length) || !Update.setMD5(md5)) {
    Update.abort();
    api.stop();
    Serial.println("Program not taken, the controller refused it");
    return 0;
  }
  size_t got = n > headerEnd ? n - headerEnd : 0;
  if (got > 0 && Update.write(reinterpret_cast<uint8_t*>(httpRaw + headerEnd), got) != got) {
    Update.abort();
    api.stop();
    return 0;
  }
  uint8_t chunk[1024];
  while ((int)got < length) {
    feedWdt();
    lastServerMs = millis();
    if (pressWaiting()) {
      Update.abort();
      api.stop();
      Serial.println("Program paused, a press is waiting");
      return -1;
    }
    if (millis() - started > 90000) {
      break;
    }
    int avail = api.available();
    if (avail <= 0) {
      if (!api.connected()) {
        break;
      }
      vTaskDelay(pdMS_TO_TICKS(10));
      continue;
    }
    int want = avail > (int)sizeof(chunk) ? (int)sizeof(chunk) : avail;
    if (want > length - (int)got) {
      want = length - (int)got;
    }
    int read = api.read(chunk, want);
    if (read <= 0) {
      break;
    }
    if (Update.write(chunk, (size_t)read) != (size_t)read) {
      Update.abort();
      api.stop();
      Serial.println("Program not taken, the write failed");
      return 0;
    }
    got += (size_t)read;
  }
  api.stop();
  if ((int)got != length || !Update.end(true)) {
    Update.abort();
    Serial.printf("Program not taken (%s)\n", Update.errorString());
    return 0;
  }
  saveString("tried", md5);
  Serial.println("New program saved. Restarting.");
  delay(200);
  ESP.restart();
  return 1;
}

static void joinWifi() {
  char name[32];
  snprintf(name, sizeof(name), "hayali-%s-%s", cfg.room[0] ? cfg.room : "new", PANEL_INNER ? "in" : "out");
  WiFi.setHostname(name);
  WiFi.disconnect(false, false);
  WiFi.begin(cfg.ssid, cfg.pass);
}

static void linkTask(void*) {
  if (wdtLong) {
    esp_task_wdt_add(nullptr);
  }
  WiFi.persistent(false);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.setAutoReconnect(true);
  WiFi.setScanMethod(WIFI_ALL_CHANNEL_SCAN);
  WiFi.setSortMethod(WIFI_CONNECT_AP_BY_SIGNAL);
  bool wasConnected = false;
  int failures = 0;
  uint32_t nextFirmwareTry = 0;
  uint32_t nextJoin = 0;
  for (;;) {
    feedWdt();
    if (cfg.room[0] == 0) {
      vTaskDelay(pdMS_TO_TICKS(500));
      continue;
    }
    if (WiFi.status() != WL_CONNECTED) {
      if (wasConnected) {
        Serial.println("Wi-Fi dropped");
        wasConnected = false;
      }
      if (nextJoin == 0 || millis() - nextJoin >= 5000) {
        joinWifi();
        uint32_t started = millis();
        while (WiFi.status() != WL_CONNECTED && millis() - started < 12000) {
          feedWdt();
          vTaskDelay(pdMS_TO_TICKS(200));
        }
        nextJoin = millis();
        if (WiFi.status() == WL_CONNECTED) {
          wasConnected = true;
          IPAddress ip = WiFi.localIP();
          Serial.printf("Wi-Fi connected, %u.%u.%u.%u, signal %d\n", ip[0], ip[1], ip[2], ip[3], WiFi.RSSI());
        } else {
          Serial.println("Wi-Fi not connected");
        }
      }
      vTaskDelay(pdMS_TO_TICKS(300));
      continue;
    }

    Net net;
    snapshot(&net);
    Press press;
    if (xQueueReceive(pressQ, &press, 0) == pdTRUE) {
      if (millis() - press.born > PRESS_KEEP_MS) {
        Serial.printf("%s not sent\n", PRESS_INFO[press.kind].said);
        continue;
      }
      char path[48];
      char body[160];
      snprintf(path, sizeof(path), "/api/rooms/%s", net.room);
      snprintf(
        body,
        sizeof(body),
        "{\"button\":\"%s\",\"type\":%u,\"index\":%u,\"value\":1,\"id\":\"%08lx-%u\"}",
        PRESS_INFO[press.kind].name,
        PRESS_INFO[press.kind].type,
        PRESS_INFO[press.kind].index,
        (unsigned long)bootId,
        press.seq
      );
      int code = exchange(net, "POST", path, body, false);
      if (code == 200) {
        lastServerMs = millis();
        failures = 0;
        confirmProgram();
        Picture picture;
        if (parsePicture(httpBody, &picture)) {
          appliedRev = picture.rev;
          xQueueOverwrite(pictureQ, &picture);
        }
        Serial.printf("%s sent\n", PRESS_INFO[press.kind].said);
      } else if (code > 0) {
        lastServerMs = millis();
        failures = 0;
        Serial.printf("%s refused (%d)\n", PRESS_INFO[press.kind].said, code);
      } else if (millis() - press.born > PRESS_KEEP_MS || xQueueSendToFront(pressQ, &press, 0) != pdTRUE) {
        Serial.printf("%s not sent\n", PRESS_INFO[press.kind].said);
      } else {
        if (++failures >= 3) {
          failures = 0;
          wasConnected = false;
          nextJoin = 0;
          Serial.println("Server not answering. Joining Wi-Fi again.");
          WiFi.disconnect(false, false);
        }
        vTaskDelay(pdMS_TO_TICKS(300));
      }
      continue;
    }

    char path[72];
    snprintf(path, sizeof(path), "/api/rooms/%s/screen?rev=%d&wait=%lu", net.room, appliedRev, HOLD_SECONDS);
    int code = exchange(net, "GET", path, nullptr, true);
    if (code == -2) {
      continue;
    }
    if (code == 200) {
      lastServerMs = millis();
      failures = 0;
      confirmProgram();
      Picture picture;
      char offer[40];
      offer[0] = 0;
      if (parsePicture(httpBody, &picture)) {
        if (appliedRev < 0 || picture.rev != appliedRev) {
          appliedRev = picture.rev;
          xQueueOverwrite(pictureQ, &picture);
        }
      }
      if (jsonText(httpBody, "fw", offer, sizeof(offer)) && strlen(offer) == 32
          && strcmp(offer, sketchMd5) != 0 && strcmp(offer, cfg.tried) != 0
          && (int32_t)(millis() - nextFirmwareTry) >= 0 && !pressWaiting()) {
        Serial.println("A new program is offered");
        int pulled = pullFirmware(net, offer);
        if (pulled <= 0) {
          nextFirmwareTry = millis() + (pulled < 0 ? 30000UL : 300000UL);
        }
      }
      continue;
    }
    if (code > 0) {
      lastServerMs = millis();
      failures = 0;
      Serial.printf("Screen request refused (%d)\n", code);
      vTaskDelay(pdMS_TO_TICKS(2000));
      continue;
    }
    Serial.println("Screen request not answered");
    if (++failures >= 3) {
      failures = 0;
      wasConnected = false;
      nextJoin = 0;
      Serial.println("Server not answering. Joining Wi-Fi again.");
      WiFi.disconnect(false, false);
    }
    for (int i = 0; i < 10 && !pressWaiting(); i++) {
      feedWdt();
      vTaskDelay(pdMS_TO_TICKS(100));
    }
  }
}

static bool validRoom(const char* text) {
  size_t n = strlen(text);
  if (n < 1 || n > 8) {
    return false;
  }
  for (size_t i = 0; i < n; i++) {
    if (!isalnum(static_cast<unsigned char>(text[i]))) {
      return false;
    }
  }
  return true;
}

static bool validHost(const char* text) {
  size_t n = strlen(text);
  if (n < 1 || n > 60) {
    return false;
  }
  for (size_t i = 0; i < n; i++) {
    char c = text[i];
    if (!(isalnum(static_cast<unsigned char>(c)) || c == '.' || c == '-' || c == '_')) {
      return false;
    }
  }
  return true;
}

static void printHelp() {
  Serial.println("room 404");
  Serial.println("wifi <name>");
  Serial.println("password <text>");
  Serial.println("server <address> [port]");
  Serial.println("key <text>");
  Serial.println("show");
#if PANEL_INNER
  Serial.println("press dnd");
  Serial.println("press mur");
  Serial.println("press laundry");
#else
  Serial.println("press bell");
#endif
  Serial.println("forget");
  Serial.println("update");
  Serial.println("restart");
}

static void printShow() {
  Serial.printf("%s, program %s\n", PANEL_INNER ? "Room screen" : "Door screen", FW_VERSION);
  Serial.printf("Room %s\n", cfg.room[0] ? cfg.room : "(not set)");
  Serial.printf("Wi-Fi %s, password %s\n", cfg.ssid, cfg.pass[0] ? "set" : "not set");
  if (WiFi.status() == WL_CONNECTED) {
    IPAddress ip = WiFi.localIP();
    Serial.printf("Address %u.%u.%u.%u, signal %d\n", ip[0], ip[1], ip[2], ip[3], WiFi.RSSI());
  } else {
    Serial.println("Wi-Fi not connected");
  }
  Serial.printf("Server %s:%u\n", cfg.host, cfg.port);
  Serial.printf("Key %s\n", cfg.key[0] ? "set" : "not set");
  Serial.printf("Program md5 %s\n", sketchMd5);
  Serial.printf("Picture rev %d, up %lu s\n", appliedRev, (unsigned long)(millis() / 1000));
}

static void handleLine(char* line) {
  while (*line == ' ') {
    line++;
  }
  size_t len = strlen(line);
  while (len > 0 && (line[len - 1] == ' ' || line[len - 1] == '\r')) {
    line[--len] = 0;
  }
  if (len == 0) {
    return;
  }
  if (strcmp(line, "help") == 0) {
    printHelp();
    return;
  }
  if (strcmp(line, "show") == 0) {
    printShow();
    return;
  }
  if (strcmp(line, "restart") == 0) {
    Serial.println("Restarting");
    delay(100);
    ESP.restart();
    return;
  }
  if (strcmp(line, "forget") == 0) {
    Preferences prefs;
    prefs.begin("hayali", false);
    prefs.clear();
    prefs.end();
    Serial.println("Settings cleared. Restarting.");
    delay(100);
    ESP.restart();
    return;
  }
  if (strcmp(line, "update") == 0) {
    saveString("tried", "");
    cfg.tried[0] = 0;
    Serial.println("A new program will be taken again if one is offered");
    return;
  }
  if (strncmp(line, "press ", 6) == 0) {
    const char* name = line + 6;
    uint8_t kind = 255;
    if (strcmp(name, "bell") == 0) kind = PRESS_BELL;
    else if (strcmp(name, "dnd") == 0) kind = PRESS_DND;
    else if (strcmp(name, "mur") == 0) kind = PRESS_MUR;
    else if (strcmp(name, "laundry") == 0) kind = PRESS_LAUNDRY;
    if (kind == 255 || !panelSends(kind)) {
      Serial.println("This screen does not have that button");
      return;
    }
    if (queuePress(kind)) {
      Serial.println(PRESS_INFO[kind].said);
    }
    return;
  }
  if (strncmp(line, "room ", 5) == 0) {
    if (!validRoom(line + 5)) {
      Serial.println("The room is 1 to 8 letters or digits");
      return;
    }
    copyText(cfg.room, sizeof(cfg.room), line + 5);
    saveString("room", cfg.room);
    Serial.println("Saved. Restarting in a moment.");
    scheduleRestart();
    return;
  }
  if (strncmp(line, "wifi ", 5) == 0) {
    if (strlen(line + 5) < 1 || strlen(line + 5) > 32) {
      Serial.println("The Wi-Fi name is too long");
      return;
    }
    copyText(cfg.ssid, sizeof(cfg.ssid), line + 5);
    saveString("ssid", cfg.ssid);
    Serial.println("Saved. Restarting in a moment.");
    scheduleRestart();
    return;
  }
  if (strncmp(line, "password ", 9) == 0 || strcmp(line, "password") == 0) {
    const char* value = line[8] == 0 ? "" : line + 9;
    if (strlen(value) > 64) {
      Serial.println("The password is too long");
      return;
    }
    copyText(cfg.pass, sizeof(cfg.pass), value);
    saveString("pass", cfg.pass);
    Serial.println("Saved. Restarting in a moment.");
    scheduleRestart();
    return;
  }
  if (strncmp(line, "server ", 7) == 0) {
    char host[64];
    int port = cfg.port;
    if (sscanf(line + 7, "%63s %d", host, &port) < 1 || !validHost(host) || port < 1 || port > 65535) {
      Serial.println("Use: server " HAYALI_SERVER_HOST " 8001");
      return;
    }
    copyText(cfg.host, sizeof(cfg.host), host);
    cfg.port = (uint16_t)port;
    saveString("host", cfg.host);
    savePort(cfg.port);
    Serial.println("Saved. Restarting in a moment.");
    scheduleRestart();
    return;
  }
  if (strncmp(line, "key ", 4) == 0 || strcmp(line, "key") == 0) {
    const char* value = line[3] == 0 ? "" : line + 4;
    if (strlen(value) > 64 || strchr(value, ' ') != nullptr) {
      Serial.println("The key cannot contain a space");
      return;
    }
    copyText(cfg.key, sizeof(cfg.key), value);
    saveString("key", cfg.key);
    Serial.println("Saved. Restarting in a moment.");
    scheduleRestart();
    return;
  }
  Serial.println("Unknown command. Type help.");
}

void setup() {
  Serial.begin(115200);
  delay(200);
  bootId = esp_random();
  if (bootId == 0) {
    bootId = 1;
  }
  resetReason = (int)esp_reset_reason();
  String md5 = ESP.getSketchMD5();
  md5.toCharArray(sketchMd5, sizeof(sketchMd5));
  loadSettings();
  lastServerMs = millis();

  esp_task_wdt_config_t wdt = {};
  wdt.timeout_ms = 30000;
  wdt.idle_core_mask = 1;
  wdt.trigger_panic = true;
  wdtLong = esp_task_wdt_reconfigure(&wdt) == ESP_OK;
  enableLoopWDT();

  pressQ = xQueueCreate(8, sizeof(Press));
  pictureQ = xQueueCreate(1, sizeof(Picture));
  Display.setRxBufferSize(1024);
  Display.begin(115200, SERIAL_8N1, DISPLAY_RX_PIN, DISPLAY_TX_PIN);
  xTaskCreatePinnedToCore(displayTask, "genie", 8192, nullptr, 3, nullptr, 1);
  xTaskCreatePinnedToCore(linkTask, "link", 16384, nullptr, 1, nullptr, 0);

  Serial.println(PANEL_INNER ? "Hayali room screen" : "Hayali door screen");
  Serial.printf("Program %s, %s\n", FW_VERSION, FW_MARK);
  if (cfg.room[0] == 0) {
    Serial.println("No room set. Type: room 404");
  } else {
    Serial.printf("Room %s, server %s:%u\n", cfg.room, cfg.host, cfg.port);
  }
  Serial.println(wdtLong ? "Watchdog 30 seconds" : "Watchdog left at its default");
}

static char line[96];
static size_t lineLen = 0;

void loop() {
  while (Serial.available()) {
    char next = (char)Serial.read();
    if (next == '\r' || next == '\n') {
      Serial.println();
      line[lineLen] = 0;
      lineLen = 0;
      handleLine(line);
      continue;
    }
    if (next == 8 || next == 127) {
      if (lineLen > 0) {
        lineLen--;
        Serial.print("\b \b");
      }
      continue;
    }
    if (next < 32 || next > 126) {
      continue;
    }
    if (lineLen + 1 < sizeof(line)) {
      line[lineLen++] = next;
      Serial.write(next);
    }
  }
  uint32_t due = restartAt;
  if (due != 0 && (int32_t)(millis() - due) >= 0) {
    Serial.println("Restarting");
    delay(100);
    ESP.restart();
  }
  if (cfg.room[0] != 0 && millis() - lastServerMs > QUIET_RESTART_MS) {
    Serial.println("No answer from the server for 60 seconds. Restarting.");
    delay(100);
    ESP.restart();
  }
  delay(20);
}
