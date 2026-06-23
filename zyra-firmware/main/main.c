#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <math.h>
#include <stdbool.h>
#include <inttypes.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"
#include "esp_system.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "lwip/ip4_addr.h"
#include "nvs_flash.h"
#include "esp_heap_caps.h"
#include "esp_http_client.h"

// ESP-SR
#include "offline_voice.h"
#include "offline_speech.h"
#include "wakeword_engine.h"

// Our modules
#include "audio_pipeline.h"
#include "audio_streamer.h"
#include "websocket_client.h"
#include "display.h"
#include "smart_home_control.h"
#include "status_led.h"

#define CAPTURE_SAMPLE_RATE 16000

static const char* TAG = "ZYRA";

// ── Configuration ─────────────────────────────────
#include "zyra_config.h"

// ── WiFi event group ──────────────────────────────
static EventGroupHandle_t wifi_events;  
static esp_netif_t* s_sta_netif = NULL;
#define WIFI_CONNECTED_BIT    BIT0
#define WIFI_FAIL_BIT         BIT1
#define WIFI_DISCONNECTED_BIT BIT2

#define OFFLINE_AP_MAX_RETRIES          5
#define OFFLINE_AP_CONNECT_TIMEOUT_MS   12000
#define OFFLINE_AP_RETRY_DELAY_MS       1200

#define SERVER_HEALTH_INTERVAL_MS   5000
#define SERVER_HEALTH_TIMEOUT_MS    5000
#define SERVER_HEALTH_FAIL_LIMIT    2

static volatile bool s_switching_wifi = false;

// This means: server is unavailable, so online voice mode pauses.
static volatile bool s_offline_mode = false;

// This means: relay control is happening through home Wi-Fi.
static volatile bool s_home_relay_mode = false;

// This means: server is unavailable, but firmware is controlling devices
// through Home Assistant REST API on home Wi-Fi.
static volatile bool s_serverless_ha_mode = false;

// This means: emergency relay control is happening through ESP-REMOTE-DIRECT.
static volatile bool s_relay_ap_mode = false;

static volatile bool s_force_runtime_fallback = false;
static volatile bool s_force_direct_ap_fallback = false;
static volatile bool s_relay_error_active = false;
static volatile bool s_online_voice_paused = false;
static volatile bool s_home_relay_starting = false;
static volatile bool s_runtime_mode_switching = false;
// True while an online wake/capture/send/response cycle is active.
static volatile bool s_online_interaction_active = false;
static volatile bool s_offline_command_active = false;
static int s_relay_fail_count = 0;
static int s_relay_ok_count = 0;

static TaskHandle_t s_runtime_fallback_task_handle = NULL;
static TaskHandle_t s_server_reconnect_task_handle = NULL;
static TaskHandle_t s_smart_home_control_task_handle = NULL;
static TaskHandle_t s_zyra_task_handle = NULL;

static void smart_home_control_task(void* arg);
static void zyra_task(void* param);
static bool start_offline_mode(const char* reason);
static bool start_serverless_ha_mode(const char* reason);
static bool start_home_relay_mode(const char* reason);
static bool start_best_serverless_mode(const char* reason);
static bool switch_home_relay_to_serverless_ha(const char* reason);
static void home_assistant_restore_task(void* arg);
static void request_direct_ap_fallback(const char* reason);
static void handle_offline_voice_ui(OfflineVoiceUiEvent event);
static void handle_offline_voice_command(
    OfflineVoiceCommand command,
    const char* phrase,
    float probability
);

static bool online_wake_should_abort(void) {
    return s_online_voice_paused ||
           s_offline_mode ||
           s_switching_wifi;
}

static void show_transition_state(DisplayState state, uint32_t duration_ms);

static void set_state(DisplayState state);
static void drain_mic_frames(int frames);

static void server_health_task(void* arg);
static bool server_health_check_http(void);

static const char* offline_command_label(OfflineVoiceCommand command) {
    switch (command) {
        case OFFLINE_VOICE_CMD_TV_ON: return "TV ON";
        case OFFLINE_VOICE_CMD_TV_OFF: return "TV OFF";
        case OFFLINE_VOICE_CMD_TV_TOGGLE: return "TV TOGGLE";

        case OFFLINE_VOICE_CMD_SOUNDBAR_ON: return "SOUNDBAR ON";
        case OFFLINE_VOICE_CMD_SOUNDBAR_OFF: return "SOUNDBAR OFF";
        case OFFLINE_VOICE_CMD_SOUNDBAR_TOGGLE: return "SOUNDBAR TOGGLE";

        case OFFLINE_VOICE_CMD_SUBWOOFER_ON: return "SUBWOOFER ON";
        case OFFLINE_VOICE_CMD_SUBWOOFER_OFF: return "SUBWOOFER OFF";
        case OFFLINE_VOICE_CMD_SUBWOOFER_TOGGLE: return "SUBWOOFER TOGGLE";

        case OFFLINE_VOICE_CMD_REAR_ON: return "REAR ON";
        case OFFLINE_VOICE_CMD_REAR_OFF: return "REAR OFF";
        case OFFLINE_VOICE_CMD_REAR_TOGGLE: return "REAR TOGGLE";

        case OFFLINE_VOICE_CMD_SOUND_SYSTEM_ON: return "SOUND SYSTEM ON";
        case OFFLINE_VOICE_CMD_SOUND_SYSTEM_OFF: return "SOUND SYSTEM OFF";

        case OFFLINE_VOICE_CMD_ALL_SPEAKERS_ON: return "ALL SPEAKERS ON";
        case OFFLINE_VOICE_CMD_ALL_SPEAKERS_OFF: return "ALL SPEAKERS OFF";

        case OFFLINE_VOICE_CMD_HOME_THEATER_ON: return "HOME THEATER ON";
        case OFFLINE_VOICE_CMD_HOME_THEATER_OFF: return "HOME THEATER OFF";

        case OFFLINE_VOICE_CMD_STATUS: return "STATUS";

        default: return "UNKNOWN COMMAND";
    }
}

static const char* offline_command_prompt(OfflineVoiceCommand command) {
    switch (command) {
        case OFFLINE_VOICE_CMD_TV_ON: return "tv_on.wav";
        case OFFLINE_VOICE_CMD_TV_OFF: return "tv_off.wav";

        case OFFLINE_VOICE_CMD_SOUNDBAR_ON: return "soundbar_on.wav";
        case OFFLINE_VOICE_CMD_SOUNDBAR_OFF: return "soundbar_off.wav";

        case OFFLINE_VOICE_CMD_SUBWOOFER_ON: return "subwoofer_on.wav";
        case OFFLINE_VOICE_CMD_SUBWOOFER_OFF: return "subwoofer_off.wav";

        case OFFLINE_VOICE_CMD_REAR_ON: return "rear_on.wav";
        case OFFLINE_VOICE_CMD_REAR_OFF: return "rear_off.wav";

        case OFFLINE_VOICE_CMD_SOUND_SYSTEM_ON: return "sound_system_on.wav";
        case OFFLINE_VOICE_CMD_SOUND_SYSTEM_OFF: return "sound_system_off.wav";

        case OFFLINE_VOICE_CMD_ALL_SPEAKERS_ON: return "all_speakers_on.wav";
        case OFFLINE_VOICE_CMD_ALL_SPEAKERS_OFF: return "all_speakers_off.wav";

        case OFFLINE_VOICE_CMD_HOME_THEATER_ON: return "home_theater_on.wav";
        case OFFLINE_VOICE_CMD_HOME_THEATER_OFF: return "home_theater_off.wav";

        case OFFLINE_VOICE_CMD_STATUS: return "status.wav";

        default: return "done.wav";
    }
}

static void show_offline_status_on_display(void) {
    char line1[22];
    char line2[22];

    snprintf(
        line1,
        sizeof(line1),
        "TV:%s SB:%s",
        smart_home_control_get_state(SMART_HOME_DEVICE_TV) ? "ON" : "OFF",
        smart_home_control_get_state(SMART_HOME_DEVICE_SOUNDBAR) ? "ON" : "OFF"
    );

    snprintf(
        line2,
        sizeof(line2),
        "SUB:%s REAR:%s",
        smart_home_control_get_state(SMART_HOME_DEVICE_SUBWOOFER) ? "ON" : "OFF",
        smart_home_control_get_state(SMART_HOME_DEVICE_REAR) ? "ON" : "OFF"
    );

    display_show_message("OFFLINE STATUS", line1, line2);
}

