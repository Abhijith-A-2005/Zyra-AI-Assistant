#pragma once

#include <stdbool.h>
#include "esp_err.h"

typedef enum {
    OFFLINE_DEVICE_TV = 0,
    OFFLINE_DEVICE_SOUNDBAR,
    OFFLINE_DEVICE_SUBWOOFER,
    OFFLINE_DEVICE_REAR,
    OFFLINE_DEVICE_COUNT
} OfflineDevice;

typedef enum {
    OFFLINE_ACTION_ON = 0,
    OFFLINE_ACTION_OFF
} OfflineAction;

esp_err_t offline_relay_init(void);

bool offline_relay_fetch_status(void);

bool offline_relay_get_state(OfflineDevice device);

bool offline_relay_set_device(OfflineDevice device,
                              OfflineAction action);

bool offline_relay_set_all(OfflineAction action);

bool offline_relay_set_sound_system(OfflineAction action);

bool offline_relay_set_all_speakers(OfflineAction action);

bool offline_relay_set_home_theater(OfflineAction action);