#pragma once

#include "esp_err.h"

typedef enum {
    STATUS_LED_MODE_ONLINE = 0,
    STATUS_LED_MODE_SERVERLESS,
    STATUS_LED_MODE_OFFLINE
} StatusLedMode;

typedef enum {
    STATUS_LED_IDLE = 0,

    // Uses current mode colour
    STATUS_LED_MODE_SOLID,
    STATUS_LED_MODE_BREATHING,

    // Universal result states
    STATUS_LED_SPEAKING,
    STATUS_LED_COMMAND_SUCCESS,
    STATUS_LED_COMMAND_FAILED,
    STATUS_LED_CONNECTION_FAILED
} StatusLedState;

esp_err_t status_led_init(void);

void status_led_set_mode(StatusLedMode mode);
void status_led_set_state(StatusLedState state);