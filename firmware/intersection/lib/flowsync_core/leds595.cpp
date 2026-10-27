#include "leds595.h"

namespace flowsync {

static uint8_t bits_for(char c, int r, int y, int g) {
  switch (c) {
    case 'G': return (uint8_t)(1u << g);
    case 'Y': return (uint8_t)(1u << y);
    default: return (uint8_t)(1u << r);   // 'R' and anything unexpected
  }
}

uint8_t encode_lamps(Lamps l) {
  return (uint8_t)(bits_for(l.a, 0, 1, 2) | bits_for(l.b, 3, 4, 5));
}

void write_chain(Hal595& hal, const uint8_t regs[3]) {
  for (int reg = 2; reg >= 0; reg--) {            // farthest register first
    for (int bit = 7; bit >= 0; bit--) {          // MSB first
      hal.data((regs[reg] >> bit) & 1);
      hal.clock();
    }
  }
  hal.latch();
}

}  // namespace flowsync
