#include "smart_home_control.h"
#include "zyra_config.h"

#include "esp_http_client.h"
#include "esp_log.h"

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include <stdio.h>
#include <string.h>

static const char* TAG = "SMART_HOME_CONTROL";

#define SMART_HOME_HTTP_TIMEOUT_MS       3000
#define SMART_HOME_HTTP_RETRY_COUNT      3
#define SMART_HOME_HTTP_RETRY_DELAY_MS   150

static SmartHomeBackend s_backend = SMART_HOME_BACKEND_RELAY_HOME;
static char s_base_url[128] = RELAY_HOME_BASE_URL;

typedef struct {
    char* buffer;
    size_t buffer_size;
    size_t length;
} SmartHomeHttpResponse;

static bool s_device_states[SMART_HOME_DEVICE_COUNT] = {
    false, false, false, false
};

static const char* s_device_names[SMART_HOME_DEVICE_COUNT] = {
    "TV",
    "Soundbar",
    "Subwoofer",
    "Rear speakers"
};

static const char* s_endpoint_on[SMART_HOME_DEVICE_COUNT] = {
    "/sony/on",
    "/sb/on",
    "/sub/on",
    "/rear/on"
};

static const char* s_endpoint_off[SMART_HOME_DEVICE_COUNT] = {
    "/sony/off",
    "/sb/off",
    "/sub/off",
    "/rear/off"
};

static void strip_trailing_slashes(char* value) {
    if (!value) {
        return;
    }

    size_t len = strlen(value);

    while (len > 0 && value[len - 1] == '/') {
        value[len - 1] = '\0';
        len--;
    }
}

static esp_err_t smart_home_http_event_handler(
    esp_http_client_event_t* evt
) {
    if (evt->event_id != HTTP_EVENT_ON_DATA) {
        return ESP_OK;
    }

    if (!evt->user_data || !evt->data || evt->data_len <= 0) {
        return ESP_OK;
    }

    SmartHomeHttpResponse* ctx =
        (SmartHomeHttpResponse*)evt->user_data;

    if (!ctx->buffer || ctx->buffer_size == 0) {
        return ESP_OK;
    }

    size_t remaining = ctx->buffer_size - ctx->length - 1;

    if (remaining == 0) {
        return ESP_OK;
    }

    size_t to_copy = evt->data_len;

    if (to_copy > remaining) {
        to_copy = remaining;
    }

    memcpy(ctx->buffer + ctx->length, evt->data, to_copy);
    ctx->length += to_copy;
    ctx->buffer[ctx->length] = '\0';

    return ESP_OK;
}

void smart_home_control_set_base_url(const char* base_url) {
    if (!base_url || base_url[0] == '\0') {
        ESP_LOGW(TAG, "Ignored empty smart-home base URL");
        return;
    }

    snprintf(s_base_url, sizeof(s_base_url), "%s", base_url);
    strip_trailing_slashes(s_base_url);

    ESP_LOGI(TAG, "Smart-home base URL set to: %s", s_base_url);
}

const char* smart_home_control_get_base_url(void) {
    return s_base_url;
}

bool smart_home_control_set_backend(SmartHomeBackend backend) {
    switch (backend) {
        case SMART_HOME_BACKEND_RELAY_HOME:
            s_backend = backend;
            smart_home_control_set_base_url(RELAY_HOME_BASE_URL);
            ESP_LOGI(TAG, "Backend set: RELAY_HOME");
            return true;

        case SMART_HOME_BACKEND_RELAY_AP:
            s_backend = backend;
            smart_home_control_set_base_url(RELAY_AP_BASE_URL);
            ESP_LOGI(TAG, "Backend set: RELAY_AP");
            return true;

        case SMART_HOME_BACKEND_HOME_ASSISTANT:
            // Reserved for Build Chunk 2B.
            ESP_LOGW(TAG, "Home Assistant backend not implemented yet");
            return false;

        default:
            ESP_LOGE(TAG, "Unknown smart-home backend: %d", backend);
            return false;
    }
}

