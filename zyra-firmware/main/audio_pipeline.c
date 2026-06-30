#include "audio_pipeline.h"
#include "driver/i2s_std.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include <string.h>
#include <stdlib.h>
#include <math.h>

static const char* TAG = "AUDIO";

static i2s_chan_handle_t tx_handle = NULL;
static i2s_chan_handle_t rx_handle = NULL;
static SemaphoreHandle_t tx_mutex = NULL;

#define I2S_MIC_SCK         1
#define I2S_MIC_WS          2
#define I2S_MIC_SD          3
#define I2S_AMP_BCLK        4
#define I2S_AMP_LRC         5
#define I2S_AMP_DOUT        21
#define CAPTURE_SAMPLE_RATE 16000
#define VAD_SILENCE_THRESHOLD 500
#define VAD_SILENCE_FRAMES    40

esp_err_t audio_pipeline_init(void) {

    // ── Strategy: Use port 0 for TX (amp) ────────
    // and port 1 for RX (mic) but initialize
    // port 1 FIRST before port 0 to avoid
    // the GDMA interrupt table null pointer bug

    // ── Step 1: Init MIC first (I2S port 1 RX) ──
    i2s_chan_config_t rx_cfg = {
        .id            = I2S_NUM_1,
        .role          = I2S_ROLE_MASTER,
        .dma_desc_num  = 4,
        .dma_frame_num = 256,
        .auto_clear    = true
    };

    esp_err_t err = i2s_new_channel(&rx_cfg,
                                     NULL,
                                     &rx_handle);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "rx new_channel: %s",
                 esp_err_to_name(err));
        rx_handle = NULL;
    } else {
        i2s_std_config_t rx_std = {
            .clk_cfg  = I2S_STD_CLK_DEFAULT_CONFIG(
                          CAPTURE_SAMPLE_RATE),
            .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(
                          I2S_DATA_BIT_WIDTH_32BIT,
                          I2S_SLOT_MODE_MONO),
            .gpio_cfg = {
                .mclk = I2S_GPIO_UNUSED,
                .bclk = (gpio_num_t)I2S_MIC_SCK,
                .ws   = (gpio_num_t)I2S_MIC_WS,
                .dout = I2S_GPIO_UNUSED,
                .din  = (gpio_num_t)I2S_MIC_SD,
                .invert_flags = {
                    .mclk_inv = false,
                    .bclk_inv = false,
                    .ws_inv   = false
                }
            }
        };
        rx_std.slot_cfg.slot_mask = I2S_STD_SLOT_LEFT;

        err = i2s_channel_init_std_mode(
                rx_handle, &rx_std);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "rx init_std: %s",
                     esp_err_to_name(err));
            i2s_del_channel(rx_handle);
            rx_handle = NULL;
        } else {
            err = i2s_channel_enable(rx_handle);
            if (err != ESP_OK) {
                ESP_LOGE(TAG, "rx enable: %s",
                         esp_err_to_name(err));
                i2s_del_channel(rx_handle);
                rx_handle = NULL;
            } else {
                // Flush cold start garbage
                int32_t dummy[256];
                size_t  bytes = 0;
                for (int i = 0; i < 5; i++) {
                    i2s_channel_read(
                        rx_handle, dummy,
                        sizeof(dummy), &bytes,
                        pdMS_TO_TICKS(100));
                }
                ESP_LOGI(TAG,
                    "Mic initialized on I2S port 1");
            }
        }
    }

    if (!rx_handle) {
        ESP_LOGW(TAG, "Mic init failed — disabled");
    }

    // ── Step 2: Init AMP second (I2S port 0 TX) ──
    vTaskDelay(pdMS_TO_TICKS(50));

    i2s_chan_config_t tx_cfg = {
        .id            = I2S_NUM_0,
        .role          = I2S_ROLE_MASTER,
        .dma_desc_num  = 6,
        .dma_frame_num = 512,
        .auto_clear    = true
    };

    err = i2s_new_channel(&tx_cfg,
                           &tx_handle,
                           NULL);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "tx new_channel: %s",
                 esp_err_to_name(err));
        return err;
    }

    i2s_std_config_t tx_std = {
        .clk_cfg  = I2S_STD_CLK_DEFAULT_CONFIG(22050),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(
                      I2S_DATA_BIT_WIDTH_16BIT,
                      I2S_SLOT_MODE_MONO),
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = I2S_AMP_BCLK,
            .ws   = I2S_AMP_LRC,
            .dout = I2S_AMP_DOUT,
            .din  = I2S_GPIO_UNUSED,
            .invert_flags = {
                .mclk_inv = false,
                .bclk_inv = false,
                .ws_inv   = false
            }
        }
    };

    err = i2s_channel_init_std_mode(
            tx_handle, &tx_std);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "tx init_std: %s",
                 esp_err_to_name(err));
        return err;
    }

    err = i2s_channel_enable(tx_handle);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "tx enable: %s",
                 esp_err_to_name(err));
        return err;
    }

    if (!tx_mutex) {
        tx_mutex = xSemaphoreCreateMutex();

        if (!tx_mutex) {
            ESP_LOGE(TAG, "Failed to create TX audio mutex");
            return ESP_ERR_NO_MEM;
        }
    }

    ESP_LOGI(TAG, "Amp initialized on I2S port 0");
    return ESP_OK;
}