static const char* offline_status_prompt_filename(void) {
    static char filename[24];

    bool tv = smart_home_control_get_state(SMART_HOME_DEVICE_TV);
    bool sb = smart_home_control_get_state(SMART_HOME_DEVICE_SOUNDBAR);
    bool sub = smart_home_control_get_state(SMART_HOME_DEVICE_SUBWOOFER);
    bool rear = smart_home_control_get_state(SMART_HOME_DEVICE_REAR);

    snprintf(
        filename,
        sizeof(filename),
        "status_%d%d%d%d.wav",
        tv ? 1 : 0,
        sb ? 1 : 0,
        sub ? 1 : 0,
        rear ? 1 : 0
    );

    return filename;
}

static void play_offline_response(OfflineVoiceCommand command, bool ok) {
    if (!ok) {
        // Command failed = solid red.
        status_led_set_state(STATUS_LED_COMMAND_FAILED);

        if (offline_speech_play("failed.wav") != ESP_OK) {
            ESP_LOGW(TAG, "Failed prompt missing");
        }

        // Force red again after audio playback.
        status_led_set_state(STATUS_LED_COMMAND_FAILED);

    } else if (command == OFFLINE_VOICE_CMD_STATUS) {
        // Status response is successful speech.
        status_led_set_state(STATUS_LED_SPEAKING);

        const char* status_prompt = offline_status_prompt_filename();

        if (offline_speech_play(status_prompt) != ESP_OK) {
            ESP_LOGW(TAG, "Specific status prompt missing: %s", status_prompt);
            offline_speech_play("status.wav");
        }

        // Successful command/status result = green.
        status_led_set_state(STATUS_LED_COMMAND_SUCCESS);

    } else {
        // Normal successful command response = green/speaking.
        status_led_set_state(STATUS_LED_SPEAKING);

        const char* prompt = offline_command_prompt(command);

        if (offline_speech_play(prompt) != ESP_OK) {
            offline_speech_play("done.wav");
        }

        // Successful command result = green.
        status_led_set_state(STATUS_LED_COMMAND_SUCCESS);
    }

    drain_mic_frames(35);
}


// ── System state ──────────────────────────────────
static volatile DisplayState g_state = DISP_BOOTING;

static void set_state(DisplayState state) {
    g_state = state;

    display_set_state(state);

    switch (state) {
        case DISP_IDLE:
            status_led_set_state(STATUS_LED_IDLE);
            break;

        case DISP_WAKE_DETECTED:
        case DISP_LISTENING:
        case DISP_HEARING:
            // Solid current mode colour:
            // online blue, serverless purple, offline orange
            status_led_set_state(STATUS_LED_MODE_SOLID);
            break;

        case DISP_PROCESSING:
            // Breathing current mode colour
            status_led_set_state(STATUS_LED_MODE_BREATHING);
            break;

        case DISP_SPEAKING:
            // Universal speaking green
            status_led_set_state(STATUS_LED_SPEAKING);
            break;

        case DISP_RELAY_OK:
        case DISP_RELAY_RESTORED:
            // Universal success green
            status_led_set_state(STATUS_LED_COMMAND_SUCCESS);
            break;

        case DISP_ONLINE_RESTORED:
            // Server restored = solid online blue
            status_led_set_state(STATUS_LED_MODE_SOLID);
            break;

        case DISP_RELAY_FAIL:
            // Command failed = solid red
            status_led_set_state(STATUS_LED_COMMAND_FAILED);
            break;

        case DISP_ERROR:
            // System/connection failure = blinking red
            status_led_set_state(STATUS_LED_CONNECTION_FAILED);
            break;

        case DISP_SERVERLESS:
        case DISP_OFFLINE:
            // Mode notification = solid current mode colour
            status_led_set_state(STATUS_LED_MODE_SOLID);
            break;

        case DISP_CUSTOM:
            break;

        case DISP_BOOTING:
        case DISP_CONNECTING:
        default:
            status_led_set_state(STATUS_LED_IDLE);
            break;
    }
}

static void show_transition_state(DisplayState state, uint32_t duration_ms) {
    set_state(state);
    vTaskDelay(pdMS_TO_TICKS(duration_ms));
    set_state(DISP_IDLE);
}

static void drain_mic_frames(int frames) {
    int16_t dummy[256];

    for (int i = 0; i < frames; i++) {
        audio_read_wakenet_frame(dummy, 256);
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}

static int32_t calculate_rms(const int16_t* frame, int count) {
    if (count <= 0) return 0;

    int64_t sum_sq = 0;

    for (int i = 0; i < count; i++) {
        sum_sq += (int32_t)frame[i] * frame[i];
    }

    return (int32_t)sqrtf((float)sum_sq / count);
}


// ── WiFi event handler ────────────────────────────
static void wifi_event_handler(void* arg,
                                esp_event_base_t base,
                                int32_t id,
                                void* data) {
    if (base == WIFI_EVENT &&
        id == WIFI_EVENT_STA_START) {

        // Normal boot: connect automatically.
        if (!s_switching_wifi) {
            esp_wifi_connect();
        }

    } else if (base == WIFI_EVENT &&
               id == WIFI_EVENT_STA_DISCONNECTED) {

        ESP_LOGW(TAG, "WiFi disconnected");

        xEventGroupClearBits(wifi_events, WIFI_CONNECTED_BIT);
        xEventGroupSetBits(wifi_events, WIFI_DISCONNECTED_BIT);

        // We intentionally disconnected because we are switching
        // from home Wi-Fi to the ESP8266 relay AP.
        if (s_switching_wifi) {
            ESP_LOGI(TAG, "Disconnect acknowledged for WiFi switch");
            return;
        }

        // Direct AP offline mode:
        // We are already connected to ESP-REMOTE-DIRECT.
        // If it drops, reconnect to the relay AP.
        if (s_relay_ap_mode) {
            ESP_LOGW(TAG, "Direct relay AP disconnected, reconnecting");
            esp_wifi_connect();
            return;
        }

        // Home relay offline mode:
        // We were using the ESP8266 through home Wi-Fi.
        // If home Wi-Fi drops, escalate to direct AP fallback.
        if (s_home_relay_mode) {
            ESP_LOGE(TAG, "Home WiFi lost during home relay mode");
            request_direct_ap_fallback("home WiFi lost during home relay mode");
            return;
        }

        // Online mode:
        // Home Wi-Fi itself dropped while server mode was active.
        // Try once to reconnect home Wi-Fi, but also schedule direct AP fallback.
        ESP_LOGW(TAG, "Home WiFi disconnected during online mode");
        esp_wifi_connect();
        request_direct_ap_fallback("home WiFi lost during online mode");

    } else if (base == IP_EVENT &&
               id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t* event =
            (ip_event_got_ip_t*)data;

        ESP_LOGI(TAG, "IP: " IPSTR,
                 IP2STR(&event->ip_info.ip));

        xEventGroupSetBits(wifi_events,
                           WIFI_CONNECTED_BIT);
    }
}

static bool wifi_init(void) {
    wifi_events = xEventGroupCreate();
    if (!wifi_events) {
        ESP_LOGE(TAG, "Failed to create WiFi event group");
        return false;
    }

    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    if (s_sta_netif == NULL) {
    s_sta_netif = esp_netif_create_default_wifi_sta();
    }

    wifi_init_config_t cfg =
        WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    esp_event_handler_instance_t inst_any;
    esp_event_handler_instance_t inst_got_ip;

    ESP_ERROR_CHECK(
        esp_event_handler_instance_register(
            WIFI_EVENT, ESP_EVENT_ANY_ID,
            &wifi_event_handler, NULL, &inst_any));

    ESP_ERROR_CHECK(
        esp_event_handler_instance_register(
            IP_EVENT, IP_EVENT_STA_GOT_IP,
            &wifi_event_handler, NULL, &inst_got_ip));

    wifi_config_t wifi_cfg = {
        .sta = {
            .ssid     = WIFI_SSID,
            .password = WIFI_PASSWORD,
        },
    };

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(
                      WIFI_IF_STA, &wifi_cfg));
    ESP_ERROR_CHECK(esp_wifi_start());

    ESP_LOGI(TAG, "Waiting for WiFi connection...");

    EventBits_t bits = xEventGroupWaitBits(
        wifi_events,
        WIFI_CONNECTED_BIT,
        pdFALSE,
        pdTRUE,
        pdMS_TO_TICKS(20000)
    );

    if (!(bits & WIFI_CONNECTED_BIT)) {
        ESP_LOGE(TAG, "WiFi connection timeout");
        return false;
    }

    ESP_LOGI(TAG, "WiFi connected");
    return true;
}

