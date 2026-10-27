// Request signing, identical to backend/app/security.py::sign():
//   HMAC-SHA256(key, "METHOD|path|device_id|boot|seq|" + body) as lowercase hex.
#pragma once
#include <stddef.h>
#include <stdint.h>

namespace fsync {

// out_hex must hold 65 bytes. `path` is the URL path only (no query string).
void sign_request(const char* key, const char* method, const char* path, const char* device_id,
                  const char* boot, uint32_t seq, const uint8_t* body, size_t body_len, char out_hex[65]);

}  // namespace fsync