SmartHomeBackend smart_home_control_get_backend(void) {
    return s_backend;
}

esp_err_t smart_home_control_init(void) {
    s_backend = SMART_HOME_BACKEND_RELAY_HOME;
    smart_home_control_set_base_url(RELAY_HOME_BASE_URL);

    ESP_LOGI(
        TAG,
        "Smart-home control ready. Backend=%d URL=%s",
        s_backend,
        s_base_url
    );

    return ESP_OK;
}

static bool smart_home_http_get_once(const char* endpoint,
                                     char* response,
                                     size_t response_size) {
    char url[192];

    snprintf(url, sizeof(url), "%s%s", s_base_url, endpoint);

    ESP_LOGI(TAG, "GET %s", url);

    SmartHomeHttpResponse response_ctx = {
        .buffer = response,
        .buffer_size = response_size,
        .length = 0,
    };

    if (response && response_size > 0) {
        response[0] = '\0';
    }

    esp_http_client_config_t config = {
        .url = url,
        .timeout_ms = SMART_HOME_HTTP_TIMEOUT_MS,
        .event_handler = smart_home_http_event_handler,
        .user_data = &response_ctx,
    };

    esp_http_client_handle_t client =
        esp_http_client_init(&config);

    if (!client) {
        ESP_LOGE(TAG, "HTTP client init failed");
        return false;
    }

    esp_err_t err = esp_http_client_perform(client);

    if (err != ESP_OK) {
        ESP_LOGE(TAG, "HTTP GET failed: %s", esp_err_to_name(err));
        esp_http_client_cleanup(client);
        return false;
    }

    int status = esp_http_client_get_status_code(client);

    if (status != 200) {
        ESP_LOGE(TAG, "HTTP status: %d", status);
        esp_http_client_cleanup(client);
        return false;
    }

    if (response && response_size > 0) {
        ESP_LOGI(TAG, "HTTP response body: '%s'", response);
    }

    esp_http_client_cleanup(client);
    return true;
}

static bool smart_home_http_get(const char* endpoint,
                                char* response,
                                size_t response_size) {
    for (int attempt = 1;
         attempt <= SMART_HOME_HTTP_RETRY_COUNT;
         attempt++) {

        ESP_LOGI(
            TAG,
            "HTTP attempt %d/%d endpoint=%s",
            attempt,
            SMART_HOME_HTTP_RETRY_COUNT,
            endpoint
        );

        if (smart_home_http_get_once(
                endpoint,
                response,
                response_size
            )) {
            return true;
        }

        if (attempt < SMART_HOME_HTTP_RETRY_COUNT) {
            vTaskDelay(pdMS_TO_TICKS(SMART_HOME_HTTP_RETRY_DELAY_MS));
        }
    }

    ESP_LOGE(TAG, "HTTP endpoint failed after retries: %s", endpoint);
    return false;
}

static bool parse_status_payload(const char* payload) {
    if (!payload) {
        return false;
    }

    ESP_LOGI(TAG, "Status payload: %s", payload);

    int values[SMART_HOME_DEVICE_COUNT] = {0, 0, 0, 0};

    int parsed = sscanf(
        payload,
        "%d,%d,%d,%d",
        &values[0],
        &values[1],
        &values[2],
        &values[3]
    );

    if (parsed != SMART_HOME_DEVICE_COUNT) {
        ESP_LOGE(TAG, "Invalid status payload");
        return false;
    }

    for (int i = 0; i < SMART_HOME_DEVICE_COUNT; i++) {
        if (values[i] != 0 && values[i] != 1) {
            ESP_LOGE(TAG, "Invalid status value");
            return false;
        }

        s_device_states[i] = values[i] == 1;
    }

    ESP_LOGI(
        TAG,
        "Synced states: TV=%d SB=%d SUB=%d REAR=%d",
        s_device_states[SMART_HOME_DEVICE_TV],
        s_device_states[SMART_HOME_DEVICE_SOUNDBAR],
        s_device_states[SMART_HOME_DEVICE_SUBWOOFER],
        s_device_states[SMART_HOME_DEVICE_REAR]
    );

    return true;
}

