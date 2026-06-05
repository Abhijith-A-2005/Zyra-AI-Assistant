#pragma once
#include <stdint.h>
#include <stddef.h>
#include "esp_err.h"

esp_err_t audio_pipeline_init(void);
int audio_read_wakenet_frame(int16_t* buffer,
                              int frame_len);
size_t audio_capture_utterance(uint8_t* buffer,
                                size_t max_bytes);
esp_err_t audio_play_response(const uint8_t* data,
                               size_t len,
                               int sample_rate);