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

// ESP-SR

// ESP-SR removed for stable VAD-only mode.
// Wake word will be added later only after the base assistant is stable.

// Our modules
#include "audio_pipeline.h"
#include "websocket_client.h"
#include "display.h"
#include "offline_relay.h"

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

static volatile bool s_switching_wifi = false;
static volatile bool s_offline_mode   = false;
static volatile bool s_force_runtime_fallback = false;
static TaskHandle_t s_runtime_fallback_task_handle = NULL;
static TaskHandle_t s_offline_relay_task_handle = NULL;
static TaskHandle_t s_zyra_task_handle = NULL; 

static void offline_relay_task(void* arg);

// ── System state ──────────────────────────────────
static volatile DisplayState g_state = DISP_BOOTING;

static void set_state(DisplayState state) {
    g_state = state;
    display_set_state(state);
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

static void wait_for_quiet(int quiet_frames_required,
                           int32_t quiet_threshold) {
    int16_t frame[256];
    int quiet_frames = 0;

    while (quiet_frames < quiet_frames_required) {
        int count = audio_read_wakenet_frame(frame, 256);

        if (count <= 0) {
            vTaskDelay(pdMS_TO_TICKS(10));
            continue;
        }

        int32_t rms = calculate_rms(frame, count);

        if (rms < quiet_threshold) {
            quiet_frames++;
        } else {
            quiet_frames = 0;
        }

        vTaskDelay(pdMS_TO_TICKS(5));
    }
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

        // If offline mode is already active and the relay AP drops,
        // try reconnecting to the relay AP.
        if (s_offline_mode) {
            ESP_LOGW(TAG, "Offline AP disconnected, reconnecting");
            esp_wifi_connect();
            return;
        }

        // Normal online mode reconnect.
        ESP_LOGW(TAG, "WiFi disconnected, retrying home WiFi");
        esp_wifi_connect();

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
        ESP_LOGE(TAG, "Failed to set offline static IP: %s",
                 esp_err_to_name(err));
        return;
    }

    ESP_LOGI(TAG, "Offline static IP configured: 192.168.4.2");
}

static bool wifi_connect_offline_relay_ap(void) {
    ESP_LOGW(TAG, "Switching to offline relay AP");

    set_state(DISP_OFFLINE);

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

            ESP_LOGI(TAG, "Connected to offline relay AP");
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
    set_state(DISP_RELAY_FAIL);
    return false;
}

