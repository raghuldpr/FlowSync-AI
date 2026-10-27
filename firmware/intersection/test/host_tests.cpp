// PC test driver for the portable firmware core. Reads a tiny text protocol on stdin and prints results; the
// pytest suite generates the inputs and the expected outputs from the Python reference implementation.
//   host_tests fsm | sign | parse | leds | statejson | ackjson
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <string>
#include <vector>

#include "leds595.h"
#include "protocol.h"
#include "sha256.h"
#include "signal_fsm.h"
#include "signing.h"

using namespace flowsync;

static std::string read_all() {
  std::string s;
  char buf[4096];
  size_t n;
  while ((n = fread(buf, 1, sizeof buf, stdin)) > 0) s.append(buf, n);
  return s;
}

static std::vector<std::string> lines(const std::string& s) {
  std::vector<std::string> out;
  size_t i = 0;
  while (i < s.size()) {
    size_t j = s.find('\n', i);
    if (j == std::string::npos) j = s.size();
    std::string l = s.substr(i, j - i);
    if (!l.empty() && l.back() == '\r') l.pop_back();
    if (!l.empty()) out.push_back(l);
    i = j + 1;
  }
  return out;
}

static int cmd_fsm() {
  Timing t = {};
  SignalFsm* f = nullptr;
  char buf[256];
  for (const std::string& l : lines(read_all())) {
    const char* p = l.c_str();
    unsigned long v[8];
    unsigned long now, valid;
    long long seq;
    char target;
    int flag;
    if (l[0] == 'T') {
      sscanf(p, "T %lu %lu %lu %lu %lu %lu %lu %lu", &v[0], &v[1], &v[2], &v[3], &v[4], &v[5], &v[6], &v[7]);
      t = {(uint32_t)v[0], (uint32_t)v[1], (uint32_t)v[2], (uint32_t)v[3], (uint32_t)v[4], (uint32_t)v[5], (uint32_t)v[6], (uint32_t)v[7]};
    } else if (l[0] == 'N') {
      sscanf(p, "N %lu", &now);
      delete f;
      f = new SignalFsm(t, (uint32_t)now);
    } else if (l[0] == 'C') {
      sscanf(p, "C %lu %lld %c %lu", &now, &seq, &target, &valid);
      const char* reason;
      f->receive_command(seq, target, (uint32_t)valid, (uint32_t)now, &reason);
      printf("C %s\n", reason);
    } else if (l[0] == 'B') {
      sscanf(p, "B %lu %d", &now, &flag);
      f->set_backend_ok(flag != 0);
    } else if (l[0] == 'K') {
      sscanf(p, "K %lu", &now);
      f->tick((uint32_t)now);
      DeviceState s = f->state((uint32_t)now);
      snprintf(buf, sizeof buf, "K %lu %s %c %c %c%c %lu", now, phase_name(s.phase), s.axis, s.next_axis,
               s.lamps.a, s.lamps.b, (unsigned long)s.elapsed_ms);
      printf("%s\n", buf);
    }
  }
  delete f;
  return 0;
}

static int hexval(char c) { return c <= '9' ? c - '0' : (c | 32) - 'a' + 10; }

static int cmd_sign() {
  for (const std::string& l : lines(read_all())) {
    char key[256], method[16], path[128], dev[64], boot[80], hex[4096];
    unsigned long seq;
    if (sscanf(l.c_str(), "%255s %15s %127s %63s %79s %lu %4095s", key, method, path, dev, boot, &seq, hex) != 7) { puts("ERR"); continue; }
    std::vector<uint8_t> body;
    if (strcmp(hex, "-")) for (size_t i = 0; hex[i] && hex[i + 1]; i += 2) body.push_back((uint8_t)(hexval(hex[i]) * 16 + hexval(hex[i + 1])));
    char sig[65];
    sign_request(key, method, path, dev, boot, (uint32_t)seq, body.data(), body.size(), sig);
    puts(sig);
  }
  return 0;
}

static int cmd_parse() {
  std::string in = read_all();
  ParsedCommand out[8];
  int n = parse_commands(in.c_str(), in.size(), out, 8);
  printf("n=%d\n", n);
  for (int i = 0; i < n; i++)
    printf("%d %d %c %lu %lld %s\n", out[i].ok, out[i].inter, out[i].target ? out[i].target : '-',
           (unsigned long)out[i].valid_for_ms, (long long)out[i].seq, out[i].reason);
  return 0;
}

// Behavioural model of three chained 74HCT595: outputs change ONLY on the latch pulse.
struct SimChain : Hal595 {
  uint8_t sr[3] = {0, 0, 0}, out[3] = {0, 0, 0};
  bool d = false;
  int clocks = 0, latches = 0;
  void data(bool l) override { d = l; }
  void clock() override {
    uint8_t c0 = sr[0] >> 7, c1 = sr[1] >> 7;
    sr[0] = (uint8_t)((sr[0] << 1) | (d ? 1 : 0));
    sr[1] = (uint8_t)((sr[1] << 1) | c0);
    sr[2] = (uint8_t)((sr[2] << 1) | c1);
    clocks++;
  }
  void latch() override { memcpy(out, sr, 3); latches++; }
};

static int cmd_leds() {
  for (const std::string& l : lines(read_all())) {      // e.g. "GR YR RG"
    uint8_t regs[3];
    bool safe = true;
    for (int i = 0; i < 3; i++) {
      Lamps lp = {l[i * 3], l[i * 3 + 1]};
      safe = safe && lamps_safe(lp);
      regs[i] = encode_lamps(lp);
    }
    SimChain chain;
    write_chain(chain, regs);
    printf("%02x %02x %02x safe=%d clocks=%d latches=%d\n", chain.out[0], chain.out[1], chain.out[2], safe, chain.clocks, chain.latches);
  }
  return 0;
}

static int cmd_statejson() {
  std::vector<std::string> ls = lines(read_all());
  DeviceState st[3];
  for (int i = 0; i < 3; i++) {
    char ph[16], ax, nx, a, b;
    unsigned long el;
    int be;
    sscanf(ls[i].c_str(), "%15s %c %c %lu %c %c %d", ph, &ax, &nx, &el, &a, &b, &be);
    st[i] = {!strcmp(ph, "GREEN") ? PHASE_GREEN : !strcmp(ph, "YELLOW") ? PHASE_YELLOW : PHASE_ALL_RED, ax, nx, (uint32_t)el, {a, b}, be != 0};
  }
  char buf[1024];
  int n = build_state_json(buf, sizeof buf, st);
  printf("%d\n%s\n", n, n > 0 ? buf : "");
  char tiny[40];
  printf("%d\n", build_state_json(tiny, sizeof tiny, st));   // truncation must be reported, never overflow
  return 0;
}

static int cmd_ackjson() {
  for (const std::string& l : lines(read_all())) {
    long long seq;
    char inter[8], reason[64];
    int acc;
    sscanf(l.c_str(), "%lld %7s %d %63s", &seq, inter, &acc, reason);
    char buf[256];
    build_ack_json(buf, sizeof buf, seq, inter, acc != 0, reason);
    puts(buf);
  }
  return 0;
}

int main(int argc, char** argv) {
  if (argc < 2) return 2;
  std::string m = argv[1];
  if (m == "fsm") return cmd_fsm();
  if (m == "sign") return cmd_sign();
  if (m == "parse") return cmd_parse();
  if (m == "leds") return cmd_leds();
  if (m == "statejson") return cmd_statejson();
  if (m == "ackjson") return cmd_ackjson();
  return 2;
}
