#include "offline_relay.h"
#include "zyra_config.h"

#include "esp_http_client.h"
#include "esp_log.h"

#include <stdio.h>
#include <string.h>

static const char* TAG = "OFFLINE_RELAY";
static char relay_base_url[96] = RELAY_HOME_BASE_URL;

typedef struct {
    char* buffer;
    size_t buffer_size;
    size_t length;
} OfflineHttpResponse;

static bool relay_states[OFFLINE_DEVICE_COUNT] = {
    false, false, false, false
};

void offline_relay_set_base_url(const char* base_url) {
    if (!base_url || base_url[0] == '\0') {
        return;
    }

    snprintf(relay_base_url, sizeof(relay_base_url), "%s", base_url);

    // Remove trailing slash if present.
    size_t len = strlen(relay_base_url);
    while (len > 0 && relay_base_url[len - 1] == '/') {
        relay_base_url[len - 1] = '\0';
        len--;
    }

    ESP_LOGI(TAG, "Relay base URL set to: %s", relay_base_url);
}

const char* offline_relay_get_base_url(void) {
    return relay_base_url;
}

static const char* device_names[OFFLINE_DEVICE_COUNT] = {
    "TV",
    "Soundbar",
    "Subwoofer",
    "Rear speakers"
};

static const char* endpoint_on[OFFLINE_DEVICE_COUNT] = {
    "/sony/on",
    "/sb/on",
    "/sub/on",
    "/rear/on"
};

static const char* endpoint_off[OFFLINE_DEVICE_COUNT] = {
    "/sony/off",
    "/sb/off",
    "/sub/off",
    "/rear/off"
};

static esp_err_t offline_http_event_handler(esp_http_client_event_t* evt) {
    if (evt->event_id != HTTP_EVENT_ON_DATA) {
        return ESP_OK;
    }

    if (!evt->user_data || !evt->data || evt->data_len <= 0) {
        return ESP_OK;
    }

    OfflineHttpResponse* ctx = (OfflineHttpResponse*)evt->user_data;

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

esp_err_t offline_relay_init(void) {
    ESP_LOGI(TAG, "Offline relay module ready: %s", relay_base_url);
    return ESP_OK;
}

static bool offline_http_get(const char* endpoint,
                             char* response,
                             size_t response_size) {
    char url[128];

    snprintf(url, sizeof(url), "%s%s", relay_base_url, endpoint);

    ESP_LOGI(TAG, "GET %s", url);

    OfflineHttpResponse response_ctx = {
        .buffer = response,
        .buffer_size = response_size,
        .length = 0,
    };

    if (response && response_size > 0) {
        response[0] = '\0';
    }

    esp_http_client_config_t config = {
        .url = url,
        .timeout_ms = 3000,
        .event_handler = offline_http_event_handler,
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

static bool parse_status_payload(const char* payload) {
    if (!payload) return false;

    ESP_LOGI(TAG, "Status payload: %s", payload);

    int values[4] = {0, 0, 0, 0};

    int parsed = sscanf(
        payload,
        "%d,%d,%d,%d",
        &values[0],
        &values[1],
        &values[2],
        &values[3]
    );

    if (parsed != 4) {
        ESP_LOGE(TAG, "Invalid status payload");
        return false;
    }

    for (int i = 0; i < 4; i++) {
        if (values[i] != 0 && values[i] != 1) {
            ESP_LOGE(TAG, "Invalid status value");
            return false;
        }

        relay_states[i] = values[i] == 1;
    }

    ESP_LOGI(
        TAG,
        "Synced states: TV=%d SB=%d SUB=%d REAR=%d",
        relay_states[0],
        relay_states[1],
        relay_states[2],
        relay_states[3]
    );

    return true;
}

bool offline_relay_fetch_status(void) {
    char response[64];

    bool ok = offline_http_get(
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

bool offline_relay_get_state(OfflineDevice device) {
    if (device < 0 || device >= OFFLINE_DEVICE_COUNT) {
        return false;
    }

    return relay_states[device];
}

bool offline_relay_set_device(OfflineDevice device,
                              OfflineAction action) {
    if (device < 0 || device >= OFFLINE_DEVICE_COUNT) {
        return false;
    }

    const char* endpoint =
        action == OFFLINE_ACTION_ON
            ? endpoint_on[device]
            : endpoint_off[device];

    bool ok = offline_http_get(endpoint, NULL, 0);

    if (!ok) {
        ESP_LOGE(TAG, "%s command failed", device_names[device]);
        return false;
    }

    bool status_ok = offline_relay_fetch_status();

    ESP_LOGI(
        TAG,
        "%s command sent: %s",
        device_names[device],
        action == OFFLINE_ACTION_ON ? "ON" : "OFF"
    );

    return status_ok;
}

bool offline_relay_toggle_device(OfflineDevice device) {
    if (device < 0 || device >= OFFLINE_DEVICE_COUNT) {
        return false;
    }

    if (!offline_relay_fetch_status()) {
        ESP_LOGE(TAG, "Cannot toggle — status fetch failed");
        return false;
    }

    OfflineAction next_action = relay_states[device]
        ? OFFLINE_ACTION_OFF
        : OFFLINE_ACTION_ON;

    return offline_relay_set_device(device, next_action);
}

bool offline_relay_set_all(OfflineAction action) {
    bool ok = true;

    for (int i = 0; i < OFFLINE_DEVICE_COUNT; i++) {
        const char* endpoint =
            action == OFFLINE_ACTION_ON
                ? endpoint_on[i]
                : endpoint_off[i];

        if (!offline_http_get(endpoint, NULL, 0)) {
            ok = false;
        }
    }

    if (!offline_relay_fetch_status()) {
        ok = false;
    }

    return ok;
}

bool offline_relay_set_sound_system(OfflineAction action) {
    bool ok = true;

    if (!offline_relay_set_device(OFFLINE_DEVICE_SOUNDBAR, action)) {
        ok = false;
    }

    if (!offline_relay_set_device(OFFLINE_DEVICE_SUBWOOFER, action)) {
        ok = false;
    }

    return ok;
}

bool offline_relay_set_all_speakers(OfflineAction action) {
    bool ok = true;

    if (!offline_relay_set_device(OFFLINE_DEVICE_SOUNDBAR, action)) {
        ok = false;
    }

    if (!offline_relay_set_device(OFFLINE_DEVICE_SUBWOOFER, action)) {
        ok = false;
    }

    if (!offline_relay_set_device(OFFLINE_DEVICE_REAR, action)) {
        ok = false;
    }

    return ok;
}

bool offline_relay_set_home_theater(OfflineAction action) {
    return offline_relay_set_all(action);
}