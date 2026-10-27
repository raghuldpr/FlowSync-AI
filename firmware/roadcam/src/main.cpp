// FlowSync road camera: ESP32-CAM (AI-Thinker) -> signed JPEG upload to POST /api/device/images.
// No location is sent: the backend locates the frame from the tracked car it rides on.
// Power: the camera + Wi-Fi draw current spikes; use a solid 5 V supply with a 470-1000 uF capacitor at the board's 5 V pin,
// or the board will brown out and reset (brown-out detection is deliberately left ON so a bad supply is visible, not hidden).
#include <Arduino.h>
#include <HTTPClient.h>
#include <WiFi.h>
#include <esp_camera.h>
#include <esp_system.h>
#include <mbedtls/base64.h>

#include "config.h"
#include "signing.h"

using namespace flowsync;

// AI-Thinker ESP32-CAM pin map
#define PWDN_GPIO_NUM 32
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM 0
#define SIOD_GPIO_NUM 26
#define SIOC_GPIO_NUM 27
#define Y9_GPIO_NUM 35
#define Y8_GPIO_NUM 34
#define Y7_GPIO_NUM 39
#define Y6_GPIO_NUM 36
#define Y5_GPIO_NUM 21
#define Y4_GPIO_NUM 19
#define Y3_GPIO_NUM 18
#define Y2_GPIO_NUM 5
#define VSYNC_GPIO_NUM 25
#define HREF_GPIO_NUM 23
#define PCLK_GPIO_NUM 22

static char g_boot[9];
static uint32_t g_seq = 0;

static bool init_camera() {
  camera_config_t c = {};
  c.ledc_channel = LEDC_CHANNEL_0;
  c.ledc_timer = LEDC_TIMER_0;
  c.pin_d0 = Y2_GPIO_NUM; c.pin_d1 = Y3_GPIO_NUM; c.pin_d2 = Y4_GPIO_NUM; c.pin_d3 = Y5_GPIO_NUM;
  c.pin_d4 = Y6_GPIO_NUM; c.pin_d5 = Y7_GPIO_NUM; c.pin_d6 = Y8_GPIO_NUM; c.pin_d7 = Y9_GPIO_NUM;
  c.pin_xclk = XCLK_GPIO_NUM; c.pin_pclk = PCLK_GPIO_NUM; c.pin_vsync = VSYNC_GPIO_NUM; c.pin_href = HREF_GPIO_NUM;
  c.pin_sccb_sda = SIOD_GPIO_NUM; c.pin_sccb_scl = SIOC_GPIO_NUM;
  c.pin_pwdn = PWDN_GPIO_NUM; c.pin_reset = RESET_GPIO_NUM;
  c.xclk_freq_hz = 20000000;
  c.pixel_format = PIXFORMAT_JPEG;
  c.frame_size = FRAMESIZE_QVGA;
  c.jpeg_quality = JPEG_QUALITY;
  c.fb_count = 1;
  return esp_camera_init(&c) == ESP_OK;
}

// Builds {"image_b64":"..."} in one heap buffer, signs it, posts it. Returns the HTTP status (<= 0 on transport failure).
static int upload(const uint8_t* jpg, size_t len) {
  size_t b64cap = 4 * ((len + 2) / 3) + 4;
  const char* head = "{\"image_b64\":\"";
  const char* tail = "\"}";
  size_t hl = strlen(head), tl = strlen(tail);
  char* body = (char*)malloc(hl + b64cap + tl + 1);
  if (!body) return -10;
  memcpy(body, head, hl);
  size_t olen = 0;
  if (mbedtls_base64_encode((unsigned char*)body + hl, b64cap, &olen, jpg, len) != 0) { free(body); return -11; }
  memcpy(body + hl + olen, tail, tl);
  size_t blen = hl + olen + tl;
  body[blen] = 0;

  char sig[65];
  uint32_t seq = ++g_seq;
  sign_request(DEVICE_KEY, "POST", "/api/device/images", DEVICE_ID, g_boot, seq, (const uint8_t*)body, blen, sig);
  HTTPClient http;
  http.setConnectTimeout(2000);
  http.setTimeout(4000);
  http.setReuse(false);
  int code = -1;
  if (http.begin(BACKEND_HOST, BACKEND_PORT, "/api/device/images")) {
    http.addHeader("X-Device-Id", DEVICE_ID);
    http.addHeader("X-Boot", g_boot);
    http.addHeader("X-Seq", String(seq));
    http.addHeader("X-Signature", sig);
    http.addHeader("Content-Type", "application/json");
    code = http.POST((uint8_t*)body, blen);
    http.end();
  }
  free(body);
  return code;
}

void setup() {
  Serial.begin(115200);
  snprintf(g_boot, sizeof g_boot, "%08x", (unsigned)esp_random());
  if (!init_camera()) {
    Serial.println("[FAULT] camera init failed (check the ribbon cable and the power supply)");
    delay(5000);
    ESP.restart();
  }
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.printf("[boot] %s boot=%s\n", DEVICE_ID, g_boot);
}

void loop() {
  static uint32_t last = 0;
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[net] wifi down");
    delay(1000);
    return;
  }
  if (millis() - last < CAPTURE_PERIOD_MS) { delay(50); return; }
  last = millis();
  camera_fb_t* fb = esp_camera_fb_get();
  if (!fb) { Serial.println("[cam] capture failed"); return; }
  int code = upload(fb->buf, fb->len);
  Serial.printf("[cam] %u bytes -> %d\n", (unsigned)fb->len, code);
  esp_camera_fb_return(fb);
}
