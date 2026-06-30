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
// Holds one leftover byte when a WebSocket fragment splits a 16-bit PCM sample.
static uint8_t s_pending_pcm_byte = 0;
static bool s_has_pending_pcm_byte = false;

static void audio_streamer_drop_queued_blocks(void) {
    if (!s_audio_queue) {
        return;
    }

    AudioStreamBlock old_block;
    int dropped = 0;

    while (xQueueReceive(s_audio_queue, &old_block, 0) == pdTRUE) {
        if (old_block.data) {
            free(old_block.data);
        }

        dropped++;
    }

    if (dropped > 0) {
        ESP_LOGW(TAG, "Dropped %d stale audio stream blocks", dropped);
    }
}

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

    // If no stream is currently active, this is a new response.
    if (!s_busy) {
        audio_streamer_drop_queued_blocks();
        s_has_pending_pcm_byte = false;
        s_pending_pcm_byte = 0;
    }

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

    // Raw PCM must stay aligned to 16-bit samples.
    //
    // Why:
    // WebSocket binary data can arrive in fragments. Even if the server sends
    // even-sized frames, the ESP event can still deliver odd-sized pieces.
    // If we write odd byte counts to I2S, the next block starts half a sample
    // late and the speaker produces harsh noise.
    size_t total_len = len + (s_has_pending_pcm_byte ? 1 : 0);
    size_t even_len = total_len & ~(size_t)1;

    if (even_len == 0) {
        s_pending_pcm_byte = data[0];
        s_has_pending_pcm_byte = true;
        return true;
    }

    uint8_t* copy = heap_caps_malloc(
        even_len,
        MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT
    );

    if (!copy) {
        copy = heap_caps_malloc(
            even_len,
            MALLOC_CAP_8BIT
        );
    }

    if (!copy) {
        ESP_LOGE(TAG, "Failed to allocate audio stream block: %zu", even_len);
        return false;
    }

    size_t out_pos = 0;
    size_t in_pos = 0;

    if (s_has_pending_pcm_byte) {
        copy[out_pos++] = s_pending_pcm_byte;
        s_has_pending_pcm_byte = false;
    }

    size_t bytes_needed = even_len - out_pos;

    if (bytes_needed > 0) {
        memcpy(copy + out_pos, data, bytes_needed);
        in_pos = bytes_needed;
    }

    // If one byte remains, keep it for the next WebSocket fragment.
    if (in_pos < len) {
        s_pending_pcm_byte = data[len - 1];
        s_has_pending_pcm_byte = true;
    }

    AudioStreamBlock block = {
        .data = copy,
        .len = even_len,
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

    // Drop an incomplete final byte if a stream ended mid-sample.
    // This should be rare, but it prevents the next response from starting
    // with a stale half-sample.
    if (s_has_pending_pcm_byte) {
        ESP_LOGW(TAG, "Dropping incomplete final PCM byte");
        s_has_pending_pcm_byte = false;
        s_pending_pcm_byte = 0;
    }

    AudioStreamBlock block = {
        .data = NULL,
        .len = 0,
        .sample_rate = 0,
        .end_marker = true,
    };

    if (xQueueSend(
            s_audio_queue,
            &block,
            pdMS_TO_TICKS(100)
        ) != pdTRUE) {
        ESP_LOGE(TAG, "Failed to enqueue audio stream end marker");
        s_busy = false;
    }
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