int audio_read_wakenet_frame(int16_t* buffer,
                              int frame_len) {
    if (!buffer || frame_len <= 0) {
        return 0;
    }

    if (!rx_handle) {
        memset(buffer, 0, frame_len * sizeof(int16_t));
        return frame_len;
    }

    int32_t raw[frame_len];
    size_t bytes = 0;

    esp_err_t err = i2s_channel_read(
        rx_handle,
        raw,
        frame_len * sizeof(int32_t),
        &bytes,
        pdMS_TO_TICKS(300)
    );

    if (err != ESP_OK || bytes == 0) {
        return 0;
    }

    int count = bytes / sizeof(int32_t);

    for (int i = 0; i < count; i++) {
        buffer[i] = (int16_t)(raw[i] >> 16);
    }

    return count;
}

size_t audio_capture_utterance(uint8_t* buffer,
                                size_t max_bytes) {
    if (!rx_handle) return 0;

    size_t   captured    = 0;
    int      silence_cnt = 0;
    bool     speech_on   = false;
    int32_t  raw[256];
    size_t   bytes       = 0;

    ESP_LOGI(TAG, "Capturing utterance...");

    while (captured < max_bytes) {
        i2s_channel_read(rx_handle, raw,
                         sizeof(raw), &bytes,
                         pdMS_TO_TICKS(50));

        int count = bytes / sizeof(int32_t);
        int32_t peak = 0;
        for (int i = 0; i < count; i++) {
            int16_t s = (int16_t)(raw[i] >> 16);
            int32_t a = s < 0 ? -s : s;
            if (a > peak) peak = a;
        }
        for (int i = 0;
             i < count &&
             captured + 2 <= max_bytes;
             i++) {
            int16_t s = (int16_t)(raw[i] >> 16);
            buffer[captured++] = s & 0xFF;
            buffer[captured++] = (s >> 8) & 0xFF;
        }
        if (peak > VAD_SILENCE_THRESHOLD) {
            speech_on   = true;
            silence_cnt = 0;
        } else if (speech_on) {
            if (++silence_cnt >= VAD_SILENCE_FRAMES) {
                ESP_LOGI(TAG, "End of speech");
                break;
            }
        }
    }

    ESP_LOGI(TAG, "Captured %d bytes", captured);
    return captured;
}

