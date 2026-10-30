// ESP32-CAM (AI-Thinker) STANDALONE road camera: it opens its own Wi-Fi network and serves a small page with a photo button.
// No router, no backend. Join the Wi-Fi "FlowSync-CAM" (password flowsync1) on a phone or laptop and open http://192.168.4.1/
//   /            page with the latest photo and a "Take photo" button
//   /capture.jpg a fresh JPEG
// Serial (115200) prints the sensor model and every capture. This is the camera that would look at the road for potholes;
// the pothole DETECTION runs in the laptop software on saved photos, it is not wired to this board.
#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <esp_camera.h>
#include "camera_pins.h"

static WebServer server(80);
static bool g_cam_ok = false;
static uint32_t g_shots = 0;

static bool camera_start() {
  camera_config_t c = {};
  c.ledc_channel = LEDC_CHANNEL_0; c.ledc_timer = LEDC_TIMER_0;
  c.pin_d0 = Y2_GPIO_NUM; c.pin_d1 = Y3_GPIO_NUM; c.pin_d2 = Y4_GPIO_NUM; c.pin_d3 = Y5_GPIO_NUM;
  c.pin_d4 = Y6_GPIO_NUM; c.pin_d5 = Y7_GPIO_NUM; c.pin_d6 = Y8_GPIO_NUM; c.pin_d7 = Y9_GPIO_NUM;
  c.pin_xclk = XCLK_GPIO_NUM; c.pin_pclk = PCLK_GPIO_NUM; c.pin_vsync = VSYNC_GPIO_NUM; c.pin_href = HREF_GPIO_NUM;
  c.pin_sccb_sda = SIOD_GPIO_NUM; c.pin_sccb_scl = SIOC_GPIO_NUM;
  c.pin_pwdn = PWDN_GPIO_NUM; c.pin_reset = RESET_GPIO_NUM;
  c.xclk_freq_hz = 20000000;
  c.pixel_format = PIXFORMAT_JPEG;
  bool psram = psramFound();
  c.frame_size = psram ? FRAMESIZE_VGA : FRAMESIZE_QVGA;   // 640x480 with PSRAM, else 320x240
  c.jpeg_quality = 12;
  c.fb_count = 1;
  c.fb_location = psram ? CAMERA_FB_IN_PSRAM : CAMERA_FB_IN_DRAM;
  c.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
  esp_err_t e = esp_camera_init(&c);
  if (e != ESP_OK) { Serial.printf("[cam] init FAILED (0x%x): check the ribbon cable and power (5 V, 2 A)\n", (unsigned)e); return false; }
  sensor_t* s = esp_camera_sensor_get();
  if (s) Serial.printf("[cam] sensor PID=0x%X (0x26 = OV2640, expected)\n", (unsigned)s->id.PID);
  return true;
}

static void handle_root() {
  String h = "<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'><title>FlowSync road camera</title>"
             "<body style='font-family:sans-serif;margin:16px'><h2>FlowSync road camera</h2>"
             "<p>Standalone demo camera. Photos stay on this page; nothing is uploaded.</p>"
             "<img id=p src='/capture.jpg' style='max-width:100%;border:1px solid #888'><p>"
             "<button style='font-size:1.2em;padding:8px 16px' onclick=\"document.getElementById('p').src='/capture.jpg?'+Date.now()\">Take photo</button></p></body>";
  server.send(200, "text/html", h);
}

static void handle_capture() {
  if (!g_cam_ok) { server.send(503, "text/plain", "camera not available"); return; }
  camera_fb_t* fb = esp_camera_fb_get();
  if (!fb) { server.send(503, "text/plain", "capture failed"); Serial.println("[cam] capture FAILED"); return; }
  g_shots++;
  Serial.printf("[cam] photo %u: %ux%u, %u bytes\n", (unsigned)g_shots, fb->width, fb->height, (unsigned)fb->len);
  server.sendHeader("Cache-Control", "no-store");
  server.send_P(200, "image/jpeg", (const char*)fb->buf, fb->len);
  esp_camera_fb_return(fb);
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println("\n=== FlowSync ESP32-CAM standalone web camera ===");
  Serial.printf("PSRAM: %s (%u bytes)\n", psramFound() ? "YES" : "NO", (unsigned)ESP.getPsramSize());
  g_cam_ok = camera_start();
  WiFi.mode(WIFI_AP);
  bool ok = WiFi.softAP("FlowSync-CAM", "flowsync1");
  Serial.printf("[wifi] access point %s, address %s\n", ok ? "up: FlowSync-CAM / flowsync1" : "FAILED", WiFi.softAPIP().toString().c_str());
  server.on("/", handle_root);
  server.on("/capture.jpg", handle_capture);
  server.begin();
  Serial.println("Open http://192.168.4.1/ after joining the Wi-Fi. Reset the board (EN) to restart.");
}

void loop() { server.handleClient(); }
