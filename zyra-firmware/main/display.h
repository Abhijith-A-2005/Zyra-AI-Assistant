#pragma once
#include "esp_err.h"

typedef enum {
    DISP_BOOTING,
    DISP_CONNECTING,
    DISP_IDLE,
    DISP_WAKE_DETECTED,
    DISP_LISTENING,
    DISP_PROCESSING,
    DISP_SPEAKING,
    DISP_ERROR,
    DISP_OFFLINE,
    DISP_RELAY_OK,
    DISP_RELAY_FAIL
} DisplayState;

esp_err_t display_init(void);
void display_set_state(DisplayState state);
void display_update(DisplayState state);
void display_task(void* param);