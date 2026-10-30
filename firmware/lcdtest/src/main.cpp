// 16x2 I2C LCD diagnostic (PCF8574 backpack). No libraries: talks to the chip directly so
// every failure point is visible on the serial monitor.
//
// Wiring: GND->GND, VCC->5V (VIN pin on ESP32; NOT 3V3), SDA->GPIO16, SCL->GPIO17 (the LCD's own bus on the car, see docs/HARDWARE_CAR_WIRING.md).
#include <Arduino.h>
#include <Wire.h>

#ifndef PIN_SDA
#define PIN_SDA 16
#endif
#ifndef PIN_SCL
#define PIN_SCL 17
#endif

// Standard backpack bit map: P0=RS P1=RW P2=E P3=BL P4..P7=D4..D7
static const uint8_t RS = 0x01, EN = 0x04, BL = 0x08;
static uint8_t addr = 0;

static bool expWrite(uint8_t v) {
  Wire.beginTransmission(addr);
  Wire.write(v);
  return Wire.endTransmission() == 0;
}

static void pulse(uint8_t v) {
  expWrite(v | EN | BL);
  delayMicroseconds(1);
  expWrite((v & ~EN) | BL);
  delayMicroseconds(50);
}

static void write4(uint8_t nib, uint8_t rs) { pulse((nib << 4) | rs); }

static void send(uint8_t b, uint8_t rs) {
  write4(b >> 4, rs);
  write4(b & 0x0F, rs);
}

static void cmd(uint8_t c) { send(c, 0); if (c < 4) delay(3); }

static void lcdInit() {
  delay(50);
  expWrite(BL);
  delay(10);
  // HD44780 4-bit reset sequence
  write4(0x3, 0); delay(5);
  write4(0x3, 0); delay(5);
  write4(0x3, 0); delay(1);
  write4(0x2, 0); delay(1);
  cmd(0x28);  // 4-bit, 2 lines, 5x8 font
  cmd(0x0C);  // display on, cursor off
  cmd(0x06);  // entry mode
  cmd(0x01);  // clear
  delay(3);
}

static void print(uint8_t row, const char *s) {
  cmd(0x80 | (row ? 0x40 : 0x00));
  while (*s) send((uint8_t)*s++, RS);
}

static uint8_t scan() {
  uint8_t first = 0;
  Serial.println("[i2c] scanning...");
  for (uint8_t a = 1; a < 127; a++) {
    Wire.beginTransmission(a);
    if (Wire.endTransmission() == 0) {
      Serial.printf("[i2c] device at 0x%02X\n", a);
      if (!first && ((a >= 0x20 && a <= 0x27) || (a >= 0x38 && a <= 0x3F))) first = a;
    }
  }
  return first;
}

void setup() {
  Serial.begin(115200);
  delay(1500);
  Serial.println("\n=== LCD1602 I2C diagnostic ===");
  Wire.begin(PIN_SDA, PIN_SCL);
  Wire.setClock(100000);

  addr = scan();
  if (!addr) {
    Serial.println("[FAIL] No I2C device found. Backlight on only means power works.");
    Serial.println("  - Check SDA/SCL not swapped, pins match PIN_SDA/PIN_SCL, solder joints on the 4 header pins");
    Serial.println("  - Power the module from 5V, share GND with the ESP32");
    return;
  }
  Serial.printf("[ok] using backpack at 0x%02X\n", addr);
  lcdInit();
  print(0, "LCD TEST OK");
  print(1, "addr 0x");
  char h[8]; snprintf(h, sizeof h, "%02X", addr);
  for (const char *p = h; *p; p++) send((uint8_t)*p, RS);
  Serial.println("Text sent. If the screen still shows nothing / solid blocks:");
  Serial.println("  -> turn the blue contrast potentiometer on the backpack slowly with a screwdriver.");
}

void loop() {
  if (!addr) { delay(2000); setup(); return; }
  static uint32_t n = 0;
  delay(1000);
  char buf[17]; snprintf(buf, sizeof buf, "count %-9lu", (unsigned long)n++);
  print(1, buf);
}
