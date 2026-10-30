// FlowSync car brain (ESP32 DevKit), STANDALONE: no Wi-Fi, no backend. Sensors -> LCD + buzzer.
//   HC-SR04 distance  -> obstacle beeps (faster when closer) and the distance on the LCD
//   MPU-9250 |acc|    -> bump / pothole "ROAD HAZARD", crash-level g -> "CRASH DETECTED"
//   Button            -> "EMERGENCY ALERT" (demo: shown and sounded locally, NOT transmitted anywhere)
// Pins: docs/HARDWARE_CAR_WIRING.md. Serial monitor at 115200, type a letter and press Enter:
//   h help | i I2C scan | s stream on/off | b beep | z tone sweep / buzzer polarity | l LCD test | p reset peak-g
// Build flavours (platformio.ini): `car` = run mode, `cartest` = same plus the sensor stream on at boot.
#include <Arduino.h>
#include <Wire.h>
#include <math.h>
#include "car_logic.h"

// ---- pins ----
static const int PIN_SDA = 21, PIN_SCL = 22;          // MPU-9250 (Wire)
static const int PIN_LCD_SDA = 16, PIN_LCD_SCL = 17;  // LCD backpack on its own bus (Wire1): it pulls up to 5 V
static const int PIN_TRIG = 25, PIN_ECHO = 34;
static const int PIN_BTN = 32;
static const int PIN_BUZZER = 13;

// ---- buzzer type: the sounder in this build is PASSIVE (needs a tone). Set 0 for an active buzzer module ----
#ifndef BUZZER_PASSIVE
#define BUZZER_PASSIVE 1
#endif
#ifndef BUZZER_ACTIVE_LOW
#define BUZZER_ACTIVE_LOW 0    // active module only: 1 = sounds when the pin is LOW
#endif
#ifndef STREAM_AT_BOOT
#define STREAM_AT_BOOT 0
#endif
static const int BUZ_CH = 0;

// ---- MPU registers ----
static const uint8_t MPU_ADDR = 0x68;
static const uint8_t REG_WHO = 0x75, REG_PWR1 = 0x6B, REG_GYRO_CFG = 0x1B, REG_ACC_CFG = 0x1C;
static const uint8_t REG_INT_PIN = 0x37, REG_ACC_OUT = 0x3B;
static const uint8_t MAG_ADDR = 0x0C;

static bool g_mpu_ok = false, g_mag_ok = false, g_stream = STREAM_AT_BOOT;
static float g_peak_g = 0;
static carlogic::Engine g_engine;

static bool wr(TwoWire& w, uint8_t addr, uint8_t reg, uint8_t val) {
  w.beginTransmission(addr); w.write(reg); w.write(val);
  return w.endTransmission() == 0;
}
static bool rd(TwoWire& w, uint8_t addr, uint8_t reg, uint8_t* buf, uint8_t n) {
  w.beginTransmission(addr); w.write(reg);
  if (w.endTransmission(false) != 0) return false;
  if (w.requestFrom((int)addr, (int)n) != n) return false;
  for (uint8_t i = 0; i < n; i++) buf[i] = w.read();
  return true;
}

static void i2c_scan(TwoWire& w, const char* name) {
  Serial.printf("[i2c] scanning %s...\n", name);
  int found = 0;
  for (uint8_t a = 1; a < 127; a++) {
    w.beginTransmission(a);
    if (w.endTransmission() == 0) { Serial.printf("[i2c] %s: device at 0x%02X\n", name, a); found++; }
  }
  if (!found) Serial.printf("[i2c] %s: NOTHING found, check SDA/SCL, power and GND\n", name);
}