bool smart_home_control_fetch_status(void) {
    char response[64];

    bool ok = smart_home_http_get(
        "/status",
        response,
        sizeof(response)
    );

    if (!ok) {
        ESP_LOGE(TAG, "Status fetch failed");
        return false;
    }

    return parse_status_payload(response);
}

bool smart_home_control_get_state(SmartHomeDevice device) {
    if (device < 0 || device >= SMART_HOME_DEVICE_COUNT) {
        return false;
    }

    return s_device_states[device];
}

bool smart_home_control_set_device(SmartHomeDevice device,
                                   SmartHomeAction action) {
    if (device < 0 || device >= SMART_HOME_DEVICE_COUNT) {
        return false;
    }

    const char* endpoint =
        action == SMART_HOME_ACTION_ON
            ? s_endpoint_on[device]
            : s_endpoint_off[device];

    bool ok = smart_home_http_get(endpoint, NULL, 0);

    if (!ok) {
        ESP_LOGE(TAG, "%s command failed", s_device_names[device]);
        return false;
    }

    // Optimistic local state update.
    // Why:
    // The command endpoint may succeed even if the immediate /status read
    // is temporarily slow. A failed status read should not convert a
    // successful ON/OFF command into a false failure.
    s_device_states[device] = action == SMART_HOME_ACTION_ON;

    if (!smart_home_control_fetch_status()) {
        ESP_LOGW(
            TAG,
            "Command succeeded but status refresh failed; using optimistic state"
        );
    }

    ESP_LOGI(
        TAG,
        "%s command sent: %s",
        s_device_names[device],
        action == SMART_HOME_ACTION_ON ? "ON" : "OFF"
    );

    return true;
}

bool smart_home_control_toggle_device(SmartHomeDevice device) {
    if (device < 0 || device >= SMART_HOME_DEVICE_COUNT) {
        return false;
    }

    if (!smart_home_control_fetch_status()) {
        ESP_LOGE(TAG, "Cannot toggle — status fetch failed");
        return false;
    }

    SmartHomeAction next_action = s_device_states[device]
        ? SMART_HOME_ACTION_OFF
        : SMART_HOME_ACTION_ON;

    return smart_home_control_set_device(device, next_action);
}

bool smart_home_control_set_all(SmartHomeAction action) {
    bool ok = true;

    for (int i = 0; i < SMART_HOME_DEVICE_COUNT; i++) {
        const char* endpoint =
            action == SMART_HOME_ACTION_ON
                ? s_endpoint_on[i]
                : s_endpoint_off[i];

        if (smart_home_http_get(endpoint, NULL, 0)) {
            s_device_states[i] = action == SMART_HOME_ACTION_ON;
        } else {
            ok = false;
        }
    }

    if (!smart_home_control_fetch_status()) {
        ESP_LOGW(
            TAG,
            "Set-all completed with status refresh failure; using optimistic state"
        );
    }

    return ok;
}

bool smart_home_control_set_sound_system(SmartHomeAction action) {
    bool ok = true;

    if (!smart_home_control_set_device(
            SMART_HOME_DEVICE_SOUNDBAR,
            action
        )) {
        ok = false;
    }

    if (!smart_home_control_set_device(
            SMART_HOME_DEVICE_SUBWOOFER,
            action
        )) {
        ok = false;
    }

    return ok;
}

bool smart_home_control_set_all_speakers(SmartHomeAction action) {
    bool ok = true;

    if (!smart_home_control_set_device(
            SMART_HOME_DEVICE_SOUNDBAR,
            action
        )) {
        ok = false;
    }

    if (!smart_home_control_set_device(
            SMART_HOME_DEVICE_SUBWOOFER,
            action
        )) {
        ok = false;
    }

    if (!smart_home_control_set_device(
            SMART_HOME_DEVICE_REAR,
            action
        )) {
        ok = false;
    }

    return ok;
}

bool smart_home_control_set_home_theater(SmartHomeAction action) {
    return smart_home_control_set_all(action);
}