static void configure_offline_static_ip(void) {
    if (!s_sta_netif) {
        ESP_LOGE(TAG, "STA netif not available for static IP");
        return;
    }

    esp_err_t err = esp_netif_dhcpc_stop(s_sta_netif);

    if (err != ESP_OK && err != ESP_ERR_ESP_NETIF_DHCP_ALREADY_STOPPED) {
        ESP_LOGW(TAG, "Failed to stop DHCP client: %s",
                 esp_err_to_name(err));
    }

    esp_netif_ip_info_t ip_info = {0};

    IP4_ADDR(&ip_info.ip,      192, 168, 4, 50);
    IP4_ADDR(&ip_info.gw,      192, 168, 4, 1);
    IP4_ADDR(&ip_info.netmask, 255, 255, 255, 0);

    err = esp_netif_set_ip_info(s_sta_netif, &ip_info);

    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to set serverless static IP: %s",
                 esp_err_to_name(err));
        return;
    }

    ESP_LOGI(TAG, "Serverless static IP configured: 192.168.4.50");
}

static bool wifi_connect_emergency_relay_ap(void) {
    ESP_LOGW(TAG, "Switching to emergency relay AP");

    s_switching_wifi = true;
    s_offline_mode   = true;

    xEventGroupClearBits(
        wifi_events,
        WIFI_CONNECTED_BIT | WIFI_DISCONNECTED_BIT | WIFI_FAIL_BIT
    );

    // Step 1: Cleanly disconnect from home Wi-Fi.
    esp_err_t err = esp_wifi_disconnect();

    if (err != ESP_OK &&
        err != ESP_ERR_WIFI_NOT_CONNECT) {
        ESP_LOGW(TAG, "esp_wifi_disconnect: %s",
                 esp_err_to_name(err));
    }

    xEventGroupWaitBits(
        wifi_events,
        WIFI_DISCONNECTED_BIT,
        pdTRUE,
        pdFALSE,
        pdMS_TO_TICKS(3000)
    );

    // Step 2: Fully stop Wi-Fi before applying the relay AP config.
    err = esp_wifi_stop();
    if (err != ESP_OK &&
        err != ESP_ERR_WIFI_NOT_INIT) {
        ESP_LOGW(TAG, "esp_wifi_stop: %s",
                 esp_err_to_name(err));
    }

    vTaskDelay(pdMS_TO_TICKS(1000));

    wifi_config_t relay_cfg = {0};

    strncpy((char*)relay_cfg.sta.ssid,
            RELAY_AP_SSID,
            sizeof(relay_cfg.sta.ssid) - 1);

    strncpy((char*)relay_cfg.sta.password,
            RELAY_AP_PASSWORD,
            sizeof(relay_cfg.sta.password) - 1);

    relay_cfg.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &relay_cfg));

    // Offline relay control should be reliable, not power-saving.
    esp_wifi_set_ps(WIFI_PS_NONE);

    // Avoid ESP8266 AP DHCP instability by using a static offline IP.
    configure_offline_static_ip();

    ESP_ERROR_CHECK(esp_wifi_start());

    vTaskDelay(pdMS_TO_TICKS(500));

    // Step 3: Retry relay AP connection.
    for (int attempt = 1;
         attempt <= OFFLINE_AP_MAX_RETRIES;
         attempt++) {

        ESP_LOGI(TAG,
                 "Connecting to relay AP: %s (attempt %d/%d)",
                 RELAY_AP_SSID,
                 attempt,
                 OFFLINE_AP_MAX_RETRIES);

        xEventGroupClearBits(
            wifi_events,
            WIFI_CONNECTED_BIT | WIFI_DISCONNECTED_BIT | WIFI_FAIL_BIT
        );

        err = esp_wifi_connect();

        if (err != ESP_OK) {
            ESP_LOGW(TAG,
                     "Relay AP connect call failed: %s",
                     esp_err_to_name(err));
        }

        EventBits_t bits = xEventGroupWaitBits(
            wifi_events,
            WIFI_CONNECTED_BIT | WIFI_DISCONNECTED_BIT,
            pdFALSE,
            pdFALSE,
            pdMS_TO_TICKS(OFFLINE_AP_CONNECT_TIMEOUT_MS)
        );

        if (bits & WIFI_CONNECTED_BIT) {
            s_switching_wifi = false;

            ESP_LOGI(TAG, "Connected to emergency relay AP");
            set_state(DISP_OFFLINE);
            return true;
        }

        ESP_LOGW(TAG,
                 "Relay AP attempt %d failed",
                 attempt);

        // Make sure the next attempt starts clean.
        err = esp_wifi_disconnect();

        if (err != ESP_OK &&
            err != ESP_ERR_WIFI_NOT_CONNECT) {
            ESP_LOGW(TAG,
                     "Retry disconnect: %s",
                     esp_err_to_name(err));
        }

        vTaskDelay(pdMS_TO_TICKS(OFFLINE_AP_RETRY_DELAY_MS));

        // After a couple of failed attempts, restart Wi-Fi driver.
        if (attempt == 2 || attempt == 4) {
            ESP_LOGW(TAG, "Restarting WiFi driver before retry");

            esp_wifi_stop();
            vTaskDelay(pdMS_TO_TICKS(1000));

            esp_wifi_start();
            vTaskDelay(pdMS_TO_TICKS(700));
        }
    }

    s_switching_wifi = false;

    ESP_LOGE(TAG, "Offline relay AP connection failed after retries");
    set_state(DISP_ERROR);
    return false;
}

static void request_runtime_offline_fallback(bool force) {

    // Stop online voice path immediately.
    s_online_voice_paused = true;
    set_state(DISP_IDLE);

    if (s_offline_mode || s_switching_wifi) {
        return;
    }

    if (force) {
        s_force_runtime_fallback = true;
        ESP_LOGW(TAG, "Forced runtime offline fallback requested");
    } else {
        ESP_LOGW(TAG, "Runtime offline fallback requested");
    }

    if (s_runtime_fallback_task_handle) {
        xTaskNotifyGive(s_runtime_fallback_task_handle);
    }
}

static bool server_health_check_http(void) {
    char url[96];

    snprintf(
        url,
        sizeof(url),
        "http://%s:%d/health",
        SERVER_IP,
        SERVER_PORT
    );

    esp_http_client_config_t config = {
        .url = url,
        .timeout_ms = SERVER_HEALTH_TIMEOUT_MS,
    };

    esp_http_client_handle_t client =
        esp_http_client_init(&config);

    if (!client) {
        ESP_LOGE(TAG, "Server health client init failed");
        return false;
    }

    esp_err_t err = esp_http_client_perform(client);

    if (err != ESP_OK) {
        ESP_LOGW(
            TAG,
            "Server health failed: %s",
            esp_err_to_name(err)
        );

        esp_http_client_cleanup(client);
        return false;
    }

    int status = esp_http_client_get_status_code(client);

    esp_http_client_cleanup(client);

    if (status != 200) {
        ESP_LOGW(TAG, "Server health HTTP status: %d", status);
        return false;
    }

    return true;
}

static void server_health_task(void* arg) {
    int fail_count = 0;

    ESP_LOGI(TAG, "Server health task started");

    while (true) {
        vTaskDelay(pdMS_TO_TICKS(SERVER_HEALTH_INTERVAL_MS));

        // Health monitor is only for online mode.
        if (s_offline_mode ||
            s_switching_wifi ||
            s_online_interaction_active ||
            s_home_relay_starting) {

            fail_count = 0;
            continue;
        }

        bool ok = server_health_check_http();

        if (ok) {
            if (fail_count > 0) {
                ESP_LOGI(TAG, "Server health restored while online");
            }

            fail_count = 0;
            continue;
        }

        fail_count++;

        ESP_LOGW(
            TAG,
            "Server health failed %d/%d",
            fail_count,
            SERVER_HEALTH_FAIL_LIMIT
        );

        if (fail_count >= SERVER_HEALTH_FAIL_LIMIT) {
            fail_count = 0;

            ESP_LOGE(TAG, "Server health monitor triggered fallback");

            // A /health timeout does not prove the WebSocket is dead.
            // Only actual send failure should force fallback.
            request_runtime_offline_fallback(false);

        }
    }
}

