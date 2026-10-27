// Copy to config.h (git-ignored) and edit. The device key must equal `key:` for this device in config/devices.yaml.
#pragma once

// Wi-Fi: 2.4 GHz only, WPA2. Use a dedicated router/hotspot on a fixed channel (1, 6 or 11), never venue Wi-Fi.
#define WIFI_SSID "flowsync-demo"
#define WIFI_PASSWORD "change-me"

// Backend on the laptop. Give the laptop a DHCP reservation (or use its mDNS name) so this never changes.
#define BACKEND_HOST "192.168.1.50"
#define BACKEND_PORT 8000

#define DEVICE_ID "intersection-1"
#define DEVICE_KEY "dev-intersection-1-change-me"

// 74HCT595 chain (see docs/HARDWARE_WIRING.md). Avoid strapping pins 0, 2, 5, 12, 15.
#define PIN_595_DATA 23
#define PIN_595_CLOCK 18
#define PIN_595_LATCH 19
#define PIN_595_OE 21   // active LOW; external 10k pull-up to 3V3 keeps all LEDs off until firmware has latched all-red
