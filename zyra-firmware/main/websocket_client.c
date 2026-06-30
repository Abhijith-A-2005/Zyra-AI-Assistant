#include "websocket_client.h"
#include "esp_websocket_client.h"
#include "audio_streamer.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "cJSON.h"
#include <string.h>
#include <stdlib.h>
#include "esp_heap_caps.h"

static const char* TAG = "WS";

// ── State ─────────────────────────────────────────
static esp_websocket_client_handle_t client = NULL;
static bool     connected       = false;
static bool     intentional_stop = false;
static bool     disconnect_notified = false;
static ws_disconnect_callback_t disconnect_callback = NULL;
static bool     response_ready  = false;
static uint8_t* response_buffer = NULL;
static size_t   response_len    = 0;
static int      response_sr     = 22050;
static bool     response_failed = false;
static bool     response_final  = true;
static bool     audio_incoming  = false;
static size_t   audio_expected  = 0;
static size_t   audio_received  = 0;
static bool audio_stream_active = false;

static SemaphoreHandle_t response_mutex;

void ws_set_disconnect_callback(ws_disconnect_callback_t callback) {
    disconnect_callback = callback;
}

bool ws_last_response_failed(void) {
    bool failed = false;

    if (response_mutex) {
        xSemaphoreTake(response_mutex, portMAX_DELAY);
        failed = response_failed;
        xSemaphoreGive(response_mutex);
    }

    return failed;
}

void ws_clear_response_result(void) {
    if (response_mutex) {
        xSemaphoreTake(response_mutex, portMAX_DELAY);
        response_failed = false;
        xSemaphoreGive(response_mutex);
    }
}

bool ws_audio_stream_active(void) {
    bool active;

    xSemaphoreTake(response_mutex, portMAX_DELAY);
    active = audio_stream_active;
    xSemaphoreGive(response_mutex);

    return active;
}

void ws_clear_audio_stream_active(void) {
    xSemaphoreTake(response_mutex, portMAX_DELAY);
    audio_stream_active = false;
    xSemaphoreGive(response_mutex);
}

static bool text_looks_like_success_fallback(const char* text) {
    if (!text) {
        return false;
    }
    
    return strstr(text, "used direct relay control") ||
           strstr(text, "using direct relay control") ||
           strstr(text, "used relay control") ||
           strstr(text, "controlled it directly") ||
           strstr(text, "direct relay control") ||
           strstr(text, "fallback succeeded");
}

static bool text_looks_like_command_failure(const char* text) {
    if (!text) {
        return false;
    }

    if (text_looks_like_success_fallback(text)) {
        return false;
    }

    // Only mark actual failure wording as failure.
    return strstr(text, "both unreachable") ||
           strstr(text, "both unavailable") ||
           strstr(text, "are both unreachable") ||
           strstr(text, "are both unavailable") ||
           strstr(text, "could not") ||
           strstr(text, "couldn't") ||
           strstr(text, "failed") ||
           strstr(text, "can't reach") ||
           strstr(text, "cannot reach") ||
           strstr(text, "unreachable") ||
           strstr(text, "not reachable") ||
           strstr(text, "not available") ||
           strstr(text, "unable to") ||
           strstr(text, "target entity is unavailable") ||
           strstr(text, "target HA entity may be unavailable");
}

