#include "table_demo.h"
#include <new>

namespace tabledemo {

char corridor_axis(int intersection) {
  switch (intersection) {
    case 0: return 'B';   // I1
    case 1: return 'A';   // I2
    default: return 0;    // I3 keeps cycling
  }
}

Table::Table(const flowsync::Timing& t, int first, int count, uint32_t now_ms)
    : first_(first), count_(count > kMax ? kMax : count), epoch_(now_ms), last_refresh_(now_ms), priority_(false),
      override_(false), override_until_(0), seq_(0) {
  for (int i = 0; i < kMax; i++) fsm_[i] = 0;
  for (int i = 0; i < count_; i++) {
    fsm_[i] = new (storage_[i]) flowsync::SignalFsm(t, now_ms);
    fsm_[i]->set_backend_ok(false);       // fixed-time cycle until the ambulance sequence starts
  }
}

void Table::trigger_priority(uint32_t now_ms) {
  override_ = true;
  override_until_ = now_ms + kPriorityHoldMs;
}

void Table::tick(uint32_t now_ms) {
  bool want = false;
  uint32_t in_loop = (now_ms - epoch_) % kPeriodMs;
  if (in_loop >= kPriorityStartMs && in_loop < kPriorityStartMs + kPriorityHoldMs) want = true;
  if (override_) {
    if ((int32_t)(now_ms - override_until_) <= 0) want = true; else override_ = false;
  }
  if (want && !priority_) last_refresh_ = now_ms - kRefreshMs;   // send the first command on this tick
  priority_ = want;

  bool refresh = (uint32_t)(now_ms - last_refresh_) >= kRefreshMs;
  for (int i = 0; i < count_; i++) {
    char axis = corridor_axis(first_ + i);
    bool corridor = priority_ && axis != 0;
    fsm_[i]->set_backend_ok(corridor);
    if (corridor && refresh) {
      const char* reason = "";
      fsm_[i]->receive_command(++seq_, axis, kCommandValidMs, now_ms, &reason);
    }
    fsm_[i]->tick(now_ms);
  }
  if (refresh) last_refresh_ = now_ms;
}

flowsync::Lamps Table::lamps(int i) const {
  flowsync::Lamps l = fsm_[i]->lamps();
  if (!flowsync::lamps_safe(l)) { l.a = 'R'; l.b = 'R'; }
  return l;
}

}  // namespace tabledemo
