#include "websocket_client.h"
#include "esp_websocket_client.h"
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
static bool     response_final  = true;
static bool     audio_incoming  = false;
static size_t   audio_expected  = 0;

static SemaphoreHandle_t response_mutex;

void ws_set_disconnect_callback(ws_disconnect_callback_t callback) {
    disconnect_callback = callback;
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

                    // Normal audio response path
                    else if (ab && cJSON_IsNumber(ab)) {
                        audio_expected = ab->valueint;
                        audio_incoming = true;

                        xSemaphoreTake(response_mutex, portMAX_DELAY);

                        if (response_buffer) {
                            free(response_buffer);
                            response_buffer = NULL;
                        }

                        response_len   = 0;
                        response_ready = false;

                        if (sr && cJSON_IsNumber(sr)) {
                            response_sr = sr->valueint;
                        }

                        if (final) {
                            response_final = cJSON_IsTrue(final);
                            } else {
                                // Old non-streaming protocol is treated as one final chunk.
                                response_final = true;
                        }

                        xSemaphoreGive(response_mutex);

                        ESP_LOGI(TAG,
                            "Expecting %d bytes audio at %d Hz",
                            audio_expected,
                            response_sr);
                    }

                    cJSON_Delete(json);
                }
                free(msg);

            } else if (data->op_code == 0x02) {
                // Binary frame — audio response
                // Accumulate chunks until complete
                if (audio_incoming &&
                    data->data_len > 0) {

                    xSemaphoreTake(response_mutex,
                                   portMAX_DELAY);

                    // First chunk — allocate full buffer
                    if (!response_buffer &&
                        audio_expected > 0) {
                        response_buffer = heap_caps_malloc(
                                            audio_expected,
                                            MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);

                        if (!response_buffer) {
                            ESP_LOGW(TAG, "PSRAM response alloc failed, trying internal RAM");
                            response_buffer = heap_caps_malloc(
                                                audio_expected,
                                                MALLOC_CAP_8BIT);
                        }

                        response_len = 0;

                        if (!response_buffer) {
                            ESP_LOGE(TAG,
                                "Failed to alloc "
                                "%d bytes",
                                audio_expected);
                            audio_incoming = false;
                            xSemaphoreGive(response_mutex);
                            break;
                        }
                    }

                    // Copy chunk into buffer
                    if (response_buffer &&
                        response_len + data->data_len
                        <= audio_expected) {
                        memcpy(response_buffer
                                 + response_len,
                               data->data_ptr,
                               data->data_len);
                        response_len += data->data_len;

                        ESP_LOGI(TAG,
                            "Audio chunk: %d/%d bytes",
                            response_len,
                            audio_expected);

                        // Mark ready when all received
                        if (response_len >=
                            audio_expected) {
                            response_ready = true;
                            audio_incoming = false;
                            ESP_LOGI(TAG,
                                "Audio complete: "
                                "%d bytes",
                                response_len);
                        }
                    }

                    xSemaphoreGive(response_mutex);
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
        .ping_interval_sec    = 15,
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
    audio_incoming  = false;
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