static void request_direct_ap_fallback(const char* reason) {
    if (s_switching_wifi || s_relay_ap_mode) {
        return;
    }

    ESP_LOGW(TAG, "Direct AP fallback requested: %s", reason);

    s_force_direct_ap_fallback = true;

    if (s_runtime_fallback_task_handle) {
        xTaskNotifyGive(s_runtime_fallback_task_handle);
    }
}

static void websocket_lost_callback(void) {
    request_runtime_offline_fallback(false);
}

static bool serverless_ha_hub_is_alive(void) {
    if (!s_serverless_ha_mode) {
        return false;
    }

    SmartHomeBackend old_backend = smart_home_control_get_backend();

    smart_home_control_set_backend(SMART_HOME_BACKEND_HOME_ASSISTANT);

    bool alive = smart_home_control_is_available();

    smart_home_control_set_backend(old_backend);

    return alive;
}

static void handle_offline_voice_command(
    OfflineVoiceCommand command,
    const char* phrase,
    float probability
) { 
    s_offline_command_active = true;

    if (!s_offline_mode || s_switching_wifi) {
        ESP_LOGW(TAG, "Ignoring offline voice command outside offline mode");
        s_offline_command_active = false;
        return;
    }

    ESP_LOGI(
        TAG,
        "Offline voice command: id=%d phrase='%s' prob=%.3f",
        command,
        phrase ? phrase : "",
        probability
    );

    display_show_message(
        "OFFLINE COMMAND",
        offline_command_label(command),
        "WORKING"
    );

    set_state(DISP_PROCESSING);

    bool ok = false;

    switch (command) {
        case OFFLINE_VOICE_CMD_TV_ON:
            ok = smart_home_control_set_device(
                SMART_HOME_DEVICE_TV,
                SMART_HOME_ACTION_ON
            );
            break;

        case OFFLINE_VOICE_CMD_TV_OFF:
            ok = smart_home_control_set_device(
                SMART_HOME_DEVICE_TV,
                SMART_HOME_ACTION_OFF
            );
            break;

        case OFFLINE_VOICE_CMD_TV_TOGGLE:
            ok = smart_home_control_toggle_device(SMART_HOME_DEVICE_TV);
            break;

        case OFFLINE_VOICE_CMD_SOUNDBAR_ON:
            ok = smart_home_control_set_device(
                SMART_HOME_DEVICE_SOUNDBAR,
                SMART_HOME_ACTION_ON
            );
            break;

        case OFFLINE_VOICE_CMD_SOUNDBAR_OFF:
            ok = smart_home_control_set_device(
                SMART_HOME_DEVICE_SOUNDBAR,
                SMART_HOME_ACTION_OFF
            );
            break;

        case OFFLINE_VOICE_CMD_SOUNDBAR_TOGGLE:
            ok = smart_home_control_toggle_device(SMART_HOME_DEVICE_SOUNDBAR);
            break;

        case OFFLINE_VOICE_CMD_SUBWOOFER_ON:
            ok = smart_home_control_set_device(
                SMART_HOME_DEVICE_SUBWOOFER,
                SMART_HOME_ACTION_ON
            );
            break;

        case OFFLINE_VOICE_CMD_SUBWOOFER_OFF:
            ok = smart_home_control_set_device(
                SMART_HOME_DEVICE_SUBWOOFER,
                SMART_HOME_ACTION_OFF
            );
            break;

        case OFFLINE_VOICE_CMD_SUBWOOFER_TOGGLE:
            ok = smart_home_control_toggle_device(SMART_HOME_DEVICE_SUBWOOFER);
            break;

        case OFFLINE_VOICE_CMD_REAR_ON:
            ok = smart_home_control_set_device(
                SMART_HOME_DEVICE_REAR,
                SMART_HOME_ACTION_ON
            );
            break;

        case OFFLINE_VOICE_CMD_REAR_OFF:
            ok = smart_home_control_set_device(
                SMART_HOME_DEVICE_REAR,
                SMART_HOME_ACTION_OFF
            );
            break;

        case OFFLINE_VOICE_CMD_REAR_TOGGLE:
            ok = smart_home_control_toggle_device(SMART_HOME_DEVICE_REAR);
            break;

        case OFFLINE_VOICE_CMD_SOUND_SYSTEM_ON:
            ok = smart_home_control_set_sound_system(SMART_HOME_ACTION_ON);
            break;

        case OFFLINE_VOICE_CMD_SOUND_SYSTEM_OFF:
            ok = smart_home_control_set_sound_system(SMART_HOME_ACTION_OFF);
            break;

        case OFFLINE_VOICE_CMD_ALL_SPEAKERS_ON:
            ok = smart_home_control_set_all_speakers(SMART_HOME_ACTION_ON);
            break;

        case OFFLINE_VOICE_CMD_ALL_SPEAKERS_OFF:
            ok = smart_home_control_set_all_speakers(SMART_HOME_ACTION_OFF);
            break;

        case OFFLINE_VOICE_CMD_HOME_THEATER_ON:
            ok = smart_home_control_set_home_theater(SMART_HOME_ACTION_ON);
            break;

        case OFFLINE_VOICE_CMD_HOME_THEATER_OFF:
            ok = smart_home_control_set_home_theater(SMART_HOME_ACTION_OFF);
            break;

        case OFFLINE_VOICE_CMD_STATUS:
            ok = smart_home_control_fetch_status();
            break;

        default:
            ESP_LOGW(TAG, "Unknown offline voice command: %d", command);
            ok = false;
            break;
    }

    if (ok) {
        ESP_LOGI(TAG, "Offline voice command executed successfully");

        if (command == OFFLINE_VOICE_CMD_STATUS) {
            show_offline_status_on_display();
        } else {
            display_show_message(
                "COMMAND DONE",
                offline_command_label(command),
                "SUCCESS"
            );
        }
    } else {
        ESP_LOGE(TAG, "Offline voice command failed");

        // Command failed = solid red in every mode.
        status_led_set_state(STATUS_LED_COMMAND_FAILED);

        if (s_serverless_ha_mode) {
            if (serverless_ha_hub_is_alive()) {
                ESP_LOGW(
                    TAG,
                    "HA hub is alive. Staying in Serverless HA mode. Target HA entity may be unavailable."
                );

                display_show_message(
                    "COMMAND UNDERSTOOD",
                    "HA DEVICE",
                    "UNAVAILABLE"
                );
            } else {
                ESP_LOGW(
                    TAG,
                    "HA hub is unavailable. Leaving Serverless HA mode."
                );

                // HA hub itself is dead, so firmware should fall back to direct relay.
                if (start_home_relay_mode("Home Assistant hub unavailable")) {
                    display_show_message(
                        "SERVERLESS",
                        "RELAY MODE",
                        "ACTIVE"
                    );
                } else {
                    display_show_message(
                        "COMMAND FAILED",
                        "HA + RELAY",
                        "UNAVAILABLE"
                    );
                }
            }
        } else {
            display_show_message(
                "COMMAND FAILED",
                offline_command_label(command),
                "CHECK CONNECTION"
            );
        }
    }

    play_offline_response(command, ok);

    if (s_offline_mode) {
        if (command == OFFLINE_VOICE_CMD_STATUS && ok) {
            vTaskDelay(pdMS_TO_TICKS(2500));
        } else if (!ok) {
            // Keep solid red visible a bit longer for failed commands.
            vTaskDelay(pdMS_TO_TICKS(1800));
        } else {
            vTaskDelay(pdMS_TO_TICKS(800));
        }

        set_state(DISP_IDLE);
    }

    s_offline_command_active = false;

}

