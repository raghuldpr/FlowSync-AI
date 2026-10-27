#include "signing.h"

#include <string.h>

#include "sha256.h"

namespace fsync {

static void u32_dec(uint32_t v, char* out) {
  char tmp[11];
  int n = 0;
  do { tmp[n++] = (char)('0' + v % 10); v /= 10; } while (v);
  for (int i = 0; i < n; i++) out[i] = tmp[n - 1 - i];
  out[n] = 0;
}

void sign_request(const char* key, const char* method, const char* path, const char* device_id,
                  const char* boot, uint32_t seq, const uint8_t* body, size_t body_len, char out_hex[65]) {
  HmacSha256 h((const uint8_t*)key, strlen(key));
  char num[12];
  u32_dec(seq, num);
  // method is upper-cased by the backend; callers pass "GET"/"POST"
  h.update(method); h.update("|"); h.update(path); h.update("|"); h.update(device_id); h.update("|");
  h.update(boot); h.update("|"); h.update(num); h.update("|");
  if (body_len) h.update(body, body_len);
  uint8_t mac[32];
  h.finish(mac);
  to_hex(mac, 32, out_hex);
}

}  // namespace fsync
