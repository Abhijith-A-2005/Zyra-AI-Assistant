#pragma once

#include <stdbool.h>
#include "esp_err.h"

typedef bool (*wakeword_abort_fn_t)(void);

esp_err_t wakeword_engine_init(void);

bool wakeword_wait_blocking(void);

bool wakeword_wait_blocking_abortable(
    wakeword_abort_fn_t should_abort
);