// FlowSync intersection controller: one ESP32 drives all three tabletop intersections.
//
//   signal task (core 1, fixed 20 ms tick, watchdog)  owns the FSMs and the LED chain. It never waits on the network.
//   net task    (core 0, ~1 s cycle)                  signed HTTP: POST state, GET commands, POST acks.
//
// The backend is authoritative for WHICH axis gets green; this firmware is the final safety gate for HOW: it enforces
// minimum green, yellow, all-red, command freshness (on its own clock) and replay rejection, and runs a fixed-time
// cycle if no successful exchange happened for backend_lost_ms. The portable logic lives in lib/flowsync_core and is
// tested on a PC against the Python reference.
#include <Arduino.h>
#include <HTTPClient.h>
#include <WiFi.h>
#include <esp_system.h>
#include <esp_task_wdt.h>

#include "config.h"
#include "leds595.h"
#include "protocol.h"
#include "signal_fsm.h"
#include "signing.h"
#include "timing_defaults.h"

using namespace fsync;

struct GpioHal : Hal595 {
  void data(bool level) override { digitalWrite(PIN_595_DATA, level ? HIGH : LOW); }
  void clock() override { digitalWrite(PIN_595_CLOCK, HIGH); digitalWrite(PIN_595_CLOCK, LOW); }
  void latch() override { digitalWrite(PIN_595_LATCH, HIGH); digitalWrite(PIN_595_LATCH, LOW); }
};

static void init_outputs() {
  pinMode(PIN_595_OE, OUTPUT);
  digitalWrite(PIN_595_OE, HIGH);          // outputs disabled (an external pull-up also holds this during reset)
  pinMode(PIN_595_DATA, OUTPUT); pinMode(PIN_595_CLOCK, OUTPUT); pinMode(PIN_595_LATCH, OUTPUT);
  digitalWrite(PIN_595_DATA, LOW); digitalWrite(PIN_595_CLOCK, LOW); digitalWrite(PIN_595_LATCH, LOW);
  GpioHal hal;
  const uint8_t red[3] = {kAllRedRegister, kAllRedRegister, kAllRedRegister};
  write_chain(hal, red);                   // latch ALL-RED first ...
  digitalWrite(PIN_595_OE, LOW);           // ... and only then enable the outputs
}

#ifdef LAMP_TEST
// Bring-up: lights ONE lamp at a time (all 18 in turn) so wiring/polarity/resistors can be checked. No network.
void setup() {
  Serial.begin(115200);
  init_outputs();
  Serial.println("[lamptest] one lamp at a time, order per intersection: A_R A_Y A_G B_R B_Y B_G");
}
void loop() {
  GpioHal hal;
  for (int i = 0; i < kIntersections; i++)
    for (int bit = 0; bit < 6; bit++) {
      uint8_t regs[3] = {0, 0, 0};
      regs[i] = (uint8_t)(1u << bit);
      write_chain(hal, regs);
      Serial.printf("[lamptest] %s bit %d\n", kIntersectionNames[i], bit);
      delay(600);
    }
  const uint8_t red[3] = {kAllRedRegister, kAllRedRegister, kAllRedRegister};
  write_chain(hal, red);
  delay(1500);
}
#else

struct CmdMsg { bool ok; int8_t inter; char target; uint32_t valid_for_ms; int64_t seq; };
struct AckMsg { int64_t seq; int8_t inter; bool accepted; char reason[16]; };

static SemaphoreHandle_t g_mu;
static QueueHandle_t g_cmdq, g_ackq;
static DeviceState g_snap[kIntersections];
static volatile uint32_t g_last_ok_ms = 0;   // millis() of the last fully successful state+commands exchange
static uint32_t g_seq = 0;                   // signed-request sequence; resets with the random boot id on reboot
static char g_boot[9];

static void signal_task(void*) {
  esp_task_wdt_add(nullptr);
  GpioHal hal;
  SignalFsm fsm[kIntersections] = {SignalFsm(kTiming, millis()), SignalFsm(kTiming, millis()), SignalFsm(kTiming, millis())};
  uint8_t shown[kIntersections] = {kAllRedRegister, kAllRedRegister, kAllRedRegister};
  bool fault_logged = false;
  TickType_t wake = xTaskGetTickCount();
  for (;;) {
    uint32_t now = millis();

    CmdMsg m;
    while (xQueueReceive(g_cmdq, &m, 0) == pdTRUE) {
      AckMsg a = {m.seq, m.inter, false, {0}};
      if (!m.ok) {
        strlcpy(a.reason, "bad_command", sizeof a.reason);
      } else {
        const char* why;
        a.accepted = fsm[m.inter].receive_command(m.seq, m.target, m.valid_for_ms, now, &why);
        strlcpy(a.reason, why, sizeof a.reason);
      }
      if (a.inter >= 0 && (a.accepted || strcmp(a.reason, "stale_seq") != 0))   // re-polled duplicates are not acked
        xQueueSend(g_ackq, &a, 0);
    }

    bool backend_ok = (uint32_t)(now - g_last_ok_ms) < kTiming.backend_lost_ms;
    uint8_t regs[kIntersections];
    for (int i = 0; i < kIntersections; i++) {
      fsm[i].set_backend_ok(backend_ok);
      fsm[i].tick(now);
      Lamps l = fsm[i].lamps();
      if (lamps_safe(l)) {
        regs[i] = encode_lamps(l);
      } else {                                   // cannot happen by construction; defence in depth
        regs[i] = kAllRedRegister;
        if (!fault_logged) { Serial.println("[FAULT] conflicting lamps requested; forcing all-red"); fault_logged = true; }
      }
    }
    if (memcmp(regs, shown, sizeof regs) != 0) {
      write_chain(hal, regs);                    // one latch: all LEDs change together
      memcpy(shown, regs, sizeof regs);
    }

    if (xSemaphoreTake(g_mu, 0) == pdTRUE) {     // never block signalling on the snapshot lock
      for (int i = 0; i < kIntersections; i++) g_snap[i] = fsm[i].state(now);
      xSemaphoreGive(g_mu);
    }
    esp_task_wdt_reset();
    vTaskDelayUntil(&wake, pdMS_TO_TICKS(20));
  }
}

