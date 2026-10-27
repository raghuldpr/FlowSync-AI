#include "protocol.h"

#include <stdio.h>
#include <string.h>

namespace flowsync {

const char* const kIntersectionNames[kIntersections] = {"I1", "I2", "I3"};

namespace {

struct Cur { const char* p; const char* end; };

void ws(Cur& c) { while (c.p < c.end && (*c.p == ' ' || *c.p == '\n' || *c.p == '\r' || *c.p == '\t')) c.p++; }

bool str(Cur& c, char* out, size_t cap) {      // parses "..." (basic escapes); truncates silently into `out`
  if (c.p >= c.end || *c.p != '"') return false;
  c.p++;
  size_t n = 0;
  while (c.p < c.end && *c.p != '"') {
    char ch = *c.p++;
    if (ch == '\\') { if (c.p >= c.end) return false; ch = *c.p++; }
    if (n + 1 < cap) out[n++] = ch;
  }
  if (c.p >= c.end) return false;
  c.p++;
  if (cap) out[n] = 0;
  return true;
}

bool skip_value(Cur& c) {
  ws(c);
  if (c.p >= c.end) return false;
  if (*c.p == '"') { char tmp[2]; return str(c, tmp, sizeof tmp); }
  if (*c.p == '{' || *c.p == '[') {
    int depth = 0;
    while (c.p < c.end) {
      char ch = *c.p;
      if (ch == '"') { char tmp[2]; if (!str(c, tmp, sizeof tmp)) return false; continue; }
      if (ch == '{' || ch == '[') depth++;
      if (ch == '}' || ch == ']') { depth--; if (depth == 0) { c.p++; return true; } }
      c.p++;
    }
    return false;
  }
  while (c.p < c.end && *c.p != ',' && *c.p != '}' && *c.p != ']' && *c.p != ' ' && *c.p != '\n' && *c.p != '\r') c.p++;
  return true;
}

// Integer value; sets *is_int=false (and consumes the token) for anything else (floats, strings, ...).
bool integer(Cur& c, int64_t* v, bool* is_int) {
  ws(c);
  const char* s = c.p;
  bool neg = false;
  if (c.p < c.end && *c.p == '-') { neg = true; c.p++; }
  int64_t acc = 0;
  int digits = 0;
  while (c.p < c.end && *c.p >= '0' && *c.p <= '9' && digits < 18) { acc = acc * 10 + (*c.p - '0'); c.p++; digits++; }
  *is_int = digits > 0 && (c.p >= c.end || *c.p == ',' || *c.p == '}' || *c.p == ']' || *c.p == ' ' || *c.p == '\n' || *c.p == '\r');
  if (!*is_int) { c.p = s; if (!skip_value(c)) return false; }
  *v = neg ? -acc : acc;
  return true;
}

// Parses one command object; cursor on '{'. Returns false if the JSON is structurally broken.
bool command_object(Cur& c, ParsedCommand* out) {
  out->ok = false; out->reason = "bad_command"; out->inter = -1; out->target = 0; out->valid_for_ms = 0; out->seq = -1;
  bool have_i = false, have_t = false, have_v = false, have_s = false;
  c.p++;   // '{'
  ws(c);
  if (c.p < c.end && *c.p == '}') { c.p++; return true; }
  while (true) {
    ws(c);
    char key[24];
    if (!str(c, key, sizeof key)) return false;
    ws(c);
    if (c.p >= c.end || *c.p != ':') return false;
    c.p++; ws(c);
    if (!strcmp(key, "intersection")) {
      char v[8];
      if (*c.p == '"') { if (!str(c, v, sizeof v)) return false; for (int i = 0; i < kIntersections; i++) if (!strcmp(v, kIntersectionNames[i])) { out->inter = (int8_t)i; have_i = true; } }
      else if (!skip_value(c)) return false;
    } else if (!strcmp(key, "target")) {
      char v[4];
      if (*c.p == '"') { if (!str(c, v, sizeof v)) return false; if ((v[0] == 'A' || v[0] == 'B') && v[1] == 0) { out->target = v[0]; have_t = true; } }
      else if (!skip_value(c)) return false;
    } else if (!strcmp(key, "valid_for_ms") || !strcmp(key, "seq")) {
      int64_t v; bool is_int;
      if (!integer(c, &v, &is_int)) return false;
      if (is_int && v >= 0) {
        if (key[0] == 'v') { if (v <= 0xFFFFFFFFLL) { out->valid_for_ms = (uint32_t)v; have_v = true; } }
        else { out->seq = v; have_s = true; }
      }
    } else if (!skip_value(c)) {
      return false;
    }
    ws(c);
    if (c.p >= c.end) return false;
    if (*c.p == ',') { c.p++; continue; }
    if (*c.p == '}') { c.p++; break; }
    return false;
  }
  if (have_i && have_t && have_v && have_s) { out->ok = true; out->reason = "ok"; }
  return true;
}

}  // namespace

int parse_commands(const char* json, size_t len, ParsedCommand* out, int max) {
  Cur c = {json, json + len};
  ws(c);
  if (c.p >= c.end || *c.p != '{') return -1;
  c.p++;
  int n = 0;
  bool found = false;
  while (true) {
    ws(c);
    if (c.p < c.end && *c.p == '}') break;
    char key[24];
    if (!str(c, key, sizeof key)) return -1;
    ws(c);
    if (c.p >= c.end || *c.p != ':') return -1;
    c.p++; ws(c);
    if (!strcmp(key, "commands")) {
      if (c.p >= c.end || *c.p != '[') return -1;
      found = true;
      c.p++;
      ws(c);
      if (c.p < c.end && *c.p == ']') { c.p++; }
      else while (true) {
        ws(c);
        if (c.p >= c.end || *c.p != '{') return -1;
        ParsedCommand pc;
        if (!command_object(c, &pc)) return -1;
        if (n < max) out[n++] = pc;
        ws(c);
        if (c.p >= c.end) return -1;
        if (*c.p == ',') { c.p++; continue; }
        if (*c.p == ']') { c.p++; break; }
        return -1;
      }
    } else if (!skip_value(c)) {
      return -1;
    }
    ws(c);
    if (c.p >= c.end) return -1;
    if (*c.p == ',') { c.p++; continue; }
    if (*c.p == '}') break;
    return -1;
  }
  return found ? n : -1;
}

int build_state_json(char* buf, size_t cap, const DeviceState st[kIntersections]) {
  size_t n = 0;
  int w = snprintf(buf, cap, "{\"intersections\":{");
  if (w < 0 || (size_t)w >= cap) return -1;
  n = (size_t)w;
  for (int i = 0; i < kIntersections; i++) {
    const DeviceState& s = st[i];
    w = snprintf(buf + n, cap - n,
                 "%s\"%s\":{\"phase\":\"%s\",\"axis\":\"%c\",\"next_axis\":\"%c\",\"elapsed_ms\":%lu,"
                 "\"lamps\":{\"A\":\"%c\",\"B\":\"%c\"},\"backend_ok\":%s}",
                 i ? "," : "", kIntersectionNames[i], phase_name(s.phase), s.axis, s.next_axis,
                 (unsigned long)s.elapsed_ms, s.lamps.a, s.lamps.b, s.backend_ok ? "true" : "false");
    if (w < 0 || (size_t)w >= cap - n) return -1;
    n += (size_t)w;
  }
  w = snprintf(buf + n, cap - n, "}}");
  if (w < 0 || (size_t)w >= cap - n) return -1;
  return (int)(n + (size_t)w);
}

int build_ack_json(char* buf, size_t cap, int64_t seq, const char* intersection, bool accepted, const char* reason) {
  int w = snprintf(buf, cap, "{\"seq\":%ld,\"intersection\":\"%s\",\"accepted\":%s,\"reason\":\"%s\"}",
                   (long)seq, intersection, accepted ? "true" : "false", reason);
  return (w < 0 || (size_t)w >= cap) ? -1 : w;
}

}  // namespace flowsync
