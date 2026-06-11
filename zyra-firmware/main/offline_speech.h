#pragma once

#include "esp_err.h"

esp_err_t offline_speech_init(void);
esp_err_t offline_speech_play(const char* filename);