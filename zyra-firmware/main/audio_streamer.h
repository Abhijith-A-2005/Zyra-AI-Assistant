#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"

esp_err_t audio_streamer_init(void);

esp_err_t audio_streamer_start(int sample_rate);

bool audio_streamer_push(const uint8_t* data,
                         size_t len,
                         int sample_rate);

void audio_streamer_end(void);

bool audio_streamer_is_busy(void);

void audio_streamer_wait_idle(uint32_t timeout_ms);