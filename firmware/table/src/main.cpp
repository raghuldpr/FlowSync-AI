// FlowSync tabletop, STANDALONE demo. No Wi-Fi, no backend: a built-in fixed-time cycle plus a scripted ambulance-priority
// sequence, so the hotspot cannot break the table. (The backend-connected controller is firmware/intersection: future work.)
//
//   BOARD_ID 1: I1 = modules M1(A)/M2(B), I2 = M3(A)/M4(B)      BOARD_ID 2: I3 = modules M1(A)/M2(B)
//   Pins (R/Y/G): see PINS below and docs/HARDWARE_WIRING.md. Avoid TX0, RX0 and D12.
//   BOOT button (GPIO0) on a board: start the ambulance sequence now (press both boards together to keep them in step).
//   BENCH_TEST build: serial commands instead of the demo (type h).
#include <Arduino.h>
#include "table_demo.h"
#include "timing_defaults.h"

#ifndef BOARD_ID
#error "build with -DBOARD_ID=1 or 2 (use the board1 / board2 environments)"
#endif

struct LampPins { uint8_t r, y, g; };
struct IntersectionPins { const char* name; LampPins a, b; };

#if BOARD_ID == 1
static const IntersectionPins PINS[] = {
    {"I1", {23, 22, 21}, {19, 18, 27}},
    {"I2", {26, 25, 33}, {32, 14, 13}},
};
static const int FIRST = 0;
#else
static const IntersectionPins PINS[] = {
    {"I3", {23, 22, 21}, {19, 18, 27}},
};
static const int FIRST = 2;
#endif
static const int N = sizeof(PINS) / sizeof(PINS[0]);
static const uint8_t PIN_BUTTON = 0;   // BOOT button on the DevKit, pressed = LOW

static void write_lamp(const LampPins& p, char c) {
  digitalWrite(p.r, c == 'R' ? HIGH : LOW);
  digitalWrite(p.y, c == 'Y' ? HIGH : LOW);
  digitalWrite(p.g, c == 'G' ? HIGH : LOW);
}
// Drive an axis's red FIRST and its green/yellow last, so no moment shows two conflicting greens.
static void all_red() {
  for (int i = 0; i < N; i++) { write_lamp(PINS[i].a, 'R'); write_lamp(PINS[i].b, 'R'); }
}
static void init_pins() {
  for (int i = 0; i < N; i++) {
    const LampPins* l[2] = {&PINS[i].a, &PINS[i].b};
    for (int k = 0; k < 2; k++) {
      pinMode(l[k]->r, OUTPUT); pinMode(l[k]->y, OUTPUT); pinMode(l[k]->g, OUTPUT);
    }
  }
  all_red();
  pinMode(PIN_BUTTON, INPUT_PULLUP);
}