static bool start_offline_mode(const char* reason) {
    if (s_offline_mode && s_relay_ap_mode && !s_switching_wifi) {
        ESP_LOGW(TAG, "Direct AP offline mode already active");
        return true;
    }

    ESP_LOGW(TAG, "Entering direct AP emergency relay mode: %s", reason);

    // Direct offline AP mode owns the mic.
    s_offline_mode = true;
    s_home_relay_mode = false;
    s_serverless_ha_mode = false;
    s_relay_ap_mode = true;
    s_online_voice_paused = true;

    ws_client_stop();

    smart_home_control_set_backend(SMART_HOME_BACKEND_RELAY_AP);

    // First connect to ESP8266 direct AP.
    // Do not start offline voice while Wi-Fi is switching.
    if (!wifi_connect_emergency_relay_ap()) {
        ESP_LOGE(TAG, "Failed to enter direct AP emergency relay mode");

        s_relay_ap_mode = false;
        s_offline_mode = false;

        // Connection failed = blinking red
        set_state(DISP_ERROR);
        return false;
    }

    // Set offline colour before showing notification.
    status_led_set_mode(STATUS_LED_MODE_OFFLINE);

    // Show OFFLINE / RELAY MODE once.
    show_transition_state(DISP_OFFLINE, 1400);

    // Give I2S/mic a small settling gap after Wi-Fi switching.
    drain_mic_frames(12);
    vTaskDelay(pdMS_TO_TICKS(300));

    if (s_smart_home_control_task_handle == NULL) {
        xTaskCreatePinnedToCore(
            smart_home_control_task,
            "SmartHome",
            8192,
            NULL,
            4,
            &s_smart_home_control_task_handle,
            0
        );
    }

    // Start offline voice only after AP switch + notification + mic drain.
    offline_voice_set_ui_callback(handle_offline_voice_ui);
    offline_voice_start(handle_offline_voice_command);

    ESP_LOGI(TAG, "ZYRA switched to direct AP emergency relay mode");
    return true;
}

static bool switch_serverless_ha_to_home_relay(const char* reason) {
    ESP_LOGW(TAG, "Switching Serverless HA -> Serverless Relay: %s", reason);

    s_runtime_mode_switching = true;

    smart_home_control_set_backend(SMART_HOME_BACKEND_RELAY_HOME);

    if (!smart_home_control_fetch_status()) {
        ESP_LOGE(TAG, "Home relay unavailable after HA failure");

        s_runtime_mode_switching = false;
        smart_home_control_set_backend(SMART_HOME_BACKEND_HOME_ASSISTANT);

        display_show_message(
            "HA FAILED",
            "RELAY ALSO",
            "UNAVAILABLE"
        );

        vTaskDelay(pdMS_TO_TICKS(1800));
        set_state(DISP_IDLE);

        return false;
    }

    s_offline_mode = true;
    s_home_relay_mode = true;
    s_serverless_ha_mode = false;
    s_relay_ap_mode = false;
    s_online_voice_paused = true;

    status_led_set_mode(STATUS_LED_MODE_SERVERLESS);
    status_led_set_state(STATUS_LED_MODE_SOLID);


    display_show_message(
        "SERVERLESS",
        "RELAY MODE",
        "ACTIVE"
    );

    vTaskDelay(pdMS_TO_TICKS(1800));

    // Return display to idle
    set_state(DISP_IDLE);

    ESP_LOGW(TAG, "Serverless Relay mode active after HA failure");

    s_runtime_mode_switching = false;
    return true;
}

static bool start_serverless_ha_mode(const char* reason) {
    ESP_LOGW(TAG, "Entering serverless HA mode: %s", reason);

    s_offline_mode = true;
    s_serverless_ha_mode = true;
    s_home_relay_mode = false;
    s_relay_ap_mode = false;
    s_online_voice_paused = true;

    smart_home_control_set_backend(SMART_HOME_BACKEND_HOME_ASSISTANT);

    // First check HA hub.
    // If HA is OFF, do not check /api/states for every entity.
    if (!smart_home_control_is_available()) {
        ESP_LOGW(TAG, "Home Assistant hub is not reachable");

        s_serverless_ha_mode = false;
        s_offline_mode = false;

        smart_home_control_set_backend(SMART_HOME_BACKEND_RELAY_HOME);
        return false;
    }

    // HA hub is alive. Entity status may still be unavailable.
    // That should NOT make us leave HA mode.
    if (!smart_home_control_fetch_status()) {
        ESP_LOGW(
            TAG,
            "HA hub is alive, but configured HA entities may be unavailable. Staying in Serverless HA mode."
        );
    }

    status_led_set_mode(STATUS_LED_MODE_SERVERLESS);
    status_led_set_state(STATUS_LED_MODE_SOLID);

    display_show_message(
        "SERVERLESS",
        "HOME ASSISTANT",
        "MODE"
    );

    vTaskDelay(pdMS_TO_TICKS(1800));

    set_state(DISP_IDLE);

    offline_voice_set_ui_callback(handle_offline_voice_ui);
    offline_voice_start(handle_offline_voice_command);

    ESP_LOGI(TAG, "ZYRA switched to serverless HA mode");
    return true;
}

static bool start_home_relay_mode(const char* reason) {
    if (s_offline_mode && s_home_relay_mode && !s_switching_wifi) {
        ESP_LOGW(TAG, "Serverless relay mode already active");
        return true;
    }

    ESP_LOGW(TAG, "Entering serverless relay mode: %s", reason);
    s_home_relay_starting = true;

    s_offline_mode = true;
    s_home_relay_mode = true;
    s_serverless_ha_mode = false;
    s_relay_ap_mode = false;
    s_online_voice_paused = true;

    
    // If HA mode failed before this, backend may still be HOME_ASSISTANT.
    // So force it back to RELAY_HOME.
    smart_home_control_set_backend(SMART_HOME_BACKEND_RELAY_HOME);

    // Confirm ESP8266 is reachable on home Wi-Fi.
    if (!smart_home_control_fetch_status()) {
        ESP_LOGW(TAG, "Home relay IP not reachable");

        s_home_relay_mode = false;
        s_offline_mode = false;
        s_home_relay_starting = false;

        return false;
    }

    status_led_set_mode(STATUS_LED_MODE_SERVERLESS);

    show_transition_state(DISP_SERVERLESS, 2200);

    if (!s_offline_mode ||
        !s_home_relay_mode ||
        s_relay_ap_mode ||
        ws_is_connected()) {

        ESP_LOGW(
            TAG,
            "Serverless relay startup cancelled because online mode is active again"
        );

        s_home_relay_mode = false;
        s_home_relay_starting = false;
        return true;
    }

    if (s_smart_home_control_task_handle == NULL) {
        xTaskCreatePinnedToCore(
            smart_home_control_task,
            "SmartHome",
            8192,
            NULL,
            4, 
            &s_smart_home_control_task_handle,
            0
        );
    }

    offline_voice_set_ui_callback(handle_offline_voice_ui);
    offline_voice_start(handle_offline_voice_command);

    s_home_relay_starting = false;

    ESP_LOGI(TAG, "ZYRA switched to serverless relay mode");
    return true;
}

static bool start_best_serverless_mode(const char* reason) {
    if (s_runtime_mode_switching) {
        ESP_LOGW(TAG, "Runtime mode switch already running");
        return true;
    }

    s_runtime_mode_switching = true;

    ESP_LOGW(TAG, "Choosing best serverless mode: %s", reason);

    bool ok = false;

    if (start_serverless_ha_mode(reason)) {
        ok = true;
        goto done;
    }

    ESP_LOGW(TAG, "Serverless HA unavailable. Trying home relay mode.");

    if (start_home_relay_mode(reason)) {
        ok = true;
        goto done;
    }

    ESP_LOGW(TAG, "Home relay unavailable. Trying emergency AP mode.");

    ok = start_offline_mode(reason);

done:
    s_runtime_mode_switching = false;
    return ok;
}

static void runtime_fallback_task(void* arg) {
    while (true) {
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);

        if (s_switching_wifi) {
            continue;
        }

        if (s_force_direct_ap_fallback) {
            s_force_direct_ap_fallback = false;

            ESP_LOGE(TAG, "Home WiFi unavailable, switching to offline mode");

            // Clear home relay state before switching to direct AP.
            s_offline_mode = false;
            s_home_relay_mode = false;
            s_relay_ap_mode = false;

            start_offline_mode("home WiFi unavailable");
            continue;
        }

        if (s_offline_mode) {
            continue;
        }

        bool forced = s_force_runtime_fallback;
        s_force_runtime_fallback = false;

        if (!forced) {
            // Small grace delay for normal disconnect events.
            vTaskDelay(pdMS_TO_TICKS(1500));

            if (s_offline_mode || s_switching_wifi) {
                continue;
            }

            if (ws_is_connected()) {
                ESP_LOGI(TAG, "Server reconnected before fallback");
                continue;
            }
        } else {
            // Send failure is hard proof. Do not wait long.
            vTaskDelay(pdMS_TO_TICKS(200));
        }

        if (s_offline_mode || s_switching_wifi) {
            continue;
        }

        const char* log_reason = forced
            ? "Server send failed during online mode"
            : "Server lost during online mode";

        const char* offline_reason = forced
            ? "audio send failed during online mode"
            : "server disconnected during online mode";

        ESP_LOGE(TAG, "%s", log_reason);

        start_best_serverless_mode(offline_reason);
    }
}