// ---------------------------------------------------------------------------------------------------------------------
// 16x2 LCD behind a PCF8574 backpack (RS=P0 RW=P1 E=P2 BL=P3 D4..D7=P4..P7). Minimal driver, no library needed.
static uint8_t g_lcd_addr = 0;
static uint8_t g_lcd_bl = 0x08;
static void lcd_raw(uint8_t v) { Wire1.beginTransmission(g_lcd_addr); Wire1.write(v | g_lcd_bl); Wire1.endTransmission(); }
static void lcd_nibble(uint8_t nib, bool rs) {
  uint8_t v = (uint8_t)((nib << 4) | (rs ? 1 : 0));
  lcd_raw(v); lcd_raw(v | 0x04); delayMicroseconds(1); lcd_raw(v & ~0x04); delayMicroseconds(60);
}
static void lcd_byte(uint8_t b, bool rs) { lcd_nibble(b >> 4, rs); lcd_nibble(b & 0x0F, rs); }
static void lcd_cmd(uint8_t c) { lcd_byte(c, false); if (c <= 0x03) delay(2); }
static bool lcd_init() {
  g_lcd_addr = 0;
  const uint8_t tries[2] = {0x27, 0x3F};
  for (int i = 0; i < 2; i++) {
    Wire1.beginTransmission(tries[i]);
    if (Wire1.endTransmission() == 0) { g_lcd_addr = tries[i]; break; }
  }
  if (!g_lcd_addr) return false;
  delay(50);
  lcd_nibble(0x03, false); delay(5);
  lcd_nibble(0x03, false); delay(5);
  lcd_nibble(0x03, false); delayMicroseconds(150);
  lcd_nibble(0x02, false);                 // 4-bit mode
  lcd_cmd(0x28);                           // 2 lines, 5x8
  lcd_cmd(0x0C);                           // display on, no cursor
  lcd_cmd(0x06);                           // entry mode
  lcd_cmd(0x01);                           // clear
  return true;
}
static void lcd_line(uint8_t row, const char* s16) {
  if (!g_lcd_addr) return;
  lcd_cmd(0x80 | (row ? 0x40 : 0x00));
  for (int i = 0; i < 16; i++) lcd_byte((uint8_t)s16[i], true);
}
static void lcd_show(const char* a, const char* b) {
  char l1[17], l2[17];
  snprintf(l1, sizeof l1, "%-16.16s", a);
  snprintf(l2, sizeof l2, "%-16.16s", b);
  lcd_line(0, l1); lcd_line(1, l2);
}

// ---------------------------------------------------------------------------------------------------------------------
// buzzer
static void buzzer_setup() {
#if BUZZER_PASSIVE
  ledcSetup(BUZ_CH, 2700, 8);
  ledcAttachPin(PIN_BUZZER, BUZ_CH);
  ledcWriteTone(BUZ_CH, 0);
#else
  pinMode(PIN_BUZZER, OUTPUT);
  digitalWrite(PIN_BUZZER, BUZZER_ACTIVE_LOW ? HIGH : LOW);
#endif
}
static void buzzer_hz(uint16_t hz) {
#if BUZZER_PASSIVE
  ledcWriteTone(BUZ_CH, hz);                // 0 = silent
#else
  digitalWrite(PIN_BUZZER, (hz != 0) ^ BUZZER_ACTIVE_LOW ? HIGH : LOW);
#endif
}

// ---------------------------------------------------------------------------------------------------------------------
// MPU-9250 (or 6500/6050)
static const char* who_name(uint8_t w) {
  switch (w) {
    case 0x71: return "MPU-9250 (genuine)";
    case 0x73: return "MPU-9255";
    case 0x70: return "MPU-6500 (no magnetometer)";
    case 0x68: return "MPU-6050";
    default: return "UNKNOWN chip";
  }
}
static void mpu_init() {
  uint8_t w = 0;
  if (!rd(Wire, MPU_ADDR, REG_WHO, &w, 1)) { Serial.println("[mpu] FAIL: no reply at 0x68 (AD0 to GND? check SDA 21 / SCL 22, 3V3, GND)"); return; }
  Serial.printf("[mpu] WHO_AM_I = 0x%02X -> %s\n", w, who_name(w));
  wr(Wire, MPU_ADDR, REG_PWR1, 0x00);        // wake, internal oscillator
  delay(100);
  wr(Wire, MPU_ADDR, REG_ACC_CFG, 0x18);     // +-16 g (2048 LSB/g) so a crash does not clip
  wr(Wire, MPU_ADDR, REG_GYRO_CFG, 0x08);    // +-500 dps (65.5 LSB/dps)
  g_mpu_ok = true;
  if (w == 0x71 || w == 0x73) {
    wr(Wire, MPU_ADDR, REG_INT_PIN, 0x02);   // bypass: magnetometer appears on the main I2C bus
    delay(10);
    uint8_t id = 0;
    if (rd(Wire, MAG_ADDR, 0x00, &id, 1) && id == 0x48) {
      wr(Wire, MAG_ADDR, 0x0A, 0x00); delay(10);
      wr(Wire, MAG_ADDR, 0x0A, 0x16);        // 16-bit, continuous 100 Hz
      g_mag_ok = true;
      Serial.println("[mag] AK8963 found (WIA=0x48)");
    } else {
      Serial.println("[mag] AK8963 NOT found (a 6500 sold as a 9250?). Not needed: alerts use only the accelerometer.");
    }
  }
}
struct Imu { float ax, ay, az, gx, gy, gz; };
static bool mpu_read(Imu& o) {
  uint8_t b[14];
  if (!rd(Wire, MPU_ADDR, REG_ACC_OUT, b, 14)) return false;
  auto s16 = [&](int i) { return (int16_t)((b[i] << 8) | b[i + 1]); };
  o.ax = s16(0) / 2048.0f; o.ay = s16(2) / 2048.0f; o.az = s16(4) / 2048.0f;   // g
  o.gx = s16(8) / 65.5f;  o.gy = s16(10) / 65.5f;  o.gz = s16(12) / 65.5f;      // deg/s
  return true;
}
static bool mag_read(float& x, float& y, float& z) {
  uint8_t st = 0;
  if (!rd(Wire, MAG_ADDR, 0x02, &st, 1) || !(st & 1)) return false;
  uint8_t b[7];
  if (!rd(Wire, MAG_ADDR, 0x03, b, 7)) return false;
  if (b[6] & 0x08) return false;
  x = (int16_t)(b[0] | (b[1] << 8)) * 0.15f;
  y = (int16_t)(b[2] | (b[3] << 8)) * 0.15f;
  z = (int16_t)(b[4] | (b[5] << 8)) * 0.15f;
  return true;
}

