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
#include "nvs_flash.h"
#include "esp_heap_caps.h"

// ESP-SR

// ESP-SR removed for stable VAD-only mode.
// Wake word will be added later only after the base assistant is stable.

// Our modules
#include "audio_pipeline.h"
#include "websocket_client.h"
#include "display.h"

#define CAPTURE_SAMPLE_RATE 16000

static const char* TAG = "ZYRA";

// ── Configuration ─────────────────────────────────
#include "zyra_config.h"

// ── WiFi event group ──────────────────────────────
static EventGroupHandle_t wifi_events;
#define WIFI_CONNECTED_BIT BIT0
#define WIFI_FAIL_BIT      BIT1

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
        esp_wifi_connect();
    } else if (base == WIFI_EVENT &&
               id == WIFI_EVENT_STA_DISCONNECTED) {
        ESP_LOGW(TAG, "WiFi disconnected, retrying");
        xEventGroupClearBits(wifi_events, WIFI_CONNECTED_BIT);
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
    esp_netif_create_default_wifi_sta();

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
            ESP_LOGE(TAG, "Send failed");
            set_state(DISP_IDLE);
            continue;
        }

        // ── PHASE 4: Wait for response ─────────────
        int timeout = 0;
        while (!ws_response_ready() && timeout < 300) {
            vTaskDelay(pdMS_TO_TICKS(100));
            timeout++;
        }
        if (!ws_response_ready()) {
            ESP_LOGW(TAG, "Response timeout");
            set_state(DISP_IDLE);
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
        ESP_LOGE(TAG, "Stopping boot because WiFi failed");
        set_state(DISP_ERROR);
        return;
    }

    set_state(DISP_PROCESSING);

    audio_pipeline_init();

    ESP_LOGI(TAG, "Connecting to server...");
    esp_err_t ws_err = ws_client_init(
        SERVER_IP, SERVER_PORT);

    if (ws_err != ESP_OK) {
        ESP_LOGE(TAG, "Server connection failed");
        set_state(DISP_ERROR);
        return;
    }

    xTaskCreatePinnedToCore(
        zyra_task, "ZYRA",
        16384, NULL, 5, NULL, 0
    );

    set_state(DISP_IDLE);
    ESP_LOGI(TAG, "ZYRA online");
}