// ── WebSocket event handler ────────────────────────
static void ws_event_handler(void* arg,
                              esp_event_base_t base,
                              int32_t event_id,
                              void* event_data) {
    esp_websocket_event_data_t* data =
        (esp_websocket_event_data_t*)event_data;

    switch (event_id) {

        case WEBSOCKET_EVENT_CONNECTED:
            ESP_LOGI(TAG, "Connected to ZYRA server");
            connected = true;
            disconnect_notified = false;
            break;

        case WEBSOCKET_EVENT_DISCONNECTED: {
            ESP_LOGW(TAG, "Disconnected from server");

            bool was_connected = connected;
            connected = false;

            if (was_connected &&
                !intentional_stop &&
                !disconnect_notified &&
                disconnect_callback) {

                disconnect_notified = true;
                disconnect_callback();
            }

            break;
        }

        case WEBSOCKET_EVENT_DATA:
            if (data->op_code == 0x01) {
                // Text frame — JSON status message
                char* msg = malloc(data->data_len + 1);
                if (!msg) break;
                memcpy(msg, data->data_ptr,
                       data->data_len);
                msg[data->data_len] = '\0';

                ESP_LOGI(TAG, "Server: %s", msg);

                // Parse JSON
                cJSON* json = cJSON_Parse(msg);
                if (json) {
                    cJSON* status = cJSON_GetObjectItem(json, "status");
                    cJSON* ab     = cJSON_GetObjectItem(json, "audio_bytes");
                    cJSON* sr     = cJSON_GetObjectItem(json, "sample_rate");
                    cJSON* final  = cJSON_GetObjectItem(json, "final");
                    cJSON* response_text   = cJSON_GetObjectItem(json, "response");
                    cJSON* audio_text    = cJSON_GetObjectItem(json, "text");
                    cJSON* command_success = cJSON_GetObjectItem(json, "command_success");
                    cJSON* command_result  = cJSON_GetObjectItem(json, "command_result");

                    // Detect online command failure from server metadata/text.
                    bool detected_failure = false;
                    bool has_explicit_command_result = false;
                    bool explicit_success = false;

                    if (command_success && cJSON_IsBool(command_success)) {
                        has_explicit_command_result = true;

                        if (cJSON_IsTrue(command_success)) {
                            detected_failure = false;
                            explicit_success = true;
                        } else {
                            detected_failure = true;
                        }
                    }

                    if (command_result && cJSON_IsString(command_result)) {
                        has_explicit_command_result = true;

                        if (strcmp(command_result->valuestring, "ok") == 0 ||
                            strcmp(command_result->valuestring, "success") == 0) {
                            detected_failure = false;
                            explicit_success = true;
                        } else if (
                            strcmp(command_result->valuestring, "failed") == 0 ||
                            strcmp(command_result->valuestring, "failure") == 0 ||
                            strcmp(command_result->valuestring, "error") == 0
                        ) {
                            detected_failure = true;
                        }
                    }

                    bool detected_success_fallback = false;

                    // Only use text heuristics when the server did not explicitly tell us
                    // whether the command succeeded.
                    if (!has_explicit_command_result) {
                        if (response_text && cJSON_IsString(response_text)) {
                            if (text_looks_like_success_fallback(response_text->valuestring)) {
                                detected_success_fallback = true;
                            }

                            if (text_looks_like_command_failure(response_text->valuestring)) {
                                detected_failure = true;
                            }
                        }

                        if (audio_text && cJSON_IsString(audio_text)) {
                            if (text_looks_like_success_fallback(audio_text->valuestring)) {
                                detected_success_fallback = true;
                            }

                            if (text_looks_like_command_failure(audio_text->valuestring)) {
                                detected_failure = true;
                            }
                        }

                        if (detected_success_fallback) {
                            detected_failure = false;
                        }
                    }

                    if (explicit_success) {
                        detected_failure = false;
                    }

                    if (detected_failure) {
                        xSemaphoreTake(response_mutex, portMAX_DELAY);
                        response_failed = true;
                        xSemaphoreGive(response_mutex);

                        ESP_LOGW(TAG, "Server response marked as command failure");
                    }

                    // Server says no valid speech was detected.
                    // Treat this as a completed response with zero audio,
                    // so the firmware returns to listening immediately.
                    if (status && cJSON_IsString(status) &&
                        strcmp(status->valuestring, "ready") == 0) {

                        xSemaphoreTake(response_mutex, portMAX_DELAY);

                        if (response_buffer) {
                            free(response_buffer);
                            response_buffer = NULL;
                        }

                        response_len    = 0;
                        audio_expected  = 0;
                        audio_incoming  = false;
                        response_ready  = true;

                        xSemaphoreGive(response_mutex);

                        ESP_LOGI(TAG, "Server ready/no speech — returning to idle");
                    }

                    // Server error should also unblock the firmware.
                    else if (status && cJSON_IsString(status) &&
                            strcmp(status->valuestring, "error") == 0) {

                        xSemaphoreTake(response_mutex, portMAX_DELAY);

                        if (response_buffer) {
                            free(response_buffer);
                            response_buffer = NULL;
                        }

                        response_len    = 0;
                        audio_expected  = 0;
                        audio_incoming  = false;
                        response_ready  = true;

                        xSemaphoreGive(response_mutex);

                        ESP_LOGW(TAG, "Server error — returning to idle");
                    }

                    // Streaming audio response path
                    else if (ab && cJSON_IsNumber(ab)) {
                        audio_expected = (size_t)ab->valueint;
                        audio_received = 0;
                        audio_incoming = true;

                        xSemaphoreTake(response_mutex, portMAX_DELAY);

                        // Do not allocate a full response_buffer for online streamed audio.
                        if (response_buffer) {
                            free(response_buffer);
                            response_buffer = NULL;
                        }

                        response_len   = 0;
                        response_ready = false;

                        if (detected_success_fallback) {
                            response_failed = false;
                        } else {
                            response_failed = response_failed || detected_failure;
                        }

                        if (sr && cJSON_IsNumber(sr)) {
                            response_sr = sr->valueint;
                        }

                        if (final && cJSON_IsBool(final)) {
                            response_final = cJSON_IsTrue(final);
                        } else {
                            response_final = true;
                        }

                        xSemaphoreGive(response_mutex);

                        // Start audio streaming mode before binary bytes arrive.
                        // Why:
                        // The first binary frame should start speaker playback immediately.
                        audio_streamer_start(response_sr);
                        xSemaphoreTake(response_mutex, portMAX_DELAY);
                        audio_stream_active = true;
                        xSemaphoreGive(response_mutex);

                        ESP_LOGI(
                            TAG,
                            "Streaming audio incoming: %zu bytes at %d Hz final=%d failed=%d",
                            audio_expected,
                            response_sr,
                            response_final ? 1 : 0,
                            response_failed ? 1 : 0
                        );
                    }

                    cJSON_Delete(json);
                }
                free(msg);

            } else if (data->op_code == 0x02) {
                // Binary frame — streamed audio response
                if (audio_incoming &&
                    data->data_len > 0 &&
                    data->data_ptr) {

                    // Push this binary frame directly into the audio playback queue.
                    // Why:
                    // This lets the speaker start while the remaining WebSocket bytes
                    // are still arriving.
                    bool pushed = audio_streamer_push(
                        (const uint8_t*)data->data_ptr,
                        (size_t)data->data_len,
                        response_sr
                    );

                    if (!pushed) {
                        ESP_LOGE(
                            TAG,
                            "Failed to push streamed audio frame: %d bytes",
                            data->data_len
                        );
                        break;
                    }

                    audio_received += (size_t)data->data_len;

                    if ((audio_received % (32 * 1024)) < (size_t)data->data_len ||
                        audio_received >= audio_expected) {

                        ESP_LOGI(
                            TAG,
                            "Streamed audio: received=%zu/%zu final=%d",
                            audio_received,
                            audio_expected,
                            response_final ? 1 : 0
                        );
}

                    if (audio_received >= audio_expected) {
                        audio_incoming = false;

                        // Tell server this TTS chunk is fully received/enqueued.
                        // Why:
                        // Server can now send the next TTS chunk while this one is
                        // still playing from the audio_streamer queue.
                        ws_send_status("audio_chunk_buffered");

                        if (response_final) {
                            // Final chunk has been fully enqueued.
                            // main.c will wait for audio_streamer to finish playback.
                            audio_streamer_end();

                            xSemaphoreTake(response_mutex, portMAX_DELAY);

                            response_len = 0;
                            response_ready = true;

                            xSemaphoreGive(response_mutex);

                            ESP_LOGI(TAG, "Final streamed audio chunk enqueued");
                        } else {
                            ESP_LOGI(TAG, "Non-final streamed audio chunk enqueued");
                        }
                    }
                }
            }
            break;

        case WEBSOCKET_EVENT_ERROR:
            ESP_LOGE(TAG, "WebSocket error");
            break;

        default:
            break;
    }
}