static float read_distance_cm() {
  digitalWrite(PIN_TRIG, LOW); delayMicroseconds(4);
  digitalWrite(PIN_TRIG, HIGH); delayMicroseconds(10);
  digitalWrite(PIN_TRIG, LOW);
  unsigned long us = pulseIn(PIN_ECHO, HIGH, 30000UL);  // 30 ms ~ 5 m
  if (us == 0) return -1.0f;
  return us * 0.0343f / 2.0f;
}

// ---------------------------------------------------------------------------------------------------------------------
// bring-up helpers (serial commands)
static void beep_test() {
  Serial.println("[buzzer] 3 beeps");
  for (int i = 0; i < 3; i++) { buzzer_hz(2700); delay(150); buzzer_hz(0); delay(150); }
}
static void tone_test() {
#if BUZZER_PASSIVE
  Serial.println("[buzzer] passive: sweep 1000, 2000, 2700, 3500 Hz, 0.7 s each. Silent or only clicks? Then it is an ACTIVE buzzer: build with -DBUZZER_PASSIVE=0.");
  const uint16_t f[4] = {1000, 2000, 2700, 3500};
  for (int i = 0; i < 4; i++) { Serial.printf("[buzzer] %u Hz\n", f[i]); buzzer_hz(f[i]); delay(700); }
  buzzer_hz(0);
#else
  Serial.println("[buzzer] active module polarity test. A: pin HIGH 400 ms, then B: pin LOW 400 ms.");
  digitalWrite(PIN_BUZZER, HIGH); delay(400); digitalWrite(PIN_BUZZER, LOW); delay(600);
  digitalWrite(PIN_BUZZER, LOW); delay(400);
  Serial.println("[buzzer] sounded during A: BUZZER_ACTIVE_LOW 0 (default). Only during B: set BUZZER_ACTIVE_LOW 1.");
  buzzer_hz(0);
#endif
}
static void lcd_test() {
  if (!g_lcd_addr) { Serial.println("[lcd] not found on Wire1 (SDA 16 / SCL 17, VIN 5 V, GND). Run 'i'."); return; }
  Serial.printf("[lcd] backpack at 0x%02X: writing text. Blank squares only? turn the blue contrast pot on the backpack.\n", g_lcd_addr);
  lcd_show("FlowSync car", "LCD test OK");
  delay(1500);
  lcd_show("0123456789ABCDEF", "abcdefghijklmnop");
  delay(1500);
}
static void help() {
  Serial.println("commands: h help | i I2C scan | s stream on/off | b beep | z tone sweep | l LCD test | p reset peak-g");
}

