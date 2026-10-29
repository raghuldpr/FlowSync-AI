// FlowSync car brain: BRING-UP TEST. Checks MPU-9250 (or 6500/6050), HC-SR04, button, RGB LED and buzzer. No Wi-Fi.
// Open the serial monitor at 115200. Type a command letter and press Enter:
//   h help   i I2C scan   s stream on/off   c cycle RGB   b beep   z buzzer polarity test   p peak-g reset
#include <Arduino.h>
#include <Wire.h>
#include <math.h>

// ---- pins (docs/HARDWARE_CAR_WIRING.md) ----
static const int PIN_SDA = 21, PIN_SCL = 22;
static const int PIN_TRIG = 25, PIN_ECHO = 34;
static const int PIN_BTN = 32;
static const int PIN_RED = 26, PIN_BLUE = 27;
static const int PIN_BUZZER = 13;

// ---- polarity settings: flip if your parts behave the opposite way ----
#define RGB_COMMON_ANODE 0     // 0 = common cathode (longest leg to GND): HIGH lights the colour
#define BUZZER_ACTIVE_LOW 0    // 1 = buzzer module sounds when the pin is LOW (use the 'z' test to find out)

// ---- MPU registers ----
static const uint8_t MPU_ADDR = 0x68;
static const uint8_t REG_WHO = 0x75, REG_PWR1 = 0x6B, REG_GYRO_CFG = 0x1B, REG_ACC_CFG = 0x1C;
static const uint8_t REG_INT_PIN = 0x37, REG_ACC_OUT = 0x3B;
static const uint8_t MAG_ADDR = 0x0C;

static bool g_mpu_ok = false, g_mag_ok = false, g_stream = true;
static uint8_t g_who = 0;
static float g_peak_g = 0;

static bool wr(uint8_t addr, uint8_t reg, uint8_t val) {
  Wire.beginTransmission(addr); Wire.write(reg); Wire.write(val);
  return Wire.endTransmission() == 0;
}
static bool rd(uint8_t addr, uint8_t reg, uint8_t* buf, uint8_t n) {
  Wire.beginTransmission(addr); Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom((int)addr, (int)n) != n) return false;
  for (uint8_t i = 0; i < n; i++) buf[i] = Wire.read();
  return true;
}

static void i2c_scan() {
  Serial.println("[i2c] scanning...");
  int found = 0;
  for (uint8_t a = 1; a < 127; a++) {
    Wire.beginTransmission(a);
    if (Wire.endTransmission() == 0) { Serial.printf("[i2c] device at 0x%02X\n", a); found++; }
  }
  if (!found) Serial.println("[i2c] NOTHING found: check SDA(21)/SCL(22), VCC(3V3), GND");
}

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
  if (!rd(MPU_ADDR, REG_WHO, &w, 1)) { Serial.println("[mpu] FAIL: no reply at 0x68 (try AD0 to GND, check wiring)"); return; }
  g_who = w;
  Serial.printf("[mpu] WHO_AM_I = 0x%02X -> %s\n", w, who_name(w));
  wr(MPU_ADDR, REG_PWR1, 0x00);        // wake, internal oscillator
  delay(100);
  wr(MPU_ADDR, REG_ACC_CFG, 0x18);     // +-16 g (2048 LSB/g) so a ~2.5 g crash does not clip
  wr(MPU_ADDR, REG_GYRO_CFG, 0x08);    // +-500 dps (65.5 LSB/dps)
  g_mpu_ok = true;
  if (w == 0x71 || w == 0x73) {
    wr(MPU_ADDR, REG_INT_PIN, 0x02);   // bypass: magnetometer appears on the main I2C bus
    delay(10);
    uint8_t id = 0;
    if (rd(MAG_ADDR, 0x00, &id, 1) && id == 0x48) {
      wr(MAG_ADDR, 0x0A, 0x00); delay(10);
      wr(MAG_ADDR, 0x0A, 0x16);        // 16-bit, continuous 100 Hz
      g_mag_ok = true;
      Serial.println("[mag] AK8963 found (WIA=0x48)");
    } else {
      Serial.println("[mag] AK8963 NOT found (board may be a 6500 sold as a 9250). The car works without it.");
    }
  } else {
    Serial.println("[mag] none on this chip (not needed for crash detection)");
  }
}

