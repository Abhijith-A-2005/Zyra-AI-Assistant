#include "offline_voice.h"
#include "audio_pipeline.h"

#include "esp_log.h"
#include "esp_heap_caps.h"

#include "model_path.h"
#include "esp_mn_iface.h"
#include "esp_mn_models.h"
#include "esp_mn_speech_commands.h"

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include <string.h>
#include <stdlib.h>

#include "wakeword_engine.h"

static const char* TAG = "OFFLINE_VOICE";

#define OFFLINE_VOICE_TASK_STACK       8192
#define OFFLINE_VOICE_TASK_PRIO        5

// 0.12 was only for test mode. It is too aggressive.
#define OFFLINE_VOICE_MIN_PROB         0.12f

// INMP441 input is often low for ESP-SR.
// This boosts normal speech before sending it to MultiNet.
#define OFFLINE_VOICE_GAIN             4.0f

// Ignore very low-level noise/silence before detection.
// If normal speech is ignored, reduce this to 350.
#define OFFLINE_VOICE_MIN_PEAK         300

typedef struct {
    OfflineVoiceCommand command;
    const char* phrase;
} OfflineCommandPhrase;

static TaskHandle_t s_voice_task_handle = NULL;
static volatile bool s_voice_running = false;

static offline_voice_callback_t s_command_callback = NULL;
static offline_voice_ui_callback_t s_ui_callback = NULL;
static void offline_voice_emit_ui(OfflineVoiceUiEvent event) {
    if (s_ui_callback) {
        s_ui_callback(event);
    }
}

static bool offline_voice_should_abort_wakeword(void) {
    return !s_voice_running;
}

static srmodel_list_t* s_models = NULL;
static esp_mn_iface_t* s_multinet = NULL;
static model_iface_data_t* s_model_data = NULL;
static char* s_mn_name = NULL;