static void server_reconnect_task(void* arg) {
    while (true) {
        vTaskDelay(pdMS_TO_TICKS(15000));

        // Only try reconnecting when we are in home relay fallback.
        // In direct AP mode, Zyra is no longer on home Wi-Fi.
        if (!s_offline_mode || (!s_home_relay_mode && !s_serverless_ha_mode) ||
            s_relay_ap_mode) {
            continue;
        }

        if (s_switching_wifi || s_home_relay_starting || s_online_interaction_active
         || s_runtime_mode_switching) {
            continue;
        }

        if (ws_is_connected()) {
            continue;
        }

        ESP_LOGI(TAG, "Checking if ZYRA server is back...");

        esp_err_t err = ws_client_init(SERVER_IP, SERVER_PORT);

        if (err != ESP_OK) {
            ESP_LOGW(TAG, "Server still unavailable");
            continue;
        }

        ESP_LOGI(TAG, "Server restored. Switching back to online mode.");

        s_online_voice_paused = true;

        offline_voice_stop();

        vTaskDelay(pdMS_TO_TICKS(500));

        // Server restored.
        // Current mode colour = blue again.
        status_led_set_mode(STATUS_LED_MODE_ONLINE);

        // Show ONLINE / SERVER BACK while online task is still blocked.
        show_transition_state(DISP_ONLINE_RESTORED, 2500);

        // Now release online mode after the user has seen the notification.
        s_offline_mode = false;
        s_home_relay_mode = false;
        s_serverless_ha_mode = false;
        s_relay_ap_mode = false;
        s_force_runtime_fallback = false;
        s_force_direct_ap_fallback = false;
        s_online_voice_paused = false;

        smart_home_control_set_backend(SMART_HOME_BACKEND_RELAY_HOME);

        set_state(DISP_IDLE);

        // If Zyra task was never started because server was down during boot,
        // start it now.
        if (s_zyra_task_handle == NULL) {
            xTaskCreatePinnedToCore(
                zyra_task,
                "ZYRA",
                16384,
                NULL,
                5,
                &s_zyra_task_handle,
                0
            );
        }
    }
}

