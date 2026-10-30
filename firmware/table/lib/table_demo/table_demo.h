// Standalone tabletop demo logic (no Wi-Fi, no backend). Portable C++11, no Arduino headers: the same code runs on the
// ESP32 and in firmware/table/test/host_tests.cpp.
//
// Each intersection is one flowsync::SignalFsm (min green, yellow, all-red, never two axes non-red). Normal running uses the
// FSM's fixed-time mode (backend_ok = false). The ambulance sequence refreshes a short-lived command for the corridor axis, so
// the FSM itself still decides when it is safe to switch.
#pragma once
#include <stdint.h>
#include "signal_fsm.h"

namespace tabledemo {

constexpr int kIntersections = 3;                    // I1, I2, I3
constexpr uint32_t kPeriodMs = 50000;                // one demo loop
constexpr uint32_t kPriorityStartMs = 36000;         // ambulance sequence starts this far into the loop
constexpr uint32_t kPriorityHoldMs = 14000;          // ... and lasts this long (also used by the button)
constexpr uint32_t kCommandValidMs = 3000;           // each corridor command; refreshed every kRefreshMs while active
constexpr uint32_t kRefreshMs = 1000;

// Axis the ambulance needs green at each intersection (W -> I1 -> I2 -> E: W1 is movement B at I1, L12 is movement A at I2).
// 0 = this intersection is not on the corridor and keeps its normal cycle.
char corridor_axis(int intersection);

class Table {
 public:
  // `first`..`first+count-1` are the intersection indexes this board drives (board 1: first 0, count 2; board 2: first 2, count 1).
  Table(const flowsync::Timing& t, int first, int count, uint32_t now_ms);

  void tick(uint32_t now_ms);
  void trigger_priority(uint32_t now_ms);            // button: start the ambulance sequence now (local override)
  bool priority_active() const { return priority_; }
  int count() const { return count_; }
  int intersection_id(int i) const { return first_ + i; }
  flowsync::Lamps lamps(int i) const;                // always safe: unsafe input is forced to all-red

 private:
  static constexpr int kMax = kIntersections;
  flowsync::SignalFsm* fsm_[kMax];
  alignas(flowsync::SignalFsm) unsigned char storage_[kMax][sizeof(flowsync::SignalFsm)];
  int first_, count_;
  uint32_t epoch_, last_refresh_;
  bool priority_;
  bool override_;
  uint32_t override_until_;
  int64_t seq_;
};

}  // namespace tabledemo
