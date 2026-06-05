#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <math.h>
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
#include "esp_wn_iface.h"
#include "esp_wn_models.h"
#include "model_path.h"

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

static void wifi_init(void) {
    wifi_events = xEventGroupCreate();
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

    xEventGroupWaitBits(wifi_events,
                        WIFI_CONNECTED_BIT,
                        pdFALSE, pdTRUE,
                        portMAX_DELAY);
    ESP_LOGI(TAG, "WiFi connected");
}

// ── Wake word + main pipeline task ────────────────
static void zyra_task(void* param) {
    ESP_LOGI(TAG, "ZYRA task started");
    g_state = DISP_IDLE;

    int retries = 0;
    while (!ws_is_connected() && retries < 30) {
        vTaskDelay(pdMS_TO_TICKS(1000));
        retries++;
    }
    if (!ws_is_connected()) {
        ESP_LOGE(TAG, "Server not connected");
        g_state = DISP_ERROR;
        vTaskDelete(NULL);
        return;
    }

    ESP_LOGI(TAG, "ZYRA ready — listening for speech");

    // ── VAD settings ──────────────────────────────
    // Frame = 256 samples @ 16kHz = 16ms per frame
    #define VAD_SPEECH_THRESHOLD   1000  // must exceed to count as speech
    #define VAD_SILENCE_THRESHOLD  1000  // below this = silence
    #define VAD_SPEECH_FRAMES_MIN    15  // ~240ms speech needed to trigger
    #define VAD_SILENCE_FRAMES_END   30  // ~480ms silence to end capture
    #define VAD_MAX_CAPTURE_MS     4000  // hard 4s cap — never send more

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

        // ── PHASE 1: Wait for speech ───────────────
        g_state = DISP_IDLE;
        int pre_speech_frames = 0;

        while (true) {
            int count = audio_read_wakenet_frame(frame, 256);
            if (count <= 0) {
                vTaskDelay(pdMS_TO_TICKS(5));
                continue;
            }

            // RMS energy — more stable than peak for VAD
            int64_t sum_sq = 0;
            for (int i = 0; i < count; i++)
                sum_sq += (int32_t)frame[i] * frame[i];
            int32_t rms = (int32_t)sqrtf((float)sum_sq / count);

            if (rms > VAD_SPEECH_THRESHOLD) {
                pre_speech_frames++;
                if (pre_speech_frames >= 3) {
                    // 3 consecutive loud frames = real speech
                    ESP_LOGI(TAG, "Speech detected (RMS=%" PRId32 ")", rms);
                    break;
                }
            } else {
                pre_speech_frames = 0;  // reset on any quiet frame
            }
        }

        // ── PHASE 2: Capture utterance ─────────────
        g_state = DISP_LISTENING;

        size_t captured    = 0;
        int silence_frames = 0;
        int speech_frames  = 0;

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
            int64_t sum_sq = 0;
            for (int i = 0; i < count; i++)
                sum_sq += (int32_t)frame[i] * frame[i];
            int32_t rms = (int32_t)sqrtf((float)sum_sq / count);

            if (rms > VAD_SPEECH_THRESHOLD) {
                speech_frames++;
                silence_frames = 0;
            } else if (rms < VAD_SILENCE_THRESHOLD) {
                silence_frames++;
                if (silence_frames >= VAD_SILENCE_FRAMES_END
                    && speech_frames >= VAD_SPEECH_FRAMES_MIN) {
                    ESP_LOGI(TAG,
                        "End of speech — %zu bytes, "
                        "%d speech frames, %d silence frames",
                        captured, speech_frames, silence_frames);
                    break;
                }
            }
            // No delay here — read as fast as I2S provides frames
        }

        // Reject if not enough real speech
        if (speech_frames < VAD_SPEECH_FRAMES_MIN) {
            ESP_LOGW(TAG, "Rejected — only %d speech frames", speech_frames);
            continue;
        }

        // ── PHASE 3: Send to server ────────────────
        g_state = DISP_PROCESSING;
        ESP_LOGI(TAG, "Sending %zu bytes (%dms of audio) to server",
                 captured,
                 (int)((captured / 2) * 1000 / CAPTURE_SAMPLE_RATE));

        esp_err_t err = ws_send_audio(capture_buf, captured);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "Send failed");
            g_state = DISP_IDLE;
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
            g_state = DISP_IDLE;
            continue;
        }

        // ── PHASE 5: Play response ─────────────────
        g_state = DISP_SPEAKING;
        uint8_t* audio_data = NULL;
        int      sr         = 22050;
        size_t   audio_len  = ws_get_response(&audio_data, &sr);

        if (audio_data && audio_len > 0) {
            ESP_LOGI(TAG, "Playing %zu bytes at %dHz", audio_len, sr);
            audio_play_response(audio_data, audio_len, sr);
        }

        ws_free_response();
        g_state = DISP_IDLE;
        vTaskDelay(pdMS_TO_TICKS(300));
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

    display_update(DISP_CONNECTING);
    wifi_init();

    audio_pipeline_init();

    ESP_LOGI(TAG, "Connecting to server...");
    esp_err_t ws_err = ws_client_init(
        SERVER_IP, SERVER_PORT);

    if (ws_err != ESP_OK) {
        ESP_LOGE(TAG, "Server connection failed");
        display_update(DISP_ERROR);
        return;
    }

    xTaskCreatePinnedToCore(
        zyra_task, "ZYRA",
        16384, NULL, 5, NULL, 0
    );

    ESP_LOGI(TAG, "ZYRA online");
}