// Commands are intentionally written in simple English.
// MultiNet command words should not use special symbols or numbers.
static const OfflineCommandPhrase s_commands[] = {
    // ── TV / Sony TV ─────────────────────────────
    {OFFLINE_VOICE_CMD_TV_ON,      "turn on my tv"},
    {OFFLINE_VOICE_CMD_TV_ON,      "turn on tv"},
    {OFFLINE_VOICE_CMD_TV_ON,      "tv on"},
    {OFFLINE_VOICE_CMD_TV_ON,      "sony tv on"},
    {OFFLINE_VOICE_CMD_TV_ON,      "turn on sony tv"},
    {OFFLINE_VOICE_CMD_TV_ON,      "power on tv"},
    {OFFLINE_VOICE_CMD_TV_ON,      "switch on tv"},

    {OFFLINE_VOICE_CMD_TV_OFF,     "turn off my tv"},
    {OFFLINE_VOICE_CMD_TV_OFF,     "turn off tv"},
    {OFFLINE_VOICE_CMD_TV_OFF,     "tv off"},
    {OFFLINE_VOICE_CMD_TV_OFF,     "sony tv off"},
    {OFFLINE_VOICE_CMD_TV_OFF,     "turn off sony tv"},
    {OFFLINE_VOICE_CMD_TV_OFF,     "power off tv"},
    {OFFLINE_VOICE_CMD_TV_OFF,     "switch off tv"},

    {OFFLINE_VOICE_CMD_TV_TOGGLE,  "toggle tv"},
    {OFFLINE_VOICE_CMD_TV_TOGGLE,  "switch tv"},
    {OFFLINE_VOICE_CMD_TV_TOGGLE,  "flip tv"},

    // ── Soundbar ─────────────────────────────────
    {OFFLINE_VOICE_CMD_SOUNDBAR_ON,     "turn on soundbar"},
    {OFFLINE_VOICE_CMD_SOUNDBAR_ON,     "soundbar on"},
    {OFFLINE_VOICE_CMD_SOUNDBAR_ON,     "power on soundbar"},
    {OFFLINE_VOICE_CMD_SOUNDBAR_ON,     "switch on soundbar"},
    {OFFLINE_VOICE_CMD_SOUNDBAR_ON,     "turn on speaker bar"},
    {OFFLINE_VOICE_CMD_SOUNDBAR_ON,     "speaker bar on"},

    {OFFLINE_VOICE_CMD_SOUNDBAR_OFF,    "turn off soundbar"},
    {OFFLINE_VOICE_CMD_SOUNDBAR_OFF,    "soundbar off"},
    {OFFLINE_VOICE_CMD_SOUNDBAR_OFF,    "power off soundbar"},
    {OFFLINE_VOICE_CMD_SOUNDBAR_OFF,    "switch off soundbar"},
    {OFFLINE_VOICE_CMD_SOUNDBAR_OFF,    "turn off speaker bar"},
    {OFFLINE_VOICE_CMD_SOUNDBAR_OFF,    "speaker bar off"},

    {OFFLINE_VOICE_CMD_SOUNDBAR_TOGGLE, "toggle soundbar"},
    {OFFLINE_VOICE_CMD_SOUNDBAR_TOGGLE, "switch soundbar"},

    // ── Subwoofer ────────────────────────────────
    {OFFLINE_VOICE_CMD_SUBWOOFER_ON,     "turn on subwoofer"},
    {OFFLINE_VOICE_CMD_SUBWOOFER_ON,     "subwoofer on"},
    {OFFLINE_VOICE_CMD_SUBWOOFER_ON,     "woofer on"},
    {OFFLINE_VOICE_CMD_SUBWOOFER_ON,     "sub on"},
    {OFFLINE_VOICE_CMD_SUBWOOFER_ON,     "power on subwoofer"},

    {OFFLINE_VOICE_CMD_SUBWOOFER_OFF,    "turn off subwoofer"},
    {OFFLINE_VOICE_CMD_SUBWOOFER_OFF,    "subwoofer off"},
    {OFFLINE_VOICE_CMD_SUBWOOFER_OFF,    "woofer off"},
    {OFFLINE_VOICE_CMD_SUBWOOFER_OFF,    "sub off"},
    {OFFLINE_VOICE_CMD_SUBWOOFER_OFF,    "power off subwoofer"},

    {OFFLINE_VOICE_CMD_SUBWOOFER_TOGGLE, "toggle subwoofer"},
    {OFFLINE_VOICE_CMD_SUBWOOFER_TOGGLE, "toggle woofer"},
    {OFFLINE_VOICE_CMD_SUBWOOFER_TOGGLE, "switch subwoofer"},

    // ── Rear / Surround / Back speakers ──────────
    {OFFLINE_VOICE_CMD_REAR_ON,      "rear speakers on"},
    {OFFLINE_VOICE_CMD_REAR_ON,      "turn on rear speakers"},
    {OFFLINE_VOICE_CMD_REAR_ON,      "surround on"},
    {OFFLINE_VOICE_CMD_REAR_ON,      "turn on surround"},
    {OFFLINE_VOICE_CMD_REAR_ON,      "surround system on"},
    {OFFLINE_VOICE_CMD_REAR_ON,      "back speaker on"},
    {OFFLINE_VOICE_CMD_REAR_ON,      "back speakers on"},
    {OFFLINE_VOICE_CMD_REAR_ON,      "turn on back speakers"},

    {OFFLINE_VOICE_CMD_REAR_OFF,     "rear speakers off"},
    {OFFLINE_VOICE_CMD_REAR_OFF,     "turn off rear speakers"},
    {OFFLINE_VOICE_CMD_REAR_OFF,     "surround off"},
    {OFFLINE_VOICE_CMD_REAR_OFF,     "turn off surround"},
    {OFFLINE_VOICE_CMD_REAR_OFF,     "surround system off"},
    {OFFLINE_VOICE_CMD_REAR_OFF,     "back speaker off"},
    {OFFLINE_VOICE_CMD_REAR_OFF,     "back speakers off"},
    {OFFLINE_VOICE_CMD_REAR_OFF,     "turn off back speakers"},

    {OFFLINE_VOICE_CMD_REAR_TOGGLE,  "toggle rear speakers"},
    {OFFLINE_VOICE_CMD_REAR_TOGGLE,  "toggle surround"},
    {OFFLINE_VOICE_CMD_REAR_TOGGLE,  "switch surround"},

    // ── Sound system = Soundbar + Subwoofer ──────
    {OFFLINE_VOICE_CMD_SOUND_SYSTEM_ON,   "turn on sound system"},
    {OFFLINE_VOICE_CMD_SOUND_SYSTEM_ON,   "sound system on"},
    {OFFLINE_VOICE_CMD_SOUND_SYSTEM_ON,   "turn on audio system"},
    {OFFLINE_VOICE_CMD_SOUND_SYSTEM_ON,   "audio system on"},

    {OFFLINE_VOICE_CMD_SOUND_SYSTEM_OFF,  "turn off sound system"},
    {OFFLINE_VOICE_CMD_SOUND_SYSTEM_OFF,  "sound system off"},
    {OFFLINE_VOICE_CMD_SOUND_SYSTEM_OFF,  "turn off audio system"},
    {OFFLINE_VOICE_CMD_SOUND_SYSTEM_OFF,  "audio system off"},

    // ── All speakers = Soundbar + Subwoofer + Rear ─
    {OFFLINE_VOICE_CMD_ALL_SPEAKERS_ON,   "turn on all speakers"},
    {OFFLINE_VOICE_CMD_ALL_SPEAKERS_ON,   "all speakers on"},
    {OFFLINE_VOICE_CMD_ALL_SPEAKERS_ON,   "turn on speaker system"},
    {OFFLINE_VOICE_CMD_ALL_SPEAKERS_ON,   "speaker system on"},

    {OFFLINE_VOICE_CMD_ALL_SPEAKERS_OFF,  "turn off all speakers"},
    {OFFLINE_VOICE_CMD_ALL_SPEAKERS_OFF,  "all speakers off"},
    {OFFLINE_VOICE_CMD_ALL_SPEAKERS_OFF,  "turn off speaker system"},
    {OFFLINE_VOICE_CMD_ALL_SPEAKERS_OFF,  "speaker system off"},

    // ── Home theater = TV + Soundbar + Subwoofer + Rear ─
    {OFFLINE_VOICE_CMD_HOME_THEATER_ON,   "turn on home theater"},
    {OFFLINE_VOICE_CMD_HOME_THEATER_ON,   "home theater on"},
    {OFFLINE_VOICE_CMD_HOME_THEATER_ON,   "turn on full system"},
    {OFFLINE_VOICE_CMD_HOME_THEATER_ON,   "full system on"},
    {OFFLINE_VOICE_CMD_HOME_THEATER_ON,   "turn on everything"},
    {OFFLINE_VOICE_CMD_HOME_THEATER_ON,   "turn on all devices"},

    {OFFLINE_VOICE_CMD_HOME_THEATER_OFF,  "turn off home theater"},
    {OFFLINE_VOICE_CMD_HOME_THEATER_OFF,  "home theater off"},
    {OFFLINE_VOICE_CMD_HOME_THEATER_OFF,  "turn off full system"},
    {OFFLINE_VOICE_CMD_HOME_THEATER_OFF,  "full system off"},
    {OFFLINE_VOICE_CMD_HOME_THEATER_OFF,  "turn off everything"},
    {OFFLINE_VOICE_CMD_HOME_THEATER_OFF,  "turn off all devices"},

    // ── Status ───────────────────────────────────
    {OFFLINE_VOICE_CMD_STATUS, "status"},
    {OFFLINE_VOICE_CMD_STATUS, "device status"},
    {OFFLINE_VOICE_CMD_STATUS, "check status"},
    {OFFLINE_VOICE_CMD_STATUS, "check devices"},
    {OFFLINE_VOICE_CMD_STATUS, "what is on"},
    {OFFLINE_VOICE_CMD_STATUS, "which devices are on"}
};

