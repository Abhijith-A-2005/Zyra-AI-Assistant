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

// ESP8266 home Wi-Fi IP.
// This is used when Zyra is still connected to home Wi-Fi.
#define RELAY_HOME_BASE_URL "http://192.168.29.97"

// ESP8266 direct AP IP.
// This is used only when home Wi-Fi itself fails.
#define RELAY_AP_BASE_URL   "http://192.168.4.1"