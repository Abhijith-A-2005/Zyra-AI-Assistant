#include "audio_streamer.h"
#include "audio_pipeline.h"

#include "esp_log.h"
#include "esp_heap_caps.h"

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"

#include <string.h>
#include <stdlib.h>

static const char* TAG = "AUDIO_STREAMER";

#define AUDIO_STREAM_QUEUE_LEN      64
#define AUDIO_STREAM_TASK_STACK     8192
#define AUDIO_STREAM_TASK_PRIO      6

typedef struct {
    uint8_t* data;
    size_t len;
    int sample_rate;
    bool end_marker;
} AudioStreamBlock;

static QueueHandle_t s_audio_queue = NULL;
static TaskHandle_t s_audio_task = NULL;

static volatile bool s_busy = false;
static volatile bool s_initialized = false;

static void audio_streamer_task(void* arg) {
    AudioStreamBlock block;

    ESP_LOGI(TAG, "Audio streamer task started");

    while (true) {
        if (xQueueReceive(
                s_audio_queue,
                &block,
                portMAX_DELAY
            ) != pdTRUE) {
            continue;
        }

        if (block.end_marker) {
            ESP_LOGI(TAG, "Audio stream end marker received");
            s_busy = false;
            continue;
        }

        if (!block.data || block.len == 0) {
            continue;
        }

        // Play this small PCM block immediately.
        // Why:
        // WebSocket continues receiving future blocks while this task feeds I2S.
        audio_play_response(
            block.data,
            block.len,
            block.sample_rate
        );

        free(block.data);
    }
}

esp_err_t audio_streamer_init(void) {
    if (s_initialized) {
        return ESP_OK;
    }

    s_audio_queue = xQueueCreate(
        AUDIO_STREAM_QUEUE_LEN,
        sizeof(AudioStreamBlock)
    );

    if (!s_audio_queue) {
        ESP_LOGE(TAG, "Failed to create audio stream queue");
        return ESP_ERR_NO_MEM;
    }

    BaseType_t ok = xTaskCreatePinnedToCore(
        audio_streamer_task,
        "AudioStream",
        AUDIO_STREAM_TASK_STACK,
        NULL,
        AUDIO_STREAM_TASK_PRIO,
        &s_audio_task,
        0
    );

    if (ok != pdPASS) {
        ESP_LOGE(TAG, "Failed to create audio streamer task");
        return ESP_FAIL;
    }

    s_busy = false;
    s_initialized = true;

    ESP_LOGI(TAG, "Audio streamer initialized");
    return ESP_OK;
}

esp_err_t audio_streamer_start(int sample_rate) {
    if (!s_initialized) {
        return ESP_ERR_INVALID_STATE;
    }

    // Start/continue stream mode.
    // Do not clear the queue here.
    // Why:
    // Multiple server TTS chunks should play continuously.
    s_busy = true;

    ESP_LOGI(TAG, "Audio stream started at %dHz", sample_rate);
    return ESP_OK;
}

bool audio_streamer_push(const uint8_t* data,
                         size_t len,
                         int sample_rate) {
    if (!s_initialized || !data || len == 0) {
        return false;
    }

    uint8_t* copy = heap_caps_malloc(
        len,
        MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT
    );

    if (!copy) {
        copy = heap_caps_malloc(
            len,
            MALLOC_CAP_8BIT
        );
    }

    if (!copy) {
        ESP_LOGE(TAG, "Failed to allocate audio stream block: %zu", len);
        return false;
    }

    memcpy(copy, data, len);

    AudioStreamBlock block = {
        .data = copy,
        .len = len,
        .sample_rate = sample_rate,
        .end_marker = false,
    };

    if (xQueueSend(
            s_audio_queue,
            &block,
            pdMS_TO_TICKS(500)
        ) != pdTRUE) {

        ESP_LOGE(TAG, "Audio stream queue full; dropping block");
        free(copy);
        return false;
    }

    return true;
}

void audio_streamer_end(void) {
    if (!s_initialized) {
        return;
    }

    AudioStreamBlock block = {
        .data = NULL,
        .len = 0,
        .sample_rate = 0,
        .end_marker = true,
    };

    xQueueSend(
        s_audio_queue,
        &block,
        pdMS_TO_TICKS(100)
    );
}

bool audio_streamer_is_busy(void) {
    return s_busy;
}

void audio_streamer_wait_idle(uint32_t timeout_ms) {
    uint32_t waited = 0;

    while (s_busy && waited < timeout_ms) {
        vTaskDelay(pdMS_TO_TICKS(20));
        waited += 20;
    }
}