static esp_err_t offline_voice_register_commands(void) {
    ESP_LOGI(TAG, "Registering offline voice commands");

    esp_mn_commands_clear();

    size_t count = sizeof(s_commands) / sizeof(s_commands[0]);

    for (size_t i = 0; i < count; i++) {
        esp_mn_commands_add(
            (int)s_commands[i].command,
            (char*)s_commands[i].phrase
        );
    }

    esp_mn_error_t* err = esp_mn_commands_update(
        s_multinet,
        s_model_data
    );

    if (err) {
        ESP_LOGE(TAG, "Some offline voice commands failed to register");
        ESP_LOGE(TAG, "Failed command count: %d", err->num);

        return ESP_FAIL;
    }
    
    esp_mn_commands_print();

    ESP_LOGI(TAG, "Offline voice commands registered: %d",
             (int)count);

    return ESP_OK;
}

static esp_err_t offline_voice_load_model(void) {
    if (s_multinet && s_model_data) {
        return ESP_OK;
    }

    ESP_LOGI(TAG, "Loading ESP-SR models from model partition");

    s_models = esp_srmodel_init("model");

    if (!s_models) {
        ESP_LOGE(TAG, "esp_srmodel_init failed. Check model partition.");
        return ESP_FAIL;
    }

    s_mn_name = esp_srmodel_filter(
        s_models,
        ESP_MN_PREFIX,
        ESP_MN_ENGLISH
    );

    if (!s_mn_name) {
        ESP_LOGE(TAG, "No English MultiNet model found");
        return ESP_FAIL;
    }

    ESP_LOGI(TAG, "Selected MultiNet model: %s", s_mn_name);

    if (!strstr(s_mn_name, "mn6") &&
        !strstr(s_mn_name, "mn7")) {

        ESP_LOGW(TAG,
            "Recommended: use MultiNet6 or MultiNet7 English.");
    }

    s_multinet = esp_mn_handle_from_name(s_mn_name);

    if (!s_multinet) {
        ESP_LOGE(TAG, "esp_mn_handle_from_name failed");
        return ESP_FAIL;
    }

    s_model_data = s_multinet->create(s_mn_name, 6000);

    if (!s_model_data) {
        ESP_LOGE(TAG, "MultiNet create failed");
        return ESP_FAIL;
    }

    ESP_LOGI(TAG, "MultiNet chunk size: %d",
             s_multinet->get_samp_chunksize(s_model_data));

    return offline_voice_register_commands();
}

