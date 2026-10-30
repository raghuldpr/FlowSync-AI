#include "car_logic.h"
#include <stdio.h>
#include <string.h>

namespace carlogic {

const char* alert_name(Alert a) {
  switch (a) {
    case ALERT_OBSTACLE: return "OBSTACLE";
    case ALERT_HAZARD: return "HAZARD";
    case ALERT_EMERGENCY: return "EMERGENCY";
    case ALERT_CRASH: return "CRASH";
    default: return "NONE";
  }
}

uint32_t obstacle_beep_interval_ms(float cm) {
  if (cm < 0.0f || cm > kNearCm) return 0;
  if (cm <= kStopCm) return 1;
  // 10 cm -> 120 ms between beeps, 100 cm -> 700 ms
  return (uint32_t)(120.0f + (cm - kStopCm) * (580.0f / (kNearCm - kStopCm)));
}

Alert classify_g(float mag_g) {
  if (mag_g < 0.0f) return ALERT_NONE;
  if (mag_g >= CRASH_G) return ALERT_CRASH;
  float dev = mag_g > 1.0f ? mag_g - 1.0f : 1.0f - mag_g;
  return dev >= HAZARD_DEV_G ? ALERT_HAZARD : ALERT_NONE;
}

uint16_t buzzer_hz(Alert a, uint32_t since_ms, float cm) {
  switch (a) {
    case ALERT_OBSTACLE: {
      uint32_t iv = obstacle_beep_interval_ms(cm);
      if (iv == 0) return 0;
      if (iv == 1) return 2700;
      return (since_ms % (iv + 60)) < 60 ? 2700 : 0;                 // 60 ms beep every `iv` ms
    }
    case ALERT_HAZARD:                                               // two short beeps
      { uint32_t p = since_ms % 400; return (p < 100 || (p >= 200 && p < 300)) ? 2700 : 0; }
    case ALERT_EMERGENCY:                                            // two-tone siren, 300 ms per tone
      return (since_ms / 300) % 2 == 0 ? 2700 : 1900;
    case ALERT_CRASH:                                                // fast alternating, harsher than the siren
      return (since_ms / 120) % 2 == 0 ? 3200 : 1500;
    default: return 0;
  }
}

void Engine::raise(Alert a, uint32_t now_ms, uint32_t hold_ms) {
  bool live = timed_ != ALERT_NONE && (int32_t)(now_ms - until_) < 0;
  if (live && a < timed_) return;                                    // never replace a higher live alert
  timed_ = a;
  until_ = now_ms + hold_ms;
}

void Engine::update(uint32_t now_ms, float cm, float mag_g, bool pressed) {
  cm_ = cm;
  // debounce the button, act on the press edge
  if (pressed != btn_raw_) { btn_raw_ = pressed; btn_change_ = now_ms; }
  if (btn_raw_ != btn_ && (uint32_t)(now_ms - btn_change_) >= kButtonDebounceMs) {
    btn_ = btn_raw_;
    if (btn_) raise(ALERT_EMERGENCY, now_ms, kEmergencyHoldMs);
  }

  Alert g = classify_g(mag_g);
  if (g == ALERT_CRASH) raise(ALERT_CRASH, now_ms, kCrashHoldMs);
  else if (g == ALERT_HAZARD) raise(ALERT_HAZARD, now_ms, kHazardHoldMs);

  if (timed_ != ALERT_NONE && (int32_t)(now_ms - until_) >= 0) timed_ = ALERT_NONE;
  bool near = cm >= 0.0f && cm <= kNearCm;                            // the obstacle alert follows the live distance
  Alert want = timed_ != ALERT_NONE ? timed_ : (near ? ALERT_OBSTACLE : ALERT_NONE);
  if (want != alert_) { alert_ = want; since_ = now_ms; }
}

static void pad(char out[17], const char* s) {
  size_t n = strlen(s);
  if (n > 16) n = 16;
  memset(out, ' ', 16);
  memcpy(out, s, n);
  out[16] = 0;
}

void Engine::lines(char l1[17], char l2[17]) const {
  char b[40];
  switch (alert_) {
    case ALERT_CRASH:
      pad(l1, "CRASH DETECTED"); pad(l2, "Stop safely"); break;
    case ALERT_EMERGENCY:
      pad(l1, "EMERGENCY ALERT"); pad(l2, "demo: not sent"); break;
    case ALERT_HAZARD:
      pad(l1, "ROAD HAZARD"); pad(l2, "Bump detected"); break;
    case ALERT_OBSTACLE:
      snprintf(b, sizeof b, "Obstacle %dcm", (int)(cm_ + 0.5f)); pad(l1, b);
      pad(l2, cm_ <= kStopCm ? "Stop" : "Keep distance"); break;
    default:
      pad(l1, "FlowSync car");
      if (cm_ < 0.0f) pad(l2, "Road clear");
      else { snprintf(b, sizeof b, "Ahead %dcm", (int)(cm_ + 0.5f)); pad(l2, b); }
  }
}

}  // namespace carlogic