esp_err_t ws_client_init(const char* server_ip,
                          int port) {
    response_mutex = xSemaphoreCreateMutex();

    char uri[64];
    snprintf(uri, sizeof(uri),
             "ws://%s:%d/zyra", server_ip, port);

    esp_websocket_client_config_t cfg = {
        .uri                  = uri,
        .reconnect_timeout_ms = 3000,
        .network_timeout_ms   = 15000,
        .ping_interval_sec    = 0,
        .transport            = WEBSOCKET_TRANSPORT_OVER_TCP,
    };

    client = esp_websocket_client_init(&cfg);
    esp_websocket_register_events(
        client,
        WEBSOCKET_EVENT_ANY,
        ws_event_handler,
        NULL
    );

    esp_err_t err = esp_websocket_client_start(client);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to start WS client");
        return err;
    }

    // Wait for connection
    int retries = 0;
    while (!connected && retries < 20) {
        vTaskDelay(pdMS_TO_TICKS(500));
        retries++;
    }

    if (!connected) {
        ESP_LOGE(TAG, "Could not connect to server");
        ws_client_stop();
        return ESP_FAIL;
    }

    ESP_LOGI(TAG, "Connected: %s", uri);
    return ESP_OK;
}

void ws_client_stop(void) {
    intentional_stop = true;

    if (client) {
        ESP_LOGW(TAG, "Stopping WebSocket client");

        esp_websocket_client_stop(client);
        esp_websocket_client_destroy(client);
        client = NULL;
    }

    connected = false;
    response_ready = false;
    audio_incoming = false;
    audio_expected = 0;
    disconnect_notified = false;
    audio_stream_active = false;
    intentional_stop = false;
}

