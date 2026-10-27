// Copy to config.h (git-ignored). DEVICE_KEY must equal `key:` of `roadcam-1` in config/devices.yaml.
#pragma once

#define WIFI_SSID "flowsync-demo"          // 2.4 GHz only, WPA2
#define WIFI_PASSWORD "change-me"
#define BACKEND_HOST "192.168.1.50"
#define BACKEND_PORT 8000

#define DEVICE_ID "roadcam-1"
#define DEVICE_KEY "dev-roadcam-1-change-me"

#define CAPTURE_PERIOD_MS 5000             // one frame every 5 s keeps Wi-Fi and processing load low
#define JPEG_QUALITY 12                    // 10 (best) .. 63; QVGA at 12 is ~10-20 KB (backend limit: 400 KB)