// One signed request. Returns the HTTP status (<=0 on transport failure). Body (if any) is the response text.
static int signed_request(const char* method, const char* path, const char* body, String* response) {
  char sig[65];
  uint32_t seq = ++g_seq;                        // a retry always uses a NEW seq (the backend rejects replays)
  size_t blen = body ? strlen(body) : 0;
  sign_request(DEVICE_KEY, method, path, DEVICE_ID, g_boot, seq, (const uint8_t*)body, blen, sig);
  HTTPClient http;
  http.setConnectTimeout(1000);
  http.setTimeout(1500);
  http.setReuse(false);
  if (!http.begin(BACKEND_HOST, BACKEND_PORT, path)) return -1;
  http.addHeader("X-Device-Id", DEVICE_ID);
  http.addHeader("X-Boot", g_boot);
  http.addHeader("X-Seq", String(seq));
  http.addHeader("X-Signature", sig);
  http.addHeader("Content-Type", "application/json");
  int code = body ? http.POST((uint8_t*)body, blen) : http.GET();
  if (code == 200 && response) {
    if (http.getSize() > 4096) code = -2;        // refuse unexpectedly large bodies
    else *response = http.getString();
  }
  http.end();
  return code;
}

static void net_task(void*) {
  uint32_t last_wifi_try = 0;
  int last_state_code = 0, last_cmd_code = 0;
  for (;;) {
    uint32_t cycle_start = millis();
    if (WiFi.status() != WL_CONNECTED) {
      if (cycle_start - last_wifi_try > 5000) { last_wifi_try = cycle_start; Serial.println("[net] wifi down, reconnecting"); WiFi.disconnect(); WiFi.begin(WIFI_SSID, WIFI_PASSWORD); }
      vTaskDelay(pdMS_TO_TICKS(500));
      continue;
    }
    DeviceState snap[kIntersections];
    xSemaphoreTake(g_mu, portMAX_DELAY);
    memcpy(snap, g_snap, sizeof snap);
    xSemaphoreGive(g_mu);

    char body[768];
    bool state_ok = false, cmds_ok = false;
    if (build_state_json(body, sizeof body, snap) > 0) {
      int c = signed_request("POST", "/api/device/state", body, nullptr);
      state_ok = c == 200;
      if (c != last_state_code) { Serial.printf("[net] state -> %d\n", c); last_state_code = c; }
    }
    String resp;
    int c = signed_request("GET", "/api/device/commands", nullptr, &resp);
    if (c != last_cmd_code) { Serial.printf("[net] commands -> %d\n", c); last_cmd_code = c; }
    if (c == 200) {
      ParsedCommand cmds[kIntersections * 2];
      int n = parse_commands(resp.c_str(), resp.length(), cmds, kIntersections * 2);
      cmds_ok = n >= 0;
      for (int i = 0; i < n; i++) {
        CmdMsg m = {cmds[i].ok, cmds[i].inter, cmds[i].target, cmds[i].valid_for_ms, cmds[i].seq};
        xQueueSend(g_cmdq, &m, 0);
      }
    }
    if (state_ok && cmds_ok) g_last_ok_ms = millis();

    AckMsg a;
    for (int sent = 0; sent < 6 && xQueueReceive(g_ackq, &a, 0) == pdTRUE; sent++) {
      char ab[128];
      if (build_ack_json(ab, sizeof ab, a.seq, kIntersectionNames[a.inter], a.accepted, a.reason) > 0)
        signed_request("POST", "/api/device/command-ack", ab, nullptr);
    }
    uint32_t spent = millis() - cycle_start;
    vTaskDelay(pdMS_TO_TICKS(spent < 1000 ? 1000 - spent : 10));
  }
}

void setup() {
  Serial.begin(115200);
  init_outputs();
  snprintf(g_boot, sizeof g_boot, "%08x", (unsigned)esp_random());
  Serial.printf("[boot] %s boot=%s outputs enabled in ALL-RED\n", DEVICE_ID, g_boot);

  g_mu = xSemaphoreCreateMutex();
  g_cmdq = xQueueCreate(12, sizeof(CmdMsg));
  g_ackq = xQueueCreate(12, sizeof(AckMsg));
  SignalFsm boot_fsm(kTiming, millis());
  for (int i = 0; i < kIntersections; i++) g_snap[i] = boot_fsm.state(millis());
  g_last_ok_ms = millis();                       // treat boot as "backend ok" for backend_lost_ms, like the simulator

  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  esp_task_wdt_init(3, true);                    // Arduino-ESP32 2.x API; a stuck signal task resets the board
  xTaskCreatePinnedToCore(signal_task, "signal", 4096, nullptr, 5, nullptr, 1);
  xTaskCreatePinnedToCore(net_task, "net", 8192, nullptr, 2, nullptr, 0);
}

void loop() { vTaskDelay(pdMS_TO_TICKS(1000)); }
#endif