static int apply_offline_voice_gain(int16_t* samples, int count) {
    int peak = 0;

    for (int i = 0; i < count; i++) {
        int32_t amplified = (int32_t)((float)samples[i] * OFFLINE_VOICE_GAIN);

        if (amplified > 32767) {
            amplified = 32767;
        } else if (amplified < -32768) {
            amplified = -32768;
        }

        samples[i] = (int16_t)amplified;

        int abs_value = samples[i] >= 0 ? samples[i] : -samples[i];

        if (abs_value > peak) {
            peak = abs_value;
        }
    }

    return peak;
}

static void offline_voice_task(void* arg) {
    ESP_LOGI(TAG, "Offline voice task started");

    esp_err_t err = offline_voice_load_model();

    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Offline voice model init failed");
        s_voice_running = false;
        s_voice_task_handle = NULL;
        vTaskDelete(NULL);
        return;
    }

    int chunk_size = s_multinet->get_samp_chunksize(s_model_data);

    int16_t* audio_chunk = heap_caps_malloc(
        chunk_size * sizeof(int16_t),
        MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT
    );

    if (!audio_chunk) {
        ESP_LOGE(TAG, "Failed to allocate offline voice audio chunk");
        s_voice_running = false;
        s_voice_task_handle = NULL;
        vTaskDelete(NULL);
        return;
    }

    s_multinet->clean(s_model_data);

    while (s_voice_running) {
        offline_voice_emit_ui(OFFLINE_VOICE_UI_IDLE);

        ESP_LOGI(TAG, "Offline mode waiting for wake word");

        if (!wakeword_wait_blocking_abortable(
                offline_voice_should_abort_wakeword
            )) {
            if (!s_voice_running) {
                ESP_LOGI(TAG, "Offline wake wait cancelled");
                break;
            }

            vTaskDelay(pdMS_TO_TICKS(100));
            continue;
        }

        ESP_LOGI(TAG, "Offline wake detected. Listening for command.");

        // Wakeword accepted: show small listening pulse.
        offline_voice_emit_ui(OFFLINE_VOICE_UI_LISTENING);

        // Let the user visually see LISTENING before command capture starts.
        // Also prevents the tail of "Jarvis" from entering MultiNet.
        vTaskDelay(pdMS_TO_TICKS(700));

        s_multinet->clean(s_model_data);

        bool command_session_active = true;
        bool hearing_started = false;

        TickType_t command_start = xTaskGetTickCount();
        const uint32_t command_timeout_ms = 6500;

        while (s_voice_running && command_session_active) {
            uint32_t elapsed_ms =
                (xTaskGetTickCount() - command_start) * portTICK_PERIOD_MS;

            if (elapsed_ms >= command_timeout_ms) {
                ESP_LOGI(TAG, "Offline command listen timeout");
                s_multinet->clean(s_model_data);
                offline_voice_emit_ui(OFFLINE_VOICE_UI_IDLE);
                break;
            }

            int filled = 0;

            while (filled < chunk_size && s_voice_running) {
                int request = chunk_size - filled;

                if (request > 256) {
                    request = 256;
                }

                int count = audio_read_wakenet_frame(
                    audio_chunk + filled,
                    request
                );

                if (count > 0) {
                    filled += count;
                } else {
                    vTaskDelay(pdMS_TO_TICKS(10));
                }
            }

            if (!s_voice_running) {
                break;
            }

            int peak = apply_offline_voice_gain(audio_chunk, chunk_size);

            // Once real speech energy appears, switch to bigger active pulse.
            if (!hearing_started && peak >= OFFLINE_VOICE_MIN_PEAK) {
                hearing_started = true;
                offline_voice_emit_ui(OFFLINE_VOICE_UI_HEARING);
            }

            esp_mn_state_t state = s_multinet->detect(
                s_model_data,
                audio_chunk
            );

            if (state == ESP_MN_STATE_DETECTING) {
                // Stay inside the command-listening loop.
                continue;
            }

            if (state == ESP_MN_STATE_DETECTED) {
                esp_mn_results_t* result =
                    s_multinet->get_results(s_model_data);

                if (result && result->num > 0) {
                    int command_id = result->command_id[0];
                    float probability = result->prob[0];
                    const char* phrase = result->string;

                    ESP_LOGI(
                        TAG,
                        "Detected offline command id=%d phrase='%s' prob=%.3f",
                        command_id,
                        phrase,
                        probability
                    );

                    if (probability >= OFFLINE_VOICE_MIN_PROB &&
                        s_command_callback) {

                        offline_voice_emit_ui(OFFLINE_VOICE_UI_THINKING);

                        s_command_callback(
                            (OfflineVoiceCommand)command_id,
                            phrase,
                            probability
                        );
                    } else {
                        ESP_LOGW(
                            TAG,
                            "Ignored low-confidence offline command: %.3f",
                            probability
                        );
                        offline_voice_emit_ui(OFFLINE_VOICE_UI_IDLE);
                    }
                }

                s_multinet->clean(s_model_data);

                // Small cooldown so the same phrase does not fire repeatedly.
                vTaskDelay(pdMS_TO_TICKS(900));

                command_session_active = false;
                break;
            }

            if (state == ESP_MN_STATE_TIMEOUT) {
                ESP_LOGI(TAG, "Offline voice command timeout");
                s_multinet->clean(s_model_data);
                offline_voice_emit_ui(OFFLINE_VOICE_UI_IDLE);

                command_session_active = false;
                break;
            }

            ESP_LOGW(TAG, "Offline voice unknown state: %d", state);
        }
    }

    free(audio_chunk);

    ESP_LOGI(TAG, "Offline voice task stopped");

    s_voice_task_handle = NULL;
    vTaskDelete(NULL);
}

