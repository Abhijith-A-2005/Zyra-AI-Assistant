#pragma once

#include <stdbool.h>
#include "esp_err.h"

typedef enum {
    SMART_HOME_BACKEND_RELAY_HOME = 0,
    SMART_HOME_BACKEND_RELAY_AP,
    SMART_HOME_BACKEND_HOME_ASSISTANT
} SmartHomeBackend;

typedef enum {
    SMART_HOME_DEVICE_TV = 0,
    SMART_HOME_DEVICE_SOUNDBAR,
    SMART_HOME_DEVICE_SUBWOOFER,
    SMART_HOME_DEVICE_REAR,
    SMART_HOME_DEVICE_COUNT
} SmartHomeDevice;

typedef enum {
    SMART_HOME_ACTION_ON = 0,
    SMART_HOME_ACTION_OFF
} SmartHomeAction;

esp_err_t smart_home_control_init(void);

bool smart_home_control_set_backend(SmartHomeBackend backend);
SmartHomeBackend smart_home_control_get_backend(void);

bool smart_home_control_is_available(void);
bool smart_home_control_home_assistant_available(void);

void smart_home_control_set_base_url(const char* base_url);
const char* smart_home_control_get_base_url(void);

bool smart_home_control_fetch_status(void);

bool smart_home_control_get_state(SmartHomeDevice device);

bool smart_home_control_set_device(SmartHomeDevice device,
                                   SmartHomeAction action);

bool smart_home_control_toggle_device(SmartHomeDevice device);

bool smart_home_control_set_all(SmartHomeAction action);

bool smart_home_control_set_sound_system(SmartHomeAction action);

bool smart_home_control_set_all_speakers(SmartHomeAction action);

bool smart_home_control_set_home_theater(SmartHomeAction action);
