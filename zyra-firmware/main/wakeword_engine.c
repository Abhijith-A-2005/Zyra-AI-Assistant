#include "wakeword_engine.h"
#include "audio_pipeline.h"

#include "esp_log.h"
#include "esp_wn_iface.h"
#include "esp_wn_models.h"
#include "model_path.h"

#include <stdlib.h>
#include <string.h>
#include <math.h>

static const char* TAG = "WAKEWORD";

static srmodel_list_t* s_models = NULL;
static const esp_wn_iface_t* s_wn = NULL;
static model_iface_data_t* s_wn_data = NULL;
static int s_wn_chunksize = 0;

#define WAKEWORD_GAIN 3
#define WAKEWORD_DEBUG_INTERVAL 80

esp_err_t wakeword_engine_init(void) {
    if (s_wn && s_wn_data && s_wn_chunksize > 0) {
        return ESP_OK;
    }

    ESP_LOGI(TAG, "Loading WakeNet model from model partition");

    s_models = esp_srmodel_init("model");
    if (!s_models) {
        ESP_LOGE(TAG, "esp_srmodel_init failed. Check model partition.");
        return ESP_FAIL;
    }

    char* wn_name = esp_srmodel_filter(
        s_models,
        ESP_WN_PREFIX,
        NULL
    );

    if (!wn_name) {
        ESP_LOGE(TAG, "No WakeNet model found. Check sdkconfig wakeword selection.");
        return ESP_FAIL;
    }

    ESP_LOGI(TAG, "Selected WakeNet model: %s", wn_name);

    s_wn = esp_wn_handle_from_name(wn_name);
    if (!s_wn) {
        ESP_LOGE(TAG, "esp_wn_handle_from_name failed");
        return ESP_FAIL;
    }

    s_wn_data = s_wn->create(wn_name, DET_MODE_90);
    if (!s_wn_data) {
        ESP_LOGE(TAG, "WakeNet create failed");
        return ESP_FAIL;
    }

    s_wn_chunksize = s_wn->get_samp_chunksize(s_wn_data);

    ESP_LOGI(TAG, "WakeNet ready. Chunk size: %d", s_wn_chunksize);
    return ESP_OK;
}

bool wakeword_wait_blocking_abortable(
    wakeword_abort_fn_t should_abort
) {
    if (!s_wn || !s_wn_data || s_wn_chunksize <= 0) {
        ESP_LOGE(TAG, "WakeNet not initialized");
        return false;
    }

    int16_t* frame = calloc(s_wn_chunksize, sizeof(int16_t));
    if (!frame) {
        ESP_LOGE(TAG, "WakeNet frame allocation failed");
        return false;
    }

    ESP_LOGI(TAG, "Waiting for wake word...");

    int debug_counter = 0;

    while (true) {
        if (should_abort && should_abort()) {
            ESP_LOGI(TAG, "WakeNet wait aborted");
            free(frame);
            return false;
        }

        int count = audio_read_wakenet_frame(frame, s_wn_chunksize);

        if (should_abort && should_abort()) {
            ESP_LOGI(TAG, "WakeNet wait aborted after read");
            free(frame);
            return false;
        }

        if (count != s_wn_chunksize) {
            vTaskDelay(pdMS_TO_TICKS(10));
            continue;
        }

        int peak = 0;
        int64_t sum_sq = 0;

        for (int i = 0; i < s_wn_chunksize; i++) {
            int32_t boosted = (int32_t)frame[i] * 4;

            if (boosted > 32767) boosted = 32767;
            if (boosted < -32768) boosted = -32768;

            frame[i] = (int16_t)boosted;

            int abs_value = frame[i] >= 0 ? frame[i] : -frame[i];
            if (abs_value > peak) peak = abs_value;

            sum_sq += (int32_t)frame[i] * frame[i];
        }

        int rms = (int)sqrtf((float)sum_sq / s_wn_chunksize);

        if (++debug_counter >= 30) {
            ESP_LOGI(TAG, "WakeNet audio peak=%d rms=%d", peak, rms);
            debug_counter = 0;
        }

        int detected = s_wn->detect(s_wn_data, frame);

        if (detected > 0) {
            ESP_LOGI(TAG, "Wake word detected: %d", detected);
            free(frame);
            return true;
        }
    }
}

bool wakeword_wait_blocking(void) {
    return wakeword_wait_blocking_abortable(NULL);
}