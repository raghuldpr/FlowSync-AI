// Car brain alert logic. Portable C++11 (no Arduino headers) so it is host-tested in firmware/car/test.
// Standalone: no Wi-Fi, no backend. The emergency button only shows a local "demo" alert: nothing is transmitted.
#pragma once
#include <stdint.h>

namespace carlogic {

// ---- tunables (override with -D flags in platformio.ini if the toy car behaves differently) ----
#ifndef HAZARD_DEV_G
#define HAZARD_DEV_G 0.5f      // |acc| differs from 1 g by this much -> bump / pothole
#endif
#ifndef CRASH_G
#define CRASH_G 2.5f           // |acc| at or above this -> crash
#endif
constexpr float kNearCm = 100.0f;      // obstacle alert starts inside this distance
constexpr float kStopCm = 10.0f;       // continuous tone inside this distance
constexpr uint32_t kHazardHoldMs = 2000, kCrashHoldMs = 5000, kEmergencyHoldMs = 5000;
constexpr uint32_t kButtonDebounceMs = 30;

enum Alert : uint8_t { ALERT_NONE = 0, ALERT_OBSTACLE = 1, ALERT_HAZARD = 2, ALERT_EMERGENCY = 3, ALERT_CRASH = 4 };
const char* alert_name(Alert a);

// Time between obstacle beeps for a distance: 0 = silent (no echo or far), 1 = continuous tone.
uint32_t obstacle_beep_interval_ms(float cm);

// Acceleration magnitude in g -> alert level for a single sample.
Alert classify_g(float mag_g);

// Frequency (Hz) the passive buzzer should play right now; 0 = silent.
uint16_t buzzer_hz(Alert a, uint32_t since_ms, float cm);

class Engine {
 public:
  Engine() : alert_(ALERT_NONE), timed_(ALERT_NONE), since_(0), until_(0), btn_(false), btn_raw_(false), btn_change_(0) {}
  // cm < 0 = no echo. pressed = raw button level (true = pressed). mag_g < 0 = no IMU sample.
  void update(uint32_t now_ms, float cm, float mag_g, bool pressed);
  Alert alert() const { return alert_; }
  uint32_t since_ms(uint32_t now_ms) const { return now_ms - since_; }
  float last_cm() const { return cm_; }
  // Fills two 16-character lines (NUL terminated, space padded).
  void lines(char l1[17], char l2[17]) const;

 private:
  void raise(Alert a, uint32_t now_ms, uint32_t hold_ms);
  Alert alert_;          // what is shown / sounded now
  Alert timed_;          // latched hazard / emergency / crash, held until `until_`
  uint32_t since_, until_;
  bool btn_, btn_raw_;
  uint32_t btn_change_;
  float cm_ = -1.0f;
};

}  // namespace carlogic