// ── VAD-only voice pipeline task ──────────────────
static void zyra_task(void* param) {
    ESP_LOGI(TAG, "ZYRA task started");
    set_state(DISP_IDLE);

    int retries = 0;
    while (!ws_is_connected() && retries < 30) {
        vTaskDelay(pdMS_TO_TICKS(1000));
        retries++;
    }
    if (!ws_is_connected()) {
        ESP_LOGE(TAG, "Server not connected");
        set_state(DISP_ERROR);
        vTaskDelete(NULL);
        return;
    }

    ESP_LOGI(TAG, "ZYRA ready — listening for speech");

    // ── VAD settings ──────────────────────────────
    // Frame = 256 samples @ 16kHz = 16ms per frame
    #define VAD_SPEECH_THRESHOLD       1400  // must exceed to count as speech
    #define VAD_SILENCE_THRESHOLD      1200  // below this = silence
    #define VAD_TRIGGER_FRAMES            5  // ~160ms continuous speech to trigger
    #define VAD_SPEECH_FRAMES_MIN        12  // ~400ms real speech required
    #define VAD_SILENCE_FRAMES_END       85  // ~1.36s silence needed before ending capture
    #define VAD_MAX_CAPTURE_MS         6000  // keep 6s for longer questions
    #define VAD_MIN_CAPTURE_BYTES      8000  // 0.5 sec at 16kHz 16-bit 
    #define VAD_POST_SPEAK_COOLDOWN_MS  900  // prevent self-trigger after speaking
    #define VAD_WAKE_COMMAND_TIMEOUT_MS 6500  // max time to wait for command after wake word

    size_t max_capture = (CAPTURE_SAMPLE_RATE * 2 * VAD_MAX_CAPTURE_MS) / 1000;

    ESP_LOGI(TAG, "Allocating capture buffer: %zu bytes", max_capture);
    ESP_LOGI(TAG, "Free internal heap before alloc: %u",
            (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL));
    ESP_LOGI(TAG, "Free PSRAM before alloc: %u",
            (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM));

    uint8_t* capture_buf = heap_caps_malloc(max_capture, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);

    if (!capture_buf) {
        ESP_LOGW(TAG, "PSRAM allocation failed, trying internal RAM");
        capture_buf = heap_caps_malloc(max_capture, MALLOC_CAP_8BIT);
    }

    if (!capture_buf) {
        ESP_LOGE(TAG, "Failed to alloc capture buffer: %zu bytes", max_capture);
        ESP_LOGE(TAG, "Free internal heap: %u", (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL));
        ESP_LOGE(TAG, "Free PSRAM heap: %u", (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
        vTaskDelete(NULL);
        return;
    }

    ESP_LOGI(TAG, "Capture buffer allocated successfully");
    int16_t frame[256];

    while (true) {

        if (s_online_voice_paused || s_offline_mode || s_switching_wifi) {
            vTaskDelay(pdMS_TO_TICKS(250));
            continue;
        }

        if (!ws_is_connected()) {
            request_runtime_offline_fallback(false);
            vTaskDelay(pdMS_TO_TICKS(500));
            continue;
        }

        // ── PHASE 0: Wait for wake word ───────────────────
        set_state(DISP_IDLE);

        if (!wakeword_wait_blocking_abortable(online_wake_should_abort)) {
            if (s_online_voice_paused || s_offline_mode || s_switching_wifi) {
                set_state(DISP_IDLE);
                vTaskDelay(pdMS_TO_TICKS(200));
                continue;
            }

            set_state(DISP_ERROR);
            vTaskDelay(pdMS_TO_TICKS(500));
            continue;
        }

        set_state(DISP_WAKE_DETECTED);

        // Avoid capturing the tail of "Jarvis" as the command.
        vTaskDelay(pdMS_TO_TICKS(250));
        drain_mic_frames(8);

        // If fallback started just after wakeword,
        // stop online command capture immediately.
        if (s_online_voice_paused || s_offline_mode || s_switching_wifi) {
            set_state(DISP_IDLE);
            continue;
        }

        // ── PHASE 1: Wait for speech immediately ─
        // Small circle means Zyra is ready and actually checking mic frames.
        set_state(DISP_WAKE_DETECTED);

        int pre_speech_frames = 0;
        bool speech_detected = false;
        TickType_t speech_wait_start = xTaskGetTickCount();

        while (true) {

            if (s_online_voice_paused || s_offline_mode || s_switching_wifi) {
                set_state(DISP_IDLE);
                speech_detected = false;
                break;
            }

            uint32_t elapsed_ms =
                (xTaskGetTickCount() - speech_wait_start) * portTICK_PERIOD_MS;

            if (elapsed_ms >= VAD_WAKE_COMMAND_TIMEOUT_MS) {
                ESP_LOGW(TAG, "No speech after wake word — returning to idle");
                set_state(DISP_IDLE);
                drain_mic_frames(6);
                break;
            }

            int count = audio_read_wakenet_frame(frame, 256);
            if (count <= 0) {
                vTaskDelay(pdMS_TO_TICKS(5));
                continue;
            }

            int32_t rms = calculate_rms(frame, count);

            if (rms > VAD_SPEECH_THRESHOLD) {
                pre_speech_frames++;

                if (pre_speech_frames >= VAD_TRIGGER_FRAMES) {
                    ESP_LOGI(TAG, "Speech detected (RMS=%" PRId32 ")", rms);
                    speech_detected = true;
                    break;
                }
            } else {
                pre_speech_frames = 0;
            }
        }

        if (!speech_detected) {
            continue;
        }

        // ── PHASE 2: Capture utterance ─────────────
        // Big moving circle only starts after real speech is detected.
        set_state(DISP_HEARING);

        size_t captured    = 0;
        int silence_frames = 0;
        int speech_frames  = VAD_TRIGGER_FRAMES; // We already have these frames from the trigger

        // Pre-fill with the triggering frame
        for (int i = 0; i < 256 && captured + 2 <= max_capture; i++) {
            capture_buf[captured++] = frame[i] & 0xFF;
            capture_buf[captured++] = (frame[i] >> 8) & 0xFF;
        }

        TickType_t capture_start = xTaskGetTickCount();

        while (captured < max_capture) {

            if (s_online_voice_paused || s_offline_mode || s_switching_wifi) {
                set_state(DISP_IDLE);
                captured = 0;
                break;
            }

            // Hard time cap — never exceed VAD_MAX_CAPTURE_MS
            uint32_t elapsed_ms = (xTaskGetTickCount() - capture_start)
                                  * portTICK_PERIOD_MS;
            if (elapsed_ms >= VAD_MAX_CAPTURE_MS) {
                ESP_LOGW(TAG, "Hit max capture time (%dms)", VAD_MAX_CAPTURE_MS);
                break;
            }

            int count = audio_read_wakenet_frame(frame, 256);
            if (count <= 0) continue;

            // Store samples
            for (int i = 0; i < count && captured + 2 <= max_capture; i++) {
                capture_buf[captured++] = frame[i] & 0xFF;
                capture_buf[captured++] = (frame[i] >> 8) & 0xFF;
            }

            // RMS for this frame
            int32_t rms = calculate_rms(frame, count);
            
            
            if (rms > VAD_SPEECH_THRESHOLD) {
                speech_frames++;
                silence_frames = 0;
            } else if (rms < VAD_SILENCE_THRESHOLD) {
                silence_frames++;

                // End capture when enough silence is detected without waiting for speech_frames to complete.
                if (silence_frames >= VAD_SILENCE_FRAMES_END) {
                    ESP_LOGI(TAG,
                        "End of utterance — %zu bytes, "
                        "%d speech frames, %d silence frames",
                        captured, speech_frames, silence_frames);
                    break;
                }
            } else {
                silence_frames = 0;
            }
            // No delay here — read as fast as I2S provides frames
        }

        if (s_online_voice_paused || s_offline_mode || s_switching_wifi) {
            set_state(DISP_IDLE);
            continue;
        }

        // Reject if not enough real speech
        if (speech_frames < VAD_SPEECH_FRAMES_MIN ||
            captured < VAD_MIN_CAPTURE_BYTES) {
            ESP_LOGW(TAG,
                "Rejected — speech_frames=%d captured=%zu",
                speech_frames,
                captured);
            set_state(DISP_IDLE);
            continue;
        }

        // ── PHASE 3: Send to server ────────────────
        set_state(DISP_PROCESSING);
        s_online_interaction_active = true;
        ws_clear_response_result();
        ESP_LOGI(TAG, "Sending %zu bytes (%dms of audio) to server",
                 captured,
                 (int)((captured / 2) * 1000 / CAPTURE_SAMPLE_RATE));

        esp_err_t err = ws_send_audio(capture_buf, captured);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "Send failed — server may be offline");

            s_online_interaction_active = false;

            request_runtime_offline_fallback(true);
            set_state(DISP_OFFLINE);
            continue;
        }

        // ── PHASE 4 + 5: Streamed response playback ───────
        // New streaming design:
        // websocket_client.c receives audio frames and pushes them directly
        // to audio_streamer. main.c only controls UI and waits for final completion.

        int timeout = 0;
        bool speaking_ui_started = false;

        while (!ws_response_ready() && timeout < 12000) {
            if (!ws_is_connected()) {
                ESP_LOGE(TAG, "Server disconnected while waiting for streamed response");

                s_online_interaction_active = false;
                request_runtime_offline_fallback(true);
                break;
            }

            // Audio can start before ws_response_ready() becomes true.
            if (!speaking_ui_started && ws_audio_stream_active()) {
                bool online_response_failed = ws_last_response_failed();

                if (online_response_failed) {
                    status_led_set_state(STATUS_LED_COMMAND_FAILED);

                    display_show_message(
                        "COMMAND FAILED",
                        "SMART HOME",
                        "UNAVAILABLE"
                    );
                } else {
                    set_state(DISP_SPEAKING);
                    status_led_set_state(STATUS_LED_COMMAND_SUCCESS);
                }

                ws_clear_audio_stream_active();
                speaking_ui_started = true;
            }

            vTaskDelay(pdMS_TO_TICKS(10));
            timeout++;
        }

        if (!ws_response_ready()) {
            ESP_LOGW(TAG, "Streamed response timeout");

            s_online_interaction_active = false;

            if (!ws_is_connected()) {
                set_state(DISP_OFFLINE);
            } else {
                set_state(DISP_IDLE);
            }

            break;
        }

        bool online_response_failed = ws_last_response_failed();
        bool final_part = ws_response_final();

        ESP_LOGI(
            TAG,
            "Final streamed response ready failed=%d final=%d",
            online_response_failed ? 1 : 0,
            final_part ? 1 : 0
        );

        // Fallback UI update.
        if (!speaking_ui_started) {
            if (online_response_failed) {
                status_led_set_state(STATUS_LED_COMMAND_FAILED);

                display_show_message(
                    "COMMAND FAILED",
                    "SMART HOME",
                    "UNAVAILABLE"
                );
            } else {
                set_state(DISP_SPEAKING);
                status_led_set_state(STATUS_LED_COMMAND_SUCCESS);
            }

            speaking_ui_started = true;
        }

        // Legacy fallback check.
        // Normally streamed audio has already been played by audio_streamer.
        // But if some old non-streamed path returns a full buffer, play it here.
        uint8_t* audio_data = NULL;
        int sr = 22050;
        size_t audio_len = ws_get_response(&audio_data, &sr);

        if (audio_data && audio_len > 0) {
            ESP_LOGW(
                TAG,
                "Legacy buffered playback path used: %zu bytes at %dHz",
                audio_len,
                sr
            );

            audio_play_response(audio_data, audio_len, sr);
        } else {
            // Normal streaming path.
            // Wait until audio_streamer has consumed all queued PCM blocks.
            ESP_LOGI(TAG, "Waiting for streamed audio playback to finish");
            audio_streamer_wait_idle(45000);
        }

        // Clean final response state only after playback has finished.
        ws_free_response();
        ws_clear_response_result();

        if (online_response_failed) {
            // Keep red visible briefly after failed speech finishes.
            status_led_set_state(STATUS_LED_COMMAND_FAILED);
            vTaskDelay(pdMS_TO_TICKS(900));
        } else {
            status_led_set_state(STATUS_LED_COMMAND_SUCCESS);
        }

        s_online_interaction_active = false; 

        // Give speaker output time to settle before listening again.
        vTaskDelay(pdMS_TO_TICKS(VAD_POST_SPEAK_COOLDOWN_MS));

        // Clear leftover mic/I2S frames so Zyra does not hear itself.
        drain_mic_frames(8);

        set_state(DISP_IDLE);

    }

    free(capture_buf);
    vTaskDelete(NULL);
}   

static void handle_offline_voice_ui(
    OfflineVoiceUiEvent event
) {
    switch (event) {
        case OFFLINE_VOICE_UI_IDLE:
            set_state(DISP_IDLE);
            break;

        case OFFLINE_VOICE_UI_LISTENING:
            set_state(DISP_LISTENING);
            break;

        case OFFLINE_VOICE_UI_HEARING:
            set_state(DISP_HEARING);
            break;

        case OFFLINE_VOICE_UI_THINKING:
            set_state(DISP_PROCESSING);
            break;

        case OFFLINE_VOICE_UI_SPEAKING:
            // Offline speaking should only affect LED, not OLED.
            status_led_set_state(STATUS_LED_SPEAKING);
            break;

        case OFFLINE_VOICE_UI_ERROR:
            set_state(DISP_ERROR);
            break;

        default:
            set_state(DISP_IDLE);
            break;
    }
}

