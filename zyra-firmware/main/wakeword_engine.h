#pragma once

#include <stdbool.h>
#include "esp_err.h"

esp_err_t wakeword_engine_init(void);
bool wakeword_wait_blocking(void);