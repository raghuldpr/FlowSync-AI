// JSON for the device API (matches backend/app/api/device.py). Hand-rolled to stay dependency-free and testable.
#pragma once
#include <stddef.h>
#include <stdint.h>

#include "signal_fsm.h"

namespace fsync {

constexpr int kIntersections = 3;
extern const char* const kIntersectionNames[kIntersections];   // "I1","I2","I3"

struct ParsedCommand {
  bool ok;               // false: syntactically present but unusable (ack it as rejected)
  const char* reason;    // "ok" | "bad_command"
  int8_t inter;          // index into kIntersectionNames, -1 if unknown
  char target;           // 'A' | 'B'
  uint32_t valid_for_ms;
  int64_t seq;
};

// Parses the body of GET /api/device/commands. Returns the number of commands written (<= max),
// or -1 if the document is malformed. Unknown keys are ignored; key order and whitespace do not matter.
int parse_commands(const char* json, size_t len, ParsedCommand* out, int max);

// {"intersections":{"I1":{...},"I2":{...},"I3":{...}}}. Returns length, or -1 if `cap` is too small.
int build_state_json(char* buf, size_t cap, const DeviceState states[kIntersections]);

// {"seq":N,"intersection":"I1","accepted":true,"reason":"ok"}
int build_ack_json(char* buf, size_t cap, int64_t seq, const char* intersection, bool accepted, const char* reason);

}  // namespace fsync
