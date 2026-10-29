// ESP32-CAM (AI-Thinker pinout) diagnostic. Prints sensor model + PSRAM and tests each pixel format.
// Send these results back: they decide how the car camera firmware gets its JPEG.
#include <Arduino.h>
#include <esp_camera.h>
#include <img_converters.h>

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

static const char* sensor_name(uint16_t pid) {
  switch (pid) {
    case 0x26: return "OV2640 (hardware JPEG: expected)";
    case 0x36: return "OV3660 (hardware JPEG)";
    case 0x56: return "OV5640 (hardware JPEG)";
    case 0x76: return "OV7670 (NO JPEG)";
    case 0x77: return "OV7725 (NO JPEG)";
    case 0x9B: return "GC0308 (NO JPEG)";
    case 0x2145: return "GC2145 (NO JPEG)";
    case 0x232A: return "GC032A (NO JPEG)";
    default: return "unknown";
  }
}

static esp_err_t start(pixformat_t fmt, framesize_t size) {
  camera_config_t c = {};
  c.ledc_channel = LEDC_CHANNEL_0; c.ledc_timer = LEDC_TIMER_0;
  c.pin_d0 = Y2_GPIO_NUM; c.pin_d1 = Y3_GPIO_NUM; c.pin_d2 = Y4_GPIO_NUM; c.pin_d3 = Y5_GPIO_NUM;
  c.pin_d4 = Y6_GPIO_NUM; c.pin_d5 = Y7_GPIO_NUM; c.pin_d6 = Y8_GPIO_NUM; c.pin_d7 = Y9_GPIO_NUM;
  c.pin_xclk = XCLK_GPIO_NUM; c.pin_pclk = PCLK_GPIO_NUM; c.pin_vsync = VSYNC_GPIO_NUM; c.pin_href = HREF_GPIO_NUM;
  c.pin_sccb_sda = SIOD_GPIO_NUM; c.pin_sccb_scl = SIOC_GPIO_NUM;
  c.pin_pwdn = PWDN_GPIO_NUM; c.pin_reset = RESET_GPIO_NUM;
  c.xclk_freq_hz = 20000000;
  c.pixel_format = fmt;
  c.frame_size = size;
  c.jpeg_quality = 12;
  c.fb_count = 1;
  c.fb_location = psramFound() ? CAMERA_FB_IN_PSRAM : CAMERA_FB_IN_DRAM;
  c.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
  return esp_camera_init(&c);
}

static void try_format(const char* name, pixformat_t fmt, framesize_t size) {
  Serial.printf("\n--- %s ---\n", name);
  esp_err_t e = start(fmt, size);
  if (e != ESP_OK) { Serial.printf("init FAILED (0x%x)\n", (unsigned)e); esp_camera_deinit(); return; }
  sensor_t* s = esp_camera_sensor_get();
  if (s) Serial.printf("sensor PID=0x%X -> %s\n", (unsigned)s->id.PID, sensor_name(s->id.PID));
  delay(300);
  camera_fb_t* fb = esp_camera_fb_get();
  if (!fb) { Serial.println("capture FAILED"); esp_camera_deinit(); return; }
  Serial.printf("capture OK: %ux%u, %u bytes, format=%d\n", fb->width, fb->height, (unsigned)fb->len, (int)fb->format);
  if (fmt == PIXFORMAT_JPEG) {
    bool magic = fb->len > 3 && fb->buf[0] == 0xFF && fb->buf[1] == 0xD8;
    Serial.printf("JPEG header (FFD8): %s\n", magic ? "yes, valid JPEG" : "NO, not a JPEG");
  } else {
    uint8_t* out = nullptr; size_t olen = 0;
    bool ok = frame2jpg(fb, 80, &out, &olen);
    Serial.printf("software JPEG encode (frame2jpg): %s", ok ? "OK" : "FAILED");
    if (ok) { Serial.printf(" -> %u bytes", (unsigned)olen); free(out); }
    Serial.println();
  }
  esp_camera_fb_return(fb);
  esp_camera_deinit();
}

void setup() {
  Serial.begin(115200);
  delay(1500);
  Serial.println("\n=== ESP32-CAM diagnostic ===");
  Serial.printf("PSRAM: %s (%u bytes)\n", psramFound() ? "YES" : "NO", (unsigned)ESP.getPsramSize());
  Serial.printf("Free heap: %u\n", (unsigned)ESP.getFreeHeap());
  try_format("JPEG QVGA", PIXFORMAT_JPEG, FRAMESIZE_QVGA);
  try_format("RGB565 QQVGA (160x120)", PIXFORMAT_RGB565, FRAMESIZE_QQVGA);
  try_format("GRAYSCALE QVGA", PIXFORMAT_GRAYSCALE, FRAMESIZE_QVGA);
  try_format("YUV422 QQVGA", PIXFORMAT_YUV422, FRAMESIZE_QQVGA);
  Serial.println("\n=== done. Copy everything above and send it back. Press EN to repeat. ===");
}

void loop() { delay(1000); }