static void smart_home_control_task(void* param) {
    ESP_LOGI(TAG, "Offline relay task started");

    // Do NOT call smart_home_control_init() here.
    // Why:
    // smart_home_control_init() should run once during boot.
    // This task should only monitor the currently selected backend.

    if (s_runtime_mode_switching) {
        ESP_LOGW(TAG, "Offline relay task waiting because runtime mode is switching");
        vTaskDelay(pdMS_TO_TICKS(1000));
    }

    if (smart_home_control_fetch_status()) {
        ESP_LOGI(TAG, "Offline relay status synced");

        s_relay_error_active = false;
        s_relay_fail_count = 0;
        s_relay_ok_count = 0;
    } else {
        ESP_LOGE(TAG, "Offline relay status sync failed");

        // Connection failed = blinking red
        s_relay_error_active = true;
        s_relay_fail_count = 2;
        s_relay_ok_count = 0;

        set_state(DISP_ERROR);
    }

    while (true) {
        vTaskDelay(pdMS_TO_TICKS(10000));

        if (s_switching_wifi || s_runtime_mode_switching) {
            continue;
        }

        if (!s_offline_mode) {
            ESP_LOGI(TAG, "Offline relay task stopping because online mode returned");
            s_smart_home_control_task_handle = NULL;
            vTaskDelete(NULL);
            return;
        }

        if (s_serverless_ha_mode) {
            continue;
        }

        bool relay_ok = smart_home_control_fetch_status();

        if (!relay_ok) {
            s_relay_ok_count = 0;
            s_relay_fail_count++;

            ESP_LOGW(TAG, "Relay health failed %d/2", s_relay_fail_count);

            if (s_relay_fail_count >= 2 && !s_relay_error_active) {
                s_relay_error_active = true;

                // Connection failed = blinking red
                set_state(DISP_ERROR);
            }

            continue;
        }

        // Relay is reachable
        s_relay_fail_count = 0;

        if (s_relay_error_active) {
            s_relay_ok_count++;

            ESP_LOGI(TAG, "Relay health restored %d/2", s_relay_ok_count);

            if (s_relay_ok_count >= 2) {
                s_relay_error_active = false;
                s_relay_ok_count = 0;

                // Temporary relay restored notification
                show_transition_state(DISP_RELAY_RESTORED, 1000);
            }
        } else {
            ESP_LOGI(TAG, "Offline relay live");
        }
    }
}


static void boot_serverless_fallback_task(void* arg) {
    const char* reason = (const char*)arg;

    ESP_LOGW(TAG, "Boot fallback task started: %s", reason);

    start_best_serverless_mode(reason);

    ESP_LOGW(TAG, "Boot fallback task finished");

    vTaskDelete(NULL);
}

static bool switch_home_relay_to_serverless_ha(const char* reason) {
    ESP_LOGI(TAG, "HA restore probe: %s", reason);

    // First check HA without changing current relay backend.
    if (!smart_home_control_home_assistant_available()) {
        ESP_LOGW(TAG, "HA restore check failed; staying in relay mode");
        return false;
    }

    ESP_LOGW(TAG, "Home Assistant restored. Switching Serverless Relay -> Serverless HA");

    s_runtime_mode_switching = true;

    // Now it is safe to actually switch mode/backend.
    smart_home_control_set_backend(SMART_HOME_BACKEND_HOME_ASSISTANT);

    if (!smart_home_control_fetch_status()) {
        ESP_LOGW(
            TAG,
            "HA restored, but configured target HA entities may be unavailable"
        );
    }

    s_offline_mode = true;
    s_home_relay_mode = false;
    s_serverless_ha_mode = true;
    s_relay_ap_mode = false;
    s_online_voice_paused = true;

    status_led_set_mode(STATUS_LED_MODE_SERVERLESS);

    display_show_message(
        "SERVERLESS",
        "HOME ASSISTANT",
        "RESTORED"
    );

    status_led_set_state(STATUS_LED_COMMAND_SUCCESS);

    vTaskDelay(pdMS_TO_TICKS(1800));
    set_state(DISP_IDLE);

    ESP_LOGW(TAG, "Serverless HA mode restored from relay mode");

    s_runtime_mode_switching = false;
    return true;
}

static void home_assistant_restore_task(void* arg) {
    ESP_LOGW(TAG, "HA monitor task started");

    while (true) {
        vTaskDelay(pdMS_TO_TICKS(10000));

        ESP_LOGI(
            TAG,
            "HA monitor tick: offline=%d relay=%d ha=%d ap=%d switching=%d cmd=%d",
            s_offline_mode,
            s_home_relay_mode,
            s_serverless_ha_mode,
            s_relay_ap_mode,
            s_runtime_mode_switching,
            s_offline_command_active
        );

        if (!s_offline_mode ||
            s_relay_ap_mode ||
            s_runtime_mode_switching ||
            s_switching_wifi ||
            s_online_interaction_active ||
            s_offline_command_active) {
            continue;
        }

        // Case 1: We are in Serverless HA mode.
        // Check whether HA hub died.
        if (s_serverless_ha_mode && !s_home_relay_mode) {
            ESP_LOGW(TAG, "HA monitor: checking active Home Assistant hub");

            if (!smart_home_control_home_assistant_available()) {
                ESP_LOGW(TAG, "HA monitor: Home Assistant hub is unavailable");

                switch_serverless_ha_to_home_relay("Home Assistant hub unavailable");
            }

            continue;
        }

        // Case 2: We are in Serverless Relay mode.
        // Check whether HA hub came back.
        if (s_home_relay_mode && !s_serverless_ha_mode) {
            ESP_LOGW(TAG, "HA monitor: checking Home Assistant restore");

            if (switch_home_relay_to_serverless_ha("Home Assistant restored")) {
                ESP_LOGW(TAG, "HA monitor: switched back to Serverless HA mode");
            }

            continue;
        }
    }
}

// ── App main ──────────────────────────────────────
void app_main(void) {
    ESP_LOGI(TAG, "ZYRA starting...");

    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES ||
        ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        nvs_flash_init();
    }

    display_init();
    status_led_init();
    display_update(DISP_BOOTING);

    xTaskCreatePinnedToCore(
        display_task, "Display",
        4096, NULL, 1, NULL, 1
    );

    set_state(DISP_CONNECTING);

    if (!wifi_init()) {
        ESP_LOGE(TAG, "Home WiFi failed");

        audio_pipeline_init();
        // Initialize streaming playback queue.
        // Online audio should start playing while WebSocket bytes are still arriving.
        audio_streamer_init();

        if (wakeword_engine_init() != ESP_OK) {
            ESP_LOGE(TAG, "Wakeword engine failed");
            set_state(DISP_ERROR);
            return;
        }


        offline_speech_init();

        if (start_offline_mode("home WiFi unavailable during boot")) {
            ESP_LOGI(TAG, "ZYRA direct AP emergency relay mode active");
            return;
        }

        set_state(DISP_ERROR);
        return;
    }

    set_state(DISP_PROCESSING);

    audio_pipeline_init();
    // Initialize streaming playback queue.
    // Online audio should start playing while WebSocket bytes are still arriving.
    audio_streamer_init();

    if (wakeword_engine_init() != ESP_OK) {
        ESP_LOGE(TAG, "Wakeword engine failed");
        set_state(DISP_ERROR);
        return;
    }

    // Initialize smart-home backend once.
    smart_home_control_init();

    offline_speech_init();

    // Continue normal ZYRA startup.
    ws_set_disconnect_callback(websocket_lost_callback);

    xTaskCreatePinnedToCore(
        runtime_fallback_task,
        "RuntimeFallback",
        16384,
        NULL,
        6,
        &s_runtime_fallback_task_handle,
        0
    );

    // Checks if Home Assistant comes back while Zyra is in Serverless Relay mode.
    xTaskCreate(
        home_assistant_restore_task,
        "HaRestore",
        8192,
        NULL,
        4,
        NULL
    );


    xTaskCreatePinnedToCore(
        server_health_task,
        "ServerHealth",
        4096,
        NULL,
        3,
        NULL,
        0
    );

    xTaskCreatePinnedToCore(
        server_reconnect_task,
        "ServerRetry",
        4096,
        NULL,
        4,
        &s_server_reconnect_task_handle,
        0
    );

    ESP_LOGI(TAG, "Connecting to server...");
    esp_err_t ws_err = ws_client_init(
        SERVER_IP, SERVER_PORT);

    if (ws_err != ESP_OK) {
        ESP_LOGE(TAG, "Server connection failed");

        xTaskCreate(
            boot_serverless_fallback_task,
            "BootFallback",
            16384,
            "server unavailable during boot",
            5,
            NULL
        );

        return;
    }

    // Server connected successfully.
    // Current mode colour = blue.
    status_led_set_mode(STATUS_LED_MODE_ONLINE);

    xTaskCreatePinnedToCore(
        zyra_task,
        "ZYRA",
        16384,
        NULL,
        5,
        &s_zyra_task_handle,
        0
    );

    set_state(DISP_IDLE);
    ESP_LOGI(TAG, "ZYRA online");
}
