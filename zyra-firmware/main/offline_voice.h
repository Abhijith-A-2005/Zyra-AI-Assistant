#pragma once

#include <stdbool.h>
#include "esp_err.h"

typedef enum {
    OFFLINE_VOICE_CMD_NONE = 0,

    OFFLINE_VOICE_CMD_TV_ON,
    OFFLINE_VOICE_CMD_TV_OFF,
    OFFLINE_VOICE_CMD_TV_TOGGLE,

    OFFLINE_VOICE_CMD_SOUNDBAR_ON,
    OFFLINE_VOICE_CMD_SOUNDBAR_OFF,
    OFFLINE_VOICE_CMD_SOUNDBAR_TOGGLE,

    OFFLINE_VOICE_CMD_SUBWOOFER_ON,
    OFFLINE_VOICE_CMD_SUBWOOFER_OFF,
    OFFLINE_VOICE_CMD_SUBWOOFER_TOGGLE,

    OFFLINE_VOICE_CMD_REAR_ON,
    OFFLINE_VOICE_CMD_REAR_OFF,
    OFFLINE_VOICE_CMD_REAR_TOGGLE,

    OFFLINE_VOICE_CMD_SOUND_SYSTEM_ON,
    OFFLINE_VOICE_CMD_SOUND_SYSTEM_OFF,

    OFFLINE_VOICE_CMD_ALL_SPEAKERS_ON,
    OFFLINE_VOICE_CMD_ALL_SPEAKERS_OFF,

    OFFLINE_VOICE_CMD_HOME_THEATER_ON,
    OFFLINE_VOICE_CMD_HOME_THEATER_OFF,

    OFFLINE_VOICE_CMD_STATUS
} OfflineVoiceCommand;

typedef void (*offline_voice_callback_t)(
    OfflineVoiceCommand command,
    const char* phrase,
    float probability
);

typedef enum {
    OFFLINE_VOICE_UI_IDLE = 0,
    OFFLINE_VOICE_UI_LISTENING,
    OFFLINE_VOICE_UI_HEARING,
    OFFLINE_VOICE_UI_THINKING,
    OFFLINE_VOICE_UI_SPEAKING,
    OFFLINE_VOICE_UI_ERROR
} OfflineVoiceUiEvent;

typedef void (*offline_voice_ui_callback_t)(
    OfflineVoiceUiEvent event
);

esp_err_t offline_voice_start(offline_voice_callback_t callback);
void offline_voice_stop(void);
bool offline_voice_is_running(void);
void offline_voice_set_ui_callback(offline_voice_ui_callback_t callback);