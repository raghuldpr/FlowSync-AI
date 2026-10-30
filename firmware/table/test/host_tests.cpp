// Host test for the standalone table logic. Run: python firmware/table/test/run_host_tests.py
#include <stdio.h>
#include <stdlib.h>
#include "table_demo.h"
#include "timing_defaults.h"

static int fails = 0;
#define CHECK(c, ...) do { if (!(c)) { printf("FAIL %s:%d: ", __FILE__, __LINE__); printf(__VA_ARGS__); printf("\n"); fails++; } } while (0)

int main() {
  using namespace tabledemo;
  // board 1 drives I1 and I2, board 2 drives I3 (same boot time, as on the table)
  Table b1(flowsync::kTiming, 0, 2, 0);
  Table b2(flowsync::kTiming, 2, 1, 0);

  uint32_t green_i1_b = 0, green_i2_a = 0, run_ms = 4 * kPeriodMs;
  uint32_t window_i1 = 0, window_i2 = 0;
  char prev[3][2] = {{'R', 'R'}, {'R', 'R'}, {'R', 'R'}};
  for (uint32_t t = 0; t <= run_ms; t += 10) {
    b1.tick(t); b2.tick(t);
    flowsync::Lamps l[3] = {b1.lamps(0), b1.lamps(1), b2.lamps(0)};
    for (int i = 0; i < 3; i++) {
      CHECK(flowsync::lamps_safe(l[i]), "t=%u I%d both axes non-red (%c,%c)", (unsigned)t, i + 1, l[i].a, l[i].b);
      // a green axis must drop to yellow before red: G -> R directly is forbidden
      if (prev[i][0] == 'G') CHECK(l[i].a != 'R', "t=%u I%d A went G->R without yellow", (unsigned)t, i + 1);
      if (prev[i][1] == 'G') CHECK(l[i].b != 'R', "t=%u I%d B went G->R without yellow", (unsigned)t, i + 1);
      prev[i][0] = l[i].a; prev[i][1] = l[i].b;
    }
    uint32_t in_loop = t % kPeriodMs;
    bool in_window = in_loop >= kPriorityStartMs + 5000 && in_loop < kPriorityStartMs + kPriorityHoldMs;   // after switching settles
    if (in_window) {
      window_i1 += 10; window_i2 += 10;
      if (l[0].b == 'G') green_i1_b += 10;
      if (l[1].a == 'G') green_i2_a += 10;
    }
  }
  CHECK(green_i1_b * 100 >= window_i1 * 95, "I1 axis B green only %u of %u ms in priority windows", (unsigned)green_i1_b, (unsigned)window_i1);
  CHECK(green_i2_a * 100 >= window_i2 * 95, "I2 axis A green only %u of %u ms in priority windows", (unsigned)green_i2_a, (unsigned)window_i2);

  // I3 is never on the corridor: its normal cycle must serve both axes (no starvation)
  Table solo(flowsync::kTiming, 2, 1, 0);
  uint32_t ga = 0, gb = 0;
  for (uint32_t t = 0; t <= kPeriodMs; t += 10) {
    solo.tick(t);
    flowsync::Lamps l = solo.lamps(0);
    if (l.a == 'G') ga += 10;
    if (l.b == 'G') gb += 10;
  }
  CHECK(ga > 8000 && gb > 8000, "I3 starved: A=%u ms B=%u ms", (unsigned)ga, (unsigned)gb);

  // the button triggers the sequence at an arbitrary time; the corridor turns green within yellow + all-red + min green
  Table btn(flowsync::kTiming, 0, 1, 0);
  uint32_t t = 0;
  for (; t < 3000; t += 10) btn.tick(t);
  btn.trigger_priority(t);
  uint32_t first_green = 0;
  for (uint32_t u = t; u < t + kPriorityHoldMs; u += 10) {
    btn.tick(u);
    if (!first_green && btn.lamps(0).b == 'G') first_green = u - t;
  }
  CHECK(btn.priority_active(), "button priority not active");
  CHECK(first_green != 0 && first_green <= 9000, "corridor green only after %u ms", (unsigned)first_green);
  for (uint32_t u = t + kPriorityHoldMs; u < t + kPriorityHoldMs + 200; u += 10) btn.tick(u);
  CHECK(!btn.priority_active(), "button priority did not end");

  // 32-bit clock wrap is safe
  Table wrap(flowsync::kTiming, 0, 2, 0xFFFFF000u);
  for (uint32_t u = 0xFFFFF000u; u != 0x00002000u; u += 10) {
    wrap.tick(u);
    for (int i = 0; i < 2; i++) CHECK(flowsync::lamps_safe(wrap.lamps(i)), "unsafe lamps across wrap");
  }

  if (fails) printf("FAILED: %d\n", fails); else printf("table host tests OK\n");
  return fails ? 1 : 0;
}