esp_err_t offline_voice_start(offline_voice_callback_t callback) {
    if (s_voice_running) {
        return ESP_OK;
    }

    if (!callback) {
        ESP_LOGE(TAG, "Offline voice callback is NULL");
        return ESP_ERR_INVALID_ARG;
    }

    s_command_callback = callback;
    s_voice_running = true;

    BaseType_t ok = xTaskCreatePinnedToCore(
        offline_voice_task,
        "OfflineVoice",
        OFFLINE_VOICE_TASK_STACK,
        NULL,
        OFFLINE_VOICE_TASK_PRIO,
        &s_voice_task_handle,
        1
    );

    if (ok != pdPASS) {
        ESP_LOGE(TAG, "Failed to create offline voice task");
        s_voice_running = false;
        s_voice_task_handle = NULL;
        return ESP_FAIL;
    }

    return ESP_OK;
}

void offline_voice_stop(void) {
    if (!s_voice_running) {
        return;
    }

    ESP_LOGW(TAG, "Stopping offline voice recognizer");

    s_voice_running = false;

    // Wait briefly for the offline task to exit.
    for (int i = 0; i < 40; i++) {
        if (s_voice_task_handle == NULL) {
            break;
        }

        vTaskDelay(pdMS_TO_TICKS(50));
    }

    if (s_voice_task_handle != NULL) {
        ESP_LOGW(TAG, "Offline voice task did not stop quickly");
    }
}
bool offline_voice_is_running(void) {
    return s_voice_running;
}

void offline_voice_set_ui_callback(
    offline_voice_ui_callback_t callback
) {
    s_ui_callback = callback;
}