// Host test for the car alert logic. Run: python firmware/car/test/run_host_tests.py
#include <stdio.h>
#include <string.h>
#include "car_logic.h"

using namespace carlogic;

static int fails = 0;
#define CHECK(c, ...) do { if (!(c)) { printf("FAIL line %d: ", __LINE__); printf(__VA_ARGS__); printf("\n"); fails++; } } while (0)

int main() {
  // distance -> beep interval
  CHECK(obstacle_beep_interval_ms(-1) == 0, "no echo must be silent");
  CHECK(obstacle_beep_interval_ms(150) == 0, "far must be silent");
  CHECK(obstacle_beep_interval_ms(5) == 1, "inside stop distance must be continuous");
  CHECK(obstacle_beep_interval_ms(30) < obstacle_beep_interval_ms(80), "closer must beep faster");

  // IMU magnitude -> alert
  CHECK(classify_g(1.0f) == ALERT_NONE && classify_g(1.2f) == ALERT_NONE, "normal gravity is not an alert");
  CHECK(classify_g(1.6f) == ALERT_HAZARD && classify_g(0.3f) == ALERT_HAZARD, "bump / drop is a hazard");
  CHECK(classify_g(3.0f) == ALERT_CRASH, "3 g is a crash");
  CHECK(classify_g(-1.0f) == ALERT_NONE, "no sample is no alert");

  Engine e;
  char l1[17], l2[17];
  uint32_t t = 1000;
  e.update(t, -1, 1.0f, false);
  CHECK(e.alert() == ALERT_NONE, "idle");

  // obstacle follows the live distance
  e.update(t += 20, 60, 1.0f, false);
  CHECK(e.alert() == ALERT_OBSTACLE, "obstacle at 60 cm");
  e.update(t += 20, 300, 1.0f, false);
  CHECK(e.alert() == ALERT_NONE, "obstacle clears when it moves away");

  // hazard latches for its hold time, then clears
  e.update(t += 20, -1, 1.8f, false);
  CHECK(e.alert() == ALERT_HAZARD, "bump -> hazard");
  e.update(t += 500, -1, 1.0f, false);
  CHECK(e.alert() == ALERT_HAZARD, "hazard still held at 500 ms");
  e.update(t += 2000, -1, 1.0f, false);
  CHECK(e.alert() == ALERT_NONE, "hazard cleared after its hold");

  // button: debounced press edge -> emergency, text says it is a local demo and is NOT sent
  e.update(t += 100, -1, 1.0f, true);
  CHECK(e.alert() == ALERT_NONE, "press shorter than the debounce time is ignored");
  e.update(t += 5, -1, 1.0f, false);
  e.update(t += 100, -1, 1.0f, true);
  e.update(t += 40, -1, 1.0f, true);
  CHECK(e.alert() == ALERT_EMERGENCY, "button -> emergency");
  e.lines(l1, l2);
  CHECK(strncmp(l1, "EMERGENCY ALERT ", 16) == 0 && strstr(l2, "demo") != 0, "emergency text '%s' / '%s'", l1, l2);
  CHECK(strlen(l1) == 16 && strlen(l2) == 16, "lines are exactly 16 characters");
  e.update(t += 40, -1, 1.0f, false);

  // hazard must not replace a live emergency; crash must
  e.update(t += 20, -1, 1.8f, false);
  CHECK(e.alert() == ALERT_EMERGENCY, "hazard does not replace emergency");
  e.update(t += 20, -1, 3.2f, false);
  CHECK(e.alert() == ALERT_CRASH, "crash replaces emergency");
  e.lines(l1, l2);
  CHECK(strncmp(l1, "CRASH DETECTED  ", 16) == 0, "crash text '%s'", l1);
  e.update(t += 6000, -1, 1.0f, false);
  CHECK(e.alert() == ALERT_NONE, "everything clears");

  // buzzer patterns: silent when idle, distinct for each alert, never above 4 kHz
  CHECK(buzzer_hz(ALERT_NONE, 0, 50) == 0, "idle is silent");
  bool saw_siren_tones = buzzer_hz(ALERT_EMERGENCY, 0, -1) != buzzer_hz(ALERT_EMERGENCY, 400, -1);
  CHECK(saw_siren_tones, "siren alternates tones");
  for (uint32_t s = 0; s < 3000; s += 7)
    for (int a = 0; a <= ALERT_CRASH; a++) CHECK(buzzer_hz((Alert)a, s, 40) <= 4000, "tone too high");
  CHECK(buzzer_hz(ALERT_OBSTACLE, 3, 5) == 2700, "very close is a continuous tone");

  // 32-bit clock wrap
  Engine w;
  uint32_t u = 0xFFFFFF00u;
  w.update(u, -1, 1.8f, false);
  CHECK(w.alert() == ALERT_HAZARD, "hazard before wrap");
  w.update(u + 0x200u, -1, 1.0f, false);   // 512 ms later, across the wrap
  CHECK(w.alert() == ALERT_HAZARD, "hazard held across the wrap");
  w.update(u + 3000u, -1, 1.0f, false);
  CHECK(w.alert() == ALERT_NONE, "hazard cleared after the wrap");

  if (fails) printf("FAILED: %d\n", fails); else printf("car host tests OK\n");
  return fails ? 1 : 0;
}
