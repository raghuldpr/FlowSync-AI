// Small self-contained SHA-256 / HMAC-SHA256 (no mbedtls dependency, so it can be tested on a PC).
#pragma once
#include <stddef.h>
#include <stdint.h>

namespace fsync {

class Sha256 {
 public:
  Sha256() { reset(); }
  void reset();
  void update(const uint8_t* data, size_t len);
  void finish(uint8_t out[32]);

 private:
  void block(const uint8_t* p);
  uint32_t h_[8];
  uint8_t buf_[64];
  size_t blen_;
  uint64_t total_;
};

class HmacSha256 {
 public:
  HmacSha256(const uint8_t* key, size_t key_len);
  void update(const uint8_t* data, size_t len) { inner_.update(data, len); }
  void update(const char* s);
  void finish(uint8_t out[32]);

 private:
  Sha256 inner_;
  uint8_t okey_[64];
};

void to_hex(const uint8_t* in, size_t n, char* out);   // writes 2n chars + NUL

}  // namespace fsync
