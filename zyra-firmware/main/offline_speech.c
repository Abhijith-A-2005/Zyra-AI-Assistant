#include "offline_speech.h"
#include "audio_pipeline.h"

#include "esp_log.h"
#include "esp_spiffs.h"
#include "esp_heap_caps.h"

#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include <stdlib.h>

static const char* TAG = "OFFLINE_SPEECH";

#define OFFLINE_SPEECH_BASE_PATH "/spiffs"

typedef struct {
    uint32_t sample_rate;
    uint16_t channels;
    uint16_t bits_per_sample;
    uint32_t data_size;
    long data_offset;
} WavInfo;

static bool s_spiffs_ready = false;

static uint16_t read_u16_le(const uint8_t* p) {
    return (uint16_t)p[0] | ((uint16_t)p[1] << 8);
}

static uint32_t read_u32_le(const uint8_t* p) {
    return (uint32_t)p[0] |
           ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) |
           ((uint32_t)p[3] << 24);
}

esp_err_t offline_speech_init(void) {
    if (s_spiffs_ready) {
        return ESP_OK;
    }

    esp_vfs_spiffs_conf_t conf = {
        .base_path = OFFLINE_SPEECH_BASE_PATH,
        .partition_label = "storage",
        .max_files = 5,
        .format_if_mount_failed = false
    };

    esp_err_t err = esp_vfs_spiffs_register(&conf);

    if (err != ESP_OK) {
        ESP_LOGE(TAG, "SPIFFS mount failed: %s", esp_err_to_name(err));
        return err;
    }

    size_t total = 0;
    size_t used = 0;

    err = esp_spiffs_info("storage", &total, &used);

    if (err != ESP_OK) {
        ESP_LOGE(TAG, "SPIFFS info failed: %s", esp_err_to_name(err));
        return err;
    }

    ESP_LOGI(TAG, "SPIFFS mounted. Total=%d Used=%d", total, used);

    s_spiffs_ready = true;
    return ESP_OK;
}

static esp_err_t parse_wav(FILE* f, WavInfo* info) {
    uint8_t header[12];

    if (fread(header, 1, sizeof(header), f) != sizeof(header)) {
        return ESP_FAIL;
    }

    if (memcmp(header, "RIFF", 4) != 0 ||
        memcmp(header + 8, "WAVE", 4) != 0) {
        ESP_LOGE(TAG, "Invalid WAV header");
        return ESP_FAIL;
    }

    bool found_fmt = false;
    bool found_data = false;

    memset(info, 0, sizeof(WavInfo));

    while (!found_data) {
        uint8_t chunk_header[8];

        if (fread(chunk_header, 1, sizeof(chunk_header), f) != sizeof(chunk_header)) {
            break;
        }

        uint32_t chunk_size = read_u32_le(chunk_header + 4);

        if (memcmp(chunk_header, "fmt ", 4) == 0) {
            uint8_t fmt[32];

            if (chunk_size > sizeof(fmt)) {
                ESP_LOGE(TAG, "Unsupported large fmt chunk");
                return ESP_FAIL;
            }

            if (fread(fmt, 1, chunk_size, f) != chunk_size) {
                return ESP_FAIL;
            }

            uint16_t audio_format = read_u16_le(fmt + 0);
            info->channels = read_u16_le(fmt + 2);
            info->sample_rate = read_u32_le(fmt + 4);
            info->bits_per_sample = read_u16_le(fmt + 14);

            if (audio_format != 1) {
                ESP_LOGE(TAG, "Only PCM WAV supported");
                return ESP_FAIL;
            }

            if (info->channels != 1 || info->bits_per_sample != 16) {
                ESP_LOGE(TAG, "WAV must be mono 16-bit PCM");
                return ESP_FAIL;
            }

            found_fmt = true;
        } else if (memcmp(chunk_header, "data", 4) == 0) {
            info->data_size = chunk_size;
            info->data_offset = ftell(f);
            found_data = true;
            break;
        } else {
            fseek(f, chunk_size, SEEK_CUR);
        }

        if (chunk_size & 1) {
            fseek(f, 1, SEEK_CUR);
        }
    }

    if (!found_fmt || !found_data) {
        ESP_LOGE(TAG, "Missing fmt/data chunk");
        return ESP_FAIL;
    }

    ESP_LOGI(
        TAG,
        "WAV: %lu Hz, %d ch, %d bit, %lu bytes",
        info->sample_rate,
        info->channels,
        info->bits_per_sample,
        info->data_size
    );

    return ESP_OK;
}

esp_err_t offline_speech_play(const char* filename) {
    if (!filename || filename[0] == '\0') {
        return ESP_ERR_INVALID_ARG;
    }

    if (!s_spiffs_ready) {
        esp_err_t err = offline_speech_init();
        if (err != ESP_OK) {
            return err;
        }
    }

    char path[96];
    snprintf(path, sizeof(path), OFFLINE_SPEECH_BASE_PATH "/%s", filename);

    FILE* f = fopen(path, "rb");

    if (!f) {
        ESP_LOGW(TAG, "Prompt missing: %s", path);
        return ESP_FAIL;
    }

    WavInfo info;

    esp_err_t err = parse_wav(f, &info);

    if (err != ESP_OK) {
        fclose(f);
        return err;
    }

    if (info.data_size == 0 || info.data_size > 300000) {
        ESP_LOGE(TAG, "Invalid WAV data size: %lu", info.data_size);
        fclose(f);
        return ESP_FAIL;
    }

    uint8_t* pcm = heap_caps_malloc(
        info.data_size,
        MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT
    );

    if (!pcm) {
        pcm = heap_caps_malloc(info.data_size, MALLOC_CAP_8BIT);
    }

    if (!pcm) {
        ESP_LOGE(TAG, "Failed to allocate WAV buffer");
        fclose(f);
        return ESP_ERR_NO_MEM;
    }

    fseek(f, info.data_offset, SEEK_SET);

    size_t read = fread(pcm, 1, info.data_size, f);
    fclose(f);

    if (read != info.data_size) {
        ESP_LOGE(TAG, "WAV read failed");
        free(pcm);
        return ESP_FAIL;
    }

    ESP_LOGI(TAG, "Playing offline prompt: %s", filename);

    err = audio_play_response(
        pcm,
        info.data_size,
        info.sample_rate
    );

    free(pcm);

    return err;
}