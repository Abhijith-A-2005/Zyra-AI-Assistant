#pragma once

// ── Home Wi-Fi + ZYRA server ─────────────────────
#define WIFI_SSID      "YOUR_WIFI_NAME"
#define WIFI_PASSWORD  "YOUR_WIFI_PASSWORD"

#define SERVER_IP      "192.168.x.x"
#define SERVER_PORT    8765

// ── Offline relay fallback ───────────────────────
// ESP8266 smart extension direct AP.
#define RELAY_AP_SSID      "ESP-REMOTE-DIRECT"
#define RELAY_AP_PASSWORD  "12345678"

// ESP8266 direct AP IP.
#define RELAY_BASE_URL     "http://192.168.4.1"