struct Imu { float ax, ay, az, gx, gy, gz; };
static bool mpu_read(Imu& o) {
  uint8_t b[14];
  if (!rd(MPU_ADDR, REG_ACC_OUT, b, 14)) return false;
  auto s16 = [&](int i) { return (int16_t)((b[i] << 8) | b[i + 1]); };
  o.ax = s16(0) / 2048.0f; o.ay = s16(2) / 2048.0f; o.az = s16(4) / 2048.0f;   // g
  o.gx = s16(8) / 65.5f;  o.gy = s16(10) / 65.5f;  o.gz = s16(12) / 65.5f;      // deg/s
  return true;
}
static bool mag_read(float& x, float& y, float& z) {
  uint8_t st = 0;
  if (!rd(MAG_ADDR, 0x02, &st, 1) || !(st & 1)) return false;
  uint8_t b[7];
  if (!rd(MAG_ADDR, 0x03, b, 7)) return false;
  if (b[6] & 0x08) return false;                       // overflow
  x = (int16_t)(b[0] | (b[1] << 8)) * 0.15f;           // uT (little endian)
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

static void rgb(bool r, bool b) {
  digitalWrite(PIN_RED, (r ^ RGB_COMMON_ANODE) ? HIGH : LOW);
  digitalWrite(PIN_BLUE, (b ^ RGB_COMMON_ANODE) ? HIGH : LOW);
}
static void buzzer(bool on) {
  digitalWrite(PIN_BUZZER, (on ^ BUZZER_ACTIVE_LOW) ? HIGH : LOW);
}

static void rgb_cycle() {
  Serial.println("[rgb] RED 1 s");   rgb(true, false);  delay(1000);
  Serial.println("[rgb] BLUE 1 s");  rgb(false, true);  delay(1000);
  Serial.println("[rgb] BOTH (magenta) 1 s"); rgb(true, true); delay(1000);
  Serial.println("[rgb] siren flash x6");
  for (int i = 0; i < 6; i++) { rgb(true, false); delay(150); rgb(false, true); delay(150); }
  rgb(false, false);
  Serial.println("[rgb] done. Wrong colour? swap pins 26/27. Nothing lights? check the longest leg goes to GND, or set RGB_COMMON_ANODE 1.");
}

static void beep_test() {
  Serial.println("[buzzer] 3 beeps");
  for (int i = 0; i < 3; i++) { buzzer(true); delay(150); buzzer(false); delay(150); }
}

static void polarity_test() {
  Serial.println("[buzzer] polarity test. Listen carefully.");
  Serial.println("[buzzer] A: pin HIGH for 400 ms ...");
  digitalWrite(PIN_BUZZER, HIGH); delay(400); digitalWrite(PIN_BUZZER, LOW); delay(600);
  Serial.println("[buzzer] B: pin LOW for 400 ms ...");
  digitalWrite(PIN_BUZZER, LOW); delay(400);
  digitalWrite(PIN_BUZZER, !BUZZER_ACTIVE_LOW ? LOW : HIGH);
  Serial.println("[buzzer] If it sounded during A: BUZZER_ACTIVE_LOW 0 (current default). If during B only: set BUZZER_ACTIVE_LOW 1.");
  buzzer(false);
}

static void help() {
  Serial.println("commands: h help | i I2C scan | s stream on/off | c cycle RGB | b beep | z buzzer polarity | p reset peak-g");
}

static void stream_line() {
  Imu m;
  char buf[260];
  int n = 0;
  if (g_mpu_ok && mpu_read(m)) {
    float mag = sqrtf(m.ax * m.ax + m.ay * m.ay + m.az * m.az);
    if (mag > g_peak_g) g_peak_g = mag;
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
  float d = read_distance_cm();
  if (d < 0) n += snprintf(buf + n, sizeof buf - n, " | dist: no echo");
  else n += snprintf(buf + n, sizeof buf - n, " | dist %.1f cm", d);
  n += snprintf(buf + n, sizeof buf - n, " | btn %s", digitalRead(PIN_BTN) == LOW ? "PRESSED" : "-");
  Serial.println(buf);
}

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n=== FlowSync car bring-up test ===");
  pinMode(PIN_TRIG, OUTPUT); digitalWrite(PIN_TRIG, LOW);
  pinMode(PIN_ECHO, INPUT);
  pinMode(PIN_BTN, INPUT_PULLUP);
  pinMode(PIN_RED, OUTPUT); pinMode(PIN_BLUE, OUTPUT);
  pinMode(PIN_BUZZER, OUTPUT);
  rgb(false, false); buzzer(false);
  Wire.begin(PIN_SDA, PIN_SCL);
  Wire.setClock(100000);
  i2c_scan();
  mpu_init();
  float d = read_distance_cm();
  Serial.printf("[ultrasonic] first reading: %s\n", d < 0 ? "no echo (point it at a wall 10-100 cm away; check ECHO divider)" : String(d, 1).c_str());
  Serial.printf("[button] idle level = %s (expected HIGH)\n", digitalRead(PIN_BTN) ? "HIGH" : "LOW (check wiring)");
  help();
  Serial.println("Streaming. Tilt the board, wave a hand in front of the sensor, press the button.");
}

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    switch (c) {
      case 'h': help(); break;
      case 'i': i2c_scan(); break;
      case 's': g_stream = !g_stream; Serial.printf("[stream] %s\n", g_stream ? "on" : "off"); break;
      case 'c': rgb_cycle(); break;
      case 'b': beep_test(); break;
      case 'z': polarity_test(); break;
      case 'p': g_peak_g = 0; Serial.println("[peak] reset"); break;
      default: break;
    }
  }
  static bool last_btn = false;
  bool btn = digitalRead(PIN_BTN) == LOW;
  if (btn != last_btn) {
    delay(20);
    btn = digitalRead(PIN_BTN) == LOW;
    if (btn != last_btn) { Serial.printf("[button] %s\n", btn ? "PRESSED" : "released"); last_btn = btn; }
  }
  static uint32_t last = 0;
  if (g_stream && millis() - last >= 300) { last = millis(); stream_line(); }
}