static void request_runtime_offline_fallback(bool force) {
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

static void websocket_lost_callback(void) {
    request_runtime_offline_fallback(false);
}

static bool start_offline_mode(const char* reason) {
    if (s_offline_mode && !s_switching_wifi) {
        ESP_LOGW(TAG, "Offline mode already active");
        return true;
    }

    ESP_LOGW(TAG, "Entering offline relay mode: %s", reason);

    s_offline_mode = true;
    set_state(DISP_OFFLINE);

    ws_client_stop();

    if (!wifi_connect_offline_relay_ap()) {
        ESP_LOGE(TAG, "Failed to enter offline relay mode");
        set_state(DISP_RELAY_FAIL);
        return false;
    }

    if (s_offline_relay_task_handle == NULL) {
        xTaskCreatePinnedToCore(
            offline_relay_task,
            "OfflineRelay",
            4096,
            NULL,
            4,
            &s_offline_relay_task_handle,
            0
        );
    }

    set_state(DISP_OFFLINE);

    ESP_LOGI(TAG, "ZYRA switched to offline relay mode");
    return true;
}

static void runtime_fallback_task(void* arg) {
    while (true) {
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);

        if (s_offline_mode || s_switching_wifi) {
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

        start_offline_mode(offline_reason);
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
    #define VAD_SPEECH_THRESHOLD       1200  // must exceed to count as speech
    #define VAD_SILENCE_THRESHOLD      1200  // below this = silence
    #define VAD_TRIGGER_FRAMES            7  // ~160ms continuous speech to trigger
    #define VAD_SPEECH_FRAMES_MIN        12  // ~400ms real speech required
    #define VAD_SILENCE_FRAMES_END       85  // ~1.36s silence needed before ending capture
    #define VAD_MAX_CAPTURE_MS         6000  // keep 6s for longer questions
    #define VAD_MIN_CAPTURE_BYTES      8000  // 0.5 sec at 16kHz 16-bit
    #define VAD_QUIET_FRAMES_START       25  // wait for quiet before listening
    #define VAD_POST_SPEAK_COOLDOWN_MS  900  // prevent self-trigger after speaking

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

        if (s_offline_mode) {
            vTaskDelay(pdMS_TO_TICKS(1000));
            continue;
        }

        if (!ws_is_connected()) {
            request_runtime_offline_fallback(false);
            vTaskDelay(pdMS_TO_TICKS(500));
            continue;
        }

        // ── PHASE 1: Wait for quiet, then wait for speech ─
        set_state(DISP_IDLE);

        // Make sure the mic is quiet before accepting a new command.
        // This prevents speaker echo/noise from triggering THINKING.
        wait_for_quiet(VAD_QUIET_FRAMES_START, VAD_SILENCE_THRESHOLD);

int pre_speech_frames = 0;

        while (true) {
            int count = audio_read_wakenet_frame(frame, 256);
            if (count <= 0) {
                vTaskDelay(pdMS_TO_TICKS(5));
                continue;
            }

            // RMS energy — more stable than peak for VAD
            int32_t rms = calculate_rms(frame, count);

            if (rms > VAD_SPEECH_THRESHOLD) {
                pre_speech_frames++;
                if (pre_speech_frames >= VAD_TRIGGER_FRAMES) {
                    ESP_LOGI(TAG, "Speech detected (RMS=%" PRId32 ")", rms);
                    break;
                }
            } else {
                pre_speech_frames = 0;
            }
        }

        // ── PHASE 2: Capture utterance ─────────────
        set_state(DISP_LISTENING);

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
        ESP_LOGI(TAG, "Sending %zu bytes (%dms of audio) to server",
                 captured,
                 (int)((captured / 2) * 1000 / CAPTURE_SAMPLE_RATE));

        esp_err_t err = ws_send_audio(capture_buf, captured);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "Send failed — server may be offline");
            request_runtime_offline_fallback(true);
            set_state(DISP_OFFLINE);
            continue;
        }

        // ── PHASE 4: Wait for response ─────────────
        int timeout = 0;
        while (!ws_response_ready() && timeout < 300) {
            if (!ws_is_connected()) {
                ESP_LOGE(TAG, "Server disconnected while waiting for response");
                request_runtime_offline_fallback(true);
                break;
            }

            vTaskDelay(pdMS_TO_TICKS(100));
            timeout++;
        }

        if (!ws_response_ready()) {
            ESP_LOGW(TAG, "Response timeout");

            if (!ws_is_connected()) {
                set_state(DISP_OFFLINE);
            } else {
                set_state(DISP_IDLE);
            }

            continue;
        }
        // ── PHASE 5: Play response ─────────────────
        set_state(DISP_SPEAKING);
        uint8_t* audio_data = NULL;
        int      sr         = 22050;
        size_t   audio_len  = ws_get_response(&audio_data, &sr);

        if (audio_data && audio_len > 0) {
            ESP_LOGI(TAG, "Playing %zu bytes at %dHz", audio_len, sr);
            audio_play_response(audio_data, audio_len, sr);

            // Give speaker output time to settle before listening again.
            vTaskDelay(pdMS_TO_TICKS(VAD_POST_SPEAK_COOLDOWN_MS));

            // Clear leftover mic/I2S frames so Zyra does not hear itself.
            drain_mic_frames(8);
        } else {
            ESP_LOGI(TAG, "No audio response — returning to idle");
        }

        ws_free_response();
        set_state(DISP_IDLE);

    }

    free(capture_buf);
    vTaskDelete(NULL);
}   

static void offline_relay_task(void* param) {
    ESP_LOGI(TAG, "Offline relay task started");

    set_state(DISP_OFFLINE);

    offline_relay_init();

    if (offline_relay_fetch_status()) {
        ESP_LOGI(TAG, "Offline relay status synced");
        set_state(DISP_RELAY_OK);
        vTaskDelay(pdMS_TO_TICKS(1200));
        set_state(DISP_OFFLINE);
    } else {
        ESP_LOGE(TAG, "Offline relay status sync failed");
        set_state(DISP_RELAY_FAIL);
    }

    while (true) {
        vTaskDelay(pdMS_TO_TICKS(10000));

        if (offline_relay_fetch_status()) {
            ESP_LOGI(TAG, "Offline relay live");
        } else {
            ESP_LOGW(TAG, "Offline relay status failed");
            set_state(DISP_RELAY_FAIL);
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
    display_update(DISP_BOOTING);

    xTaskCreatePinnedToCore(
        display_task, "Display",
        4096, NULL, 1, NULL, 1
    );

    set_state(DISP_CONNECTING);

    if (!wifi_init()) {
        ESP_LOGE(TAG, "Home WiFi failed");

        if (wifi_connect_offline_relay_ap()) {
            audio_pipeline_init();

            xTaskCreatePinnedToCore(
                offline_relay_task, "OfflineRelay",
                8192, NULL, 5, NULL, 0
            );

            ESP_LOGI(TAG, "ZYRA offline relay mode active");
            return;
        }

        set_state(DISP_ERROR);
        return;
    }

    set_state(DISP_PROCESSING);

    audio_pipeline_init();

    ws_set_disconnect_callback(websocket_lost_callback);

    xTaskCreatePinnedToCore(
        runtime_fallback_task,
        "RuntimeFallback",
        4096,
        NULL,
        6,
        &s_runtime_fallback_task_handle,
        0
    );

    ESP_LOGI(TAG, "Connecting to server...");
    esp_err_t ws_err = ws_client_init(
        SERVER_IP, SERVER_PORT);

    if (ws_err != ESP_OK) {
        ESP_LOGE(TAG, "Server connection failed");

        start_offline_mode("server unavailable during boot");
        return;
    }

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