esp_err_t ws_send_audio(const uint8_t* data,
                         size_t len) {
    if (!connected) return ESP_FAIL;

    xSemaphoreTake(response_mutex, portMAX_DELAY);

    if (response_buffer) {
        free(response_buffer);
        response_buffer = NULL;
    }

    response_len    = 0;
    audio_expected  = 0;
    audio_received  = 0;
    audio_incoming  = false;
    audio_stream_active = false;
    response_ready  = false;
    response_final = true;

    xSemaphoreGive(response_mutex);

    int sent = esp_websocket_client_send_bin(
        client,
        (const char*)data,
        len,
        pdMS_TO_TICKS(15000)
    );

    if (sent != (int)len) {
        ESP_LOGE(TAG, "WebSocket audio send incomplete: sent=%d expected=%u",
                sent,
                (unsigned int)len);
        return ESP_FAIL;
    }

    return ESP_OK;
}

bool ws_response_ready(void) {
    return response_ready;
}

size_t ws_get_response(uint8_t** data,
                        int* sample_rate) {
    xSemaphoreTake(response_mutex, portMAX_DELAY);
    *data          = response_buffer;
    *sample_rate   = response_sr;
    size_t len     = response_len;
    response_ready = false;
    xSemaphoreGive(response_mutex);
    return len;
}

bool ws_response_final(void) {
    bool final = true;

    if (!response_mutex) {
        return true;
    }

    xSemaphoreTake(response_mutex, portMAX_DELAY);
    final = response_final;
    xSemaphoreGive(response_mutex);

    return final;
}

void ws_free_response(void) {
    xSemaphoreTake(response_mutex, portMAX_DELAY);
    if (response_buffer) {
        free(response_buffer);
        response_buffer = NULL;
        response_len    = 0;
    }
    xSemaphoreGive(response_mutex);
}

bool ws_is_connected(void) {
    if (!client) {
        return false;
    }

    if (!connected) {
        return false;
    }

    return esp_websocket_client_is_connected(client);
}

esp_err_t ws_send_status(const char* status) {
    if (!connected) return ESP_FAIL;

    cJSON* json = cJSON_CreateObject();
    cJSON_AddStringToObject(json, "type", "status");
    cJSON_AddStringToObject(json, "value", status);
    char* str = cJSON_PrintUnformatted(json);

    int sent = esp_websocket_client_send_text(
        client, str, strlen(str),
        pdMS_TO_TICKS(1000)
    );

    free(str);
    cJSON_Delete(json);
    return sent >= 0 ? ESP_OK : ESP_FAIL;
}