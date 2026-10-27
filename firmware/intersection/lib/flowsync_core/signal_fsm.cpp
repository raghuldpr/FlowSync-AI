#include "signal_fsm.h"

namespace flowsync {

static inline char other(char axis) { return axis == 'A' ? 'B' : 'A'; }

const char* phase_name(Phase p) {
  switch (p) {
    case PHASE_GREEN: return "GREEN";
    case PHASE_YELLOW: return "YELLOW";
    default: return "ALL_RED";
  }
}

SignalFsm::SignalFsm(const Timing& t, uint32_t now_ms)
    : t_(t), phase_(PHASE_ALL_RED), axis_('B'), next_axis_('A'), since_(now_ms), backend_ok_(true),
      has_target_(false), target_('A'), target_until_(0), last_seq_(-1) {}

bool SignalFsm::receive_command(int64_t seq, char target, uint32_t valid_for_ms, uint32_t now_ms, const char** reason) {
  if (target != 'A' && target != 'B') { *reason = "bad_target"; return false; }
  if (valid_for_ms < 500 || valid_for_ms > 10000) { *reason = "bad_validity"; return false; }
  if (seq <= last_seq_) { *reason = "stale_seq"; return false; }
  last_seq_ = seq;
  target_ = target;
  has_target_ = true;
  target_until_ = now_ms + valid_for_ms;   // validity is measured on THIS device's clock from receipt
  *reason = "ok";
  return true;
}

char SignalFsm::desired(uint32_t now_ms) const {
  if (!backend_ok_) {
    // fixed-time fallback: alternate after fallback_green_ms
    return (uint32_t)(now_ms - since_) >= t_.fallback_green_ms ? other(axis_) : axis_;
  }
  if (has_target_ && (int32_t)(now_ms - target_until_) <= 0) return target_;   // wrap-safe "now <= until"
  return 0;
}

void SignalFsm::tick(uint32_t now_ms) {
  uint32_t elapsed = now_ms - since_;
  if (phase_ == PHASE_GREEN) {
    char want = desired(now_ms);
    if (want && want != axis_) {
      uint32_t min_g = backend_ok_ ? t_.min_green_ms : t_.fallback_green_ms;
      if (elapsed >= min_g) { next_axis_ = want; enter(PHASE_YELLOW, now_ms); }
    }
  } else if (phase_ == PHASE_YELLOW) {
    if (elapsed >= t_.yellow_ms) { next_axis_ = other(axis_); enter(PHASE_ALL_RED, now_ms); }
  } else {
    if (elapsed >= t_.all_red_ms) { axis_ = next_axis_; enter(PHASE_GREEN, now_ms); }
  }
}

Lamps SignalFsm::lamps() const {
  Lamps l = {'R', 'R'};
  char* mine = axis_ == 'A' ? &l.a : &l.b;
  if (phase_ == PHASE_GREEN) *mine = 'G';
  else if (phase_ == PHASE_YELLOW) *mine = 'Y';
  return l;
}

DeviceState SignalFsm::state(uint32_t now_ms) const {
  DeviceState s;
  s.phase = phase_; s.axis = axis_; s.next_axis = next_axis_;
  s.elapsed_ms = now_ms - since_; s.lamps = lamps(); s.backend_ok = backend_ok_;
  return s;
}

}  // namespace flowsync