#ifdef BENCH_TEST
// ---- bench test: use the serial monitor at 115200 ----
static void help() {
  Serial.println("commands: h help | l walk every lamp one at a time (R,Y,G of each module) | r / y / g ALL lamps that colour");
  Serial.println("          o all red | c safe A/B cycle (10 s) | m show the pin map");
  Serial.println("NOTE r/y/g light every module at once: that is a wiring check only, never a traffic state.");
}
static void show_map() {
  for (int i = 0; i < N; i++) {
    Serial.printf("%s A: R=D%u Y=D%u G=D%u   B: R=D%u Y=D%u G=D%u\n", PINS[i].name, PINS[i].a.r, PINS[i].a.y, PINS[i].a.g,
                  PINS[i].b.r, PINS[i].b.y, PINS[i].b.g);
  }
}
static void all_colour(char c) {
  all_red();
  if (c == 'R') return;
  for (int i = 0; i < N; i++) { write_lamp(PINS[i].a, c); write_lamp(PINS[i].b, c); }
}
static void walk() {
  Serial.println("[walk] each lamp 1 s. Expect red, yellow, green on module M1, then M2 ...");
  for (int i = 0; i < N; i++) {
    const LampPins* l[2] = {&PINS[i].a, &PINS[i].b};
    const char* mod[2] = {"A", "B"};
    for (int k = 0; k < 2; k++) {
      const uint8_t pins[3] = {l[k]->r, l[k]->y, l[k]->g};
      const char* cn[3] = {"RED", "YELLOW", "GREEN"};
      for (int c = 0; c < 3; c++) {
        all_red();
        digitalWrite(l[k]->r, LOW);
        digitalWrite(pins[c], HIGH);
        Serial.printf("[walk] %s-%s %s (D%u)\n", PINS[i].name, mod[k], cn[c], pins[c]);
        delay(1000);
      }
    }
  }
  all_red();
  Serial.println("[walk] done");
}
static void safe_cycle() {
  Serial.println("[cycle] 10 s of safe A/B on every intersection (green 3 s, yellow 2 s, all-red 1 s)");
  uint32_t t0 = millis();
  while (millis() - t0 < 10000) {
    for (char axis = 'A'; axis <= 'B'; axis++) {
      for (int i = 0; i < N; i++) {
        write_lamp(axis == 'A' ? PINS[i].b : PINS[i].a, 'R');
        write_lamp(axis == 'A' ? PINS[i].a : PINS[i].b, 'G');
      }
      delay(3000);
      for (int i = 0; i < N; i++) write_lamp(axis == 'A' ? PINS[i].a : PINS[i].b, 'Y');
      delay(2000);
      all_red();
      delay(1000);
    }
  }
  all_red();
  Serial.println("[cycle] done");
}
void setup() {
  Serial.begin(115200);
  delay(500);
  init_pins();
  Serial.printf("\n=== FlowSync table BENCH TEST, board %d ===\n", BOARD_ID);
  show_map();
  help();
}
void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    switch (c) {
      case 'h': help(); break;
      case 'm': show_map(); break;
      case 'l': walk(); break;
      case 'r': all_colour('R'); Serial.println("[all] RED"); break;
      case 'y': all_colour('Y'); Serial.println("[all] YELLOW"); break;
      case 'g': all_colour('G'); Serial.println("[all] GREEN"); break;
      case 'o': all_red(); Serial.println("[all] red"); break;
      case 'c': safe_cycle(); break;
      default: break;
    }
  }
}
#else
// ---- demo ----
static tabledemo::Table* table = nullptr;
static char last[2][2] = {{0, 0}, {0, 0}};
static bool last_priority = false;

void setup() {
  Serial.begin(115200);
  delay(300);
  init_pins();                                   // everything red before the first cycle starts
  table = new tabledemo::Table(flowsync::kTiming, FIRST, N, millis());
  Serial.printf("\n=== FlowSync table demo (standalone), board %d, %d intersection(s) ===\n", BOARD_ID, N);
  Serial.printf("loop %u s; ambulance sequence at %u s for %u s; BOOT button starts it now\n",
                (unsigned)(tabledemo::kPeriodMs / 1000), (unsigned)(tabledemo::kPriorityStartMs / 1000),
                (unsigned)(tabledemo::kPriorityHoldMs / 1000));
}

void loop() {
  static bool btn_prev = false;
  bool btn = digitalRead(PIN_BUTTON) == LOW;
  if (btn && !btn_prev) { table->trigger_priority(millis()); Serial.println("[button] ambulance sequence requested"); }
  btn_prev = btn;

  table->tick(millis());
  for (int i = 0; i < N; i++) {
    flowsync::Lamps l = table->lamps(i);
    // the FSM never has both axes non-red, and it holds all-red between them, so a plain write is safe
    if (l.a != last[i][0] || l.b != last[i][1]) {
      write_lamp(PINS[i].a, l.a);
      write_lamp(PINS[i].b, l.b);
      last[i][0] = l.a; last[i][1] = l.b;
      Serial.printf("[%lu ms] %s A=%c B=%c\n", (unsigned long)millis(), PINS[i].name, l.a, l.b);
    }
  }
  if (table->priority_active() != last_priority) {
    last_priority = table->priority_active();
    Serial.println(last_priority ? "[ambulance] priority sequence ON (simulated demo, I1 B then I2 A)" : "[ambulance] priority sequence OFF");
  }
  delay(10);
}
#endif
