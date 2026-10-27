// 18 traffic LEDs through three daisy-chained 74HCT595 (one register per intersection).
//
// Register bit map (Q0..Q5): 0=A_R 1=A_Y 2=A_G 3=B_R 4=B_Y 5=B_G   (Q6,Q7 unused)
// Chain: MCU DATA -> reg1 (I1) -> Q7' -> reg2 (I2) -> Q7' -> reg3 (I3). The first byte clocked in therefore
// ends up in the FAR register, so bytes are shifted I3, I2, I1, each MSB first.
#pragma once
#include <stdint.h>

#include "signal_fsm.h"

namespace flowsync {

constexpr uint8_t kAllRedRegister = 0x09;   // A_R | B_R

// Always lights exactly one lamp per axis (unknown characters default to RED, the safe colour).
uint8_t encode_lamps(Lamps l);

// Thin hardware abstraction so the bit-serial protocol can be tested on a PC.
struct Hal595 {
  virtual void data(bool level) = 0;
  virtual void clock() = 0;   // one SRCLK pulse (shift)
  virtual void latch() = 0;   // one RCLK pulse (all outputs update at once: no transient mixed states)
  virtual ~Hal595() {}
};

// regs[0] = intersection I1 (nearest the MCU) ... regs[2] = I3.
void write_chain(Hal595& hal, const uint8_t regs[3]);

}  // namespace flowsync