esp_err_t audio_play_response(const uint8_t* data,
                               size_t len,
                               int sample_rate) {
    if (!tx_handle || !data || len == 0) {
        return ESP_FAIL;
    }

    // I2S speaker output is 16-bit PCM.
    //
    // Why:
    // If an odd number of bytes is written to I2S, the next block can start
    // half a sample late. That creates harsh digital noise.
    if (len & 1) {
        ESP_LOGW(TAG, "Odd PCM length received by I2S: %zu, trimming 1 byte", len);
        len--;
    }

    if (len == 0) {
        return ESP_OK;
    }

    if (sample_rate < 8000 || sample_rate > 48000) {
        ESP_LOGE(TAG, "Invalid playback sample rate: %d", sample_rate);
        return ESP_ERR_INVALID_ARG;
    }

    esp_err_t result = ESP_OK;
    bool locked = false;

    if (tx_mutex) {
        xSemaphoreTake(tx_mutex, portMAX_DELAY);
        locked = true;
    }

    static int s_current_tx_sample_rate = 0;

    if (s_current_tx_sample_rate != sample_rate) {
        esp_err_t err;

        err = i2s_channel_disable(tx_handle);

        if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {

            ESP_LOGW(TAG, "I2S TX disable before reconfig returned: %s",
                     esp_err_to_name(err));
        }

        i2s_std_clk_config_t clk = {
            .sample_rate_hz = sample_rate,
            .clk_src        = I2S_CLK_SRC_DEFAULT,
            .mclk_multiple  = I2S_MCLK_MULTIPLE_256
        };

        err = i2s_channel_reconfig_std_clock(tx_handle, &clk);

        if (err != ESP_OK) {
            ESP_LOGE(TAG, "I2S TX clock reconfig failed: %s",
                     esp_err_to_name(err));

            result = err;
            goto cleanup;
        }

        err = i2s_channel_enable(tx_handle);

        if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
            ESP_LOGE(TAG, "I2S TX enable failed: %s",
                     esp_err_to_name(err));

            result = err;
            goto cleanup;
        }

        s_current_tx_sample_rate = sample_rate;

        ESP_LOGI(TAG, "I2S TX configured at %dHz", sample_rate);
    }

    size_t offset = 0;
    const size_t chunk = 1024;

    while (offset < len) {
        size_t written = 0;

        size_t to_write = (len - offset) < chunk
            ? (len - offset)
            : chunk;

        // Keep every write even-sized.
        //
        // Why:
        // 16-bit mono PCM = 2 bytes per sample.
        // Writing odd byte counts can corrupt sample alignment.
        if (to_write & 1) {
            to_write--;
        }

        if (to_write == 0) {
            break;
        }

        esp_err_t err = i2s_channel_write(
            tx_handle,
            data + offset,
            to_write,
            &written,
            pdMS_TO_TICKS(1000)
        );

        if (err != ESP_OK) {
            ESP_LOGE(TAG, "I2S write failed: %s",
                     esp_err_to_name(err));

            result = err;
            goto cleanup;
        }

        if (written == 0) {
            ESP_LOGE(TAG, "I2S write returned 0 bytes");

            result = ESP_FAIL;
            goto cleanup;
        }

        offset += written;
    }

    ESP_LOGD(TAG, "Played %zu bytes at %dHz", len, sample_rate);

cleanup:
    if (locked) {
        xSemaphoreGive(tx_mutex);
    }

    return result;
}

void audio_tone_test(void) {
    if (!tx_handle) return;
    ESP_LOGI(TAG, "Tone test...");
    int sr = 22050;
    int n  = sr;
    int16_t* tone = malloc(n * sizeof(int16_t));
    if (!tone) return;
    for (int i = 0; i < n; i++) {
        float t = (float)i / sr;
        tone[i] = (int16_t)(32767.0f * 0.5f *
                  sinf(2.0f * 3.14159f * 440.0f * t));
    }
    size_t written = 0;
    i2s_channel_write(tx_handle, tone,
                      n * sizeof(int16_t),
                      &written,
                      pdMS_TO_TICKS(3000));
    ESP_LOGI(TAG, "Tone wrote %d bytes", written);
    free(tone);
}