static void stream_line(float mag, float cm, bool btn) {
  Imu m;
  char buf[260];
  int n = 0;
  if (g_mpu_ok && mpu_read(m)) {
    float roll = atan2f(m.ay, m.az) * 57.2958f;
    float pitch = atan2f(-m.ax, sqrtf(m.ay * m.ay + m.az * m.az)) * 57.2958f;
    n += snprintf(buf + n, sizeof buf - n, "acc[g] %+.2f %+.2f %+.2f |a|=%.2f peak=%.2f | gyro[dps] %+.0f %+.0f %+.0f | roll %+.0f pitch %+.0f",
                  m.ax, m.ay, m.az, mag, g_peak_g, m.gx, m.gy, m.gz, roll, pitch);
  } else {
    n += snprintf(buf + n, sizeof buf - n, "MPU: no data");
  }
  float mx, my, mz;
  if (g_mag_ok && mag_read(mx, my, mz)) {
    float heading = atan2f(my, mx) * 57.2958f; if (heading < 0) heading += 360;
    n += snprintf(buf + n, sizeof buf - n, " | mag[uT] %.0f %.0f %.0f hdg %.0f", mx, my, mz, heading);
  }
  if (cm < 0) n += snprintf(buf + n, sizeof buf - n, " | dist: no echo");
  else n += snprintf(buf + n, sizeof buf - n, " | dist %.1f cm", cm);
  n += snprintf(buf + n, sizeof buf - n, " | btn %s | alert %s", btn ? "PRESSED" : "-", carlogic::alert_name(g_engine.alert()));
  Serial.println(buf);
}

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n=== FlowSync car brain (standalone) ===");
  pinMode(PIN_TRIG, OUTPUT); digitalWrite(PIN_TRIG, LOW);
  pinMode(PIN_ECHO, INPUT);
  pinMode(PIN_BTN, INPUT_PULLUP);
  buzzer_setup();
  Wire.begin(PIN_SDA, PIN_SCL);
  Wire.setClock(100000);
  Wire1.begin(PIN_LCD_SDA, PIN_LCD_SCL);
  Wire1.setClock(100000);
  i2c_scan(Wire, "MPU bus (21/22)");
  i2c_scan(Wire1, "LCD bus (16/17)");
  mpu_init();
  if (lcd_init()) { Serial.printf("[lcd] backpack at 0x%02X\n", g_lcd_addr); lcd_show("FlowSync car", "starting..."); }
  else Serial.println("[lcd] not found (0x27 / 0x3F). The car still runs; alerts go to the buzzer and serial only.");
  float d = read_distance_cm();
  Serial.printf("[ultrasonic] first reading: %s\n", d < 0 ? "no echo (point it at a wall 10-100 cm away; check the ECHO divider)" : String(d, 1).c_str());
  Serial.printf("[button] idle level = %s (expected HIGH)\n", digitalRead(PIN_BTN) ? "HIGH" : "LOW (check wiring)");
  help();
  beep_test();
}

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    switch (c) {
      case 'h': help(); break;
      case 'i': i2c_scan(Wire, "MPU bus (21/22)"); i2c_scan(Wire1, "LCD bus (16/17)"); break;
      case 's': g_stream = !g_stream; Serial.printf("[stream] %s\n", g_stream ? "on" : "off"); break;
      case 'b': beep_test(); break;
      case 'z': tone_test(); break;
      case 'l': lcd_test(); break;
      case 'p': g_peak_g = 0; Serial.println("[peak] reset"); break;
      default: break;
    }
  }

  static uint32_t t_imu = 0, t_dist = 0, t_lcd = 0, t_stream = 0;
  static float mag = -1.0f, cm = -1.0f;
  uint32_t now = millis();
  if (now - t_imu >= 20) {
    t_imu = now;
    Imu m;
    if (g_mpu_ok && mpu_read(m)) {
      mag = sqrtf(m.ax * m.ax + m.ay * m.ay + m.az * m.az);
      if (mag > g_peak_g) g_peak_g = mag;
    } else {
      mag = -1.0f;
    }
  }
  if (now - t_dist >= 100) { t_dist = now; cm = read_distance_cm(); }

  bool btn = digitalRead(PIN_BTN) == LOW;
  carlogic::Alert before = g_engine.alert();
  g_engine.update(now, cm, mag, btn);
  if (g_engine.alert() != before) Serial.printf("[alert] %s\n", carlogic::alert_name(g_engine.alert()));
  buzzer_hz(carlogic::buzzer_hz(g_engine.alert(), g_engine.since_ms(now), cm));

  static char last1[17] = "", last2[17] = "";
  if (now - t_lcd >= 150) {
    t_lcd = now;
    char l1[17], l2[17];
    g_engine.lines(l1, l2);
    if (strcmp(l1, last1) || strcmp(l2, last2)) { lcd_line(0, l1); lcd_line(1, l2); strcpy(last1, l1); strcpy(last2, l2); }
  }
  if (g_stream && now - t_stream >= 300) { t_stream = now; stream_line(mag, cm, btn); }
}
