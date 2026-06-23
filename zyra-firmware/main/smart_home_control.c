#include "smart_home_control.h"
#include "zyra_config.h"

#include "esp_http_client.h"
#include "esp_log.h"

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include <stdio.h>
#include <string.h>
#include <stdlib.h>

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

// Tracks whether HA says each target entity is usable.
static bool s_ha_entity_available[SMART_HOME_DEVICE_COUNT] = {
    true,
    true,
    true,
    true
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

static const char* s_ha_entities[SMART_HOME_DEVICE_COUNT] = {
    HA_ENTITY_TV,
    HA_ENTITY_SOUNDBAR,
    HA_ENTITY_SUBWOOFER,
    HA_ENTITY_REAR
};

static bool smart_home_http_get(const char* endpoint,
                                char* response,
                                size_t response_size);

static bool ha_is_available(void);
bool smart_home_control_home_assistant_available(void) {
    return ha_is_available();
}
static bool ha_fetch_status(void);
static bool ha_set_device(SmartHomeDevice device, SmartHomeAction action);

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
            s_backend = backend;
            smart_home_control_set_base_url(HOME_ASSISTANT_URL);
            ESP_LOGI(TAG, "Backend set: HOME_ASSISTANT");
            return true;

        default:
            ESP_LOGE(TAG, "Unknown smart-home backend: %d", backend);
            return false;
    }
}

SmartHomeBackend smart_home_control_get_backend(void) {
    return s_backend;
}

bool smart_home_control_is_available(void) {
    if (s_backend == SMART_HOME_BACKEND_HOME_ASSISTANT) {
        return ha_is_available();
    }

    char response[32];

    return smart_home_http_get(
        "/ping",
        response,
        sizeof(response)
    );
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

static bool ha_http_request(const char* path,
                            const char* method,
                            const char* body,
                            char* response,
                            size_t response_size) {
    char url[256];

    snprintf(url, sizeof(url), "%s%s", HOME_ASSISTANT_URL, path);

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

    esp_http_client_handle_t client = esp_http_client_init(&config);

    if (!client) {
        ESP_LOGE(TAG, "HA HTTP client init failed");
        return false;
    }

    char auth_header[384];
    snprintf(
        auth_header,
        sizeof(auth_header),
        "Bearer %s",
        HOME_ASSISTANT_TOKEN
    );

    esp_http_client_set_header(client, "Authorization", auth_header);
    esp_http_client_set_header(client, "Content-Type", "application/json");

    if (strcmp(method, "POST") == 0) {
        esp_http_client_set_method(client, HTTP_METHOD_POST);

        if (body) {
            esp_http_client_set_post_field(client, body, strlen(body));
        }
    } else {
        esp_http_client_set_method(client, HTTP_METHOD_GET);
    }

    ESP_LOGI(TAG, "HA %s %s", method, url);

    esp_err_t err = esp_http_client_perform(client);

    if (err != ESP_OK) {
        ESP_LOGE(TAG, "HA request failed: %s", esp_err_to_name(err));
        esp_http_client_cleanup(client);
        return false;
    }

    int status = esp_http_client_get_status_code(client);

    if (status < 200 || status >= 300) {
        ESP_LOGE(TAG, "HA HTTP status %d for %s", status, path);

        if (response && response_size > 0) {
            ESP_LOGE(TAG, "HA error body: %s", response);
        }

        esp_http_client_cleanup(client);
        return false;
    }

    if (response && response_size > 0) {
        ESP_LOGI(TAG, "HA response body: %.160s", response);
    }

    esp_http_client_cleanup(client);
    return true;
}

static bool ha_is_available(void) {
    char response[128];

    return ha_http_request(
        "/api/",
        "GET",
        NULL,
        response,
        sizeof(response)
    );
}

static bool ha_response_entity_unavailable(const char* response) {
    if (!response) {
        return false;
    }

    return strstr(response, "\"state\":\"unavailable\"") ||
           strstr(response, "\"state\":\"unknown\"");
}

static bool ha_parse_state_response(const char* response,
                                    bool* state_out) {
    if (!response || !state_out) {
        return false;
    }

    if (strstr(response, "\"state\":\"on\"")) {
        *state_out = true;
        return true;
    }

    if (strstr(response, "\"state\":\"off\"")) {
        *state_out = false;
        return true;
    }

    ESP_LOGE(TAG, "Could not parse HA state response");
    return false;
}

static bool ha_fetch_device_state(SmartHomeDevice device,
                                  bool* state_out) {
    if (device < 0 || device >= SMART_HOME_DEVICE_COUNT || !state_out) {
        return false;
    }

    char path[160];
    char response[768];

    snprintf(
        path,
        sizeof(path),
        "/api/states/%s",
        s_ha_entities[device]
    );

    bool ok = ha_http_request(
        path,
        "GET",
        NULL,
        response,
        sizeof(response)
    );

    if (!ok) {
        ESP_LOGE(TAG, "HA state fetch failed for %s",
                s_device_names[device]);

        s_ha_entity_available[device] = false;
        return false;
    }

    if (ha_response_entity_unavailable(response)) {
        // HA hub is reachable and the entity exists,
        // but that specific target device/entity is currently unavailable.
        s_ha_entity_available[device] = false;
        *state_out = false;

        ESP_LOGW(TAG, "HA target entity unavailable: %s (%s)",
                s_device_names[device],
                s_ha_entities[device]);

        return true;
    }

    if (ha_parse_state_response(response, state_out)) {
        s_ha_entity_available[device] = true;
        return true;
    }

    s_ha_entity_available[device] = false;
    return false;
}

static bool ha_fetch_status(void) {
    bool any_ok = false;

    for (int i = 0; i < SMART_HOME_DEVICE_COUNT; i++) {
        bool state = false;

        if (ha_fetch_device_state((SmartHomeDevice)i, &state)) {
            s_device_states[i] = state;
            any_ok = true;
        } else {
            ESP_LOGW(TAG, "HA state unavailable for %s",
                     s_device_names[i]);
        }
    }

    if (!any_ok) {
        ESP_LOGE(TAG, "No HA device states could be fetched");
        return false;
    }

    ESP_LOGI(
        TAG,
        "HA synced states: TV=%d SB=%d SUB=%d REAR=%d",
        s_device_states[SMART_HOME_DEVICE_TV],
        s_device_states[SMART_HOME_DEVICE_SOUNDBAR],
        s_device_states[SMART_HOME_DEVICE_SUBWOOFER],
        s_device_states[SMART_HOME_DEVICE_REAR]
    );

    return true;
}

static bool ha_get_entity_domain(const char* entity_id,
                                 char* domain_out,
                                 size_t domain_size) {
    if (!entity_id || !domain_out || domain_size == 0) {
        return false;
    }

    const char* dot = strchr(entity_id, '.');

    if (!dot || dot == entity_id) {
        ESP_LOGE(TAG, "Invalid HA entity_id, cannot extract domain: %s",
                 entity_id ? entity_id : "(null)");
        return false;
    }

    size_t len = dot - entity_id;

    if (len >= domain_size) {
        len = domain_size - 1;
    }

    memcpy(domain_out, entity_id, len);
    domain_out[len] = '\0';

    return true;
}

static bool ha_set_device(SmartHomeDevice device,
                          SmartHomeAction action) {
    if (device < 0 || device >= SMART_HOME_DEVICE_COUNT) {
        return false;
    }

    const char* entity_id = s_ha_entities[device];

    const char* service =
        action == SMART_HOME_ACTION_ON
            ? "turn_on"
            : "turn_off";

    char domain[40];

    if (!ha_get_entity_domain(entity_id, domain, sizeof(domain))) {
        return false;
    }

    char body[192];

    snprintf(
        body,
        sizeof(body),
        "{\"entity_id\":\"%s\"}",
        entity_id
    );

    char path[128];

    snprintf(
        path,
        sizeof(path),
        "/api/services/%s/%s",
        domain,
        service
    );

    bool ok = ha_http_request(
        path,
        "POST",
        body,
        NULL,
        0
    );

    if (!ok) {
        ESP_LOGE(TAG, "HA command failed: %s %s",
                 s_device_names[device],
                 action == SMART_HOME_ACTION_ON ? "ON" : "OFF");
        return false;
    }

    bool refreshed_state = false;

    if (!ha_fetch_device_state(device, &refreshed_state)) {
        ESP_LOGE(
            TAG,
            "HA command accepted, but state refresh failed for %s",
            s_device_names[device]
        );

        return false;
    }

    if (!s_ha_entity_available[device]) {
        ESP_LOGW(
            TAG,
            "HA command understood, but target entity is unavailable: %s (%s)",
            s_device_names[device],
            s_ha_entities[device]
        );

        return false;
    }

    // Entity is available. The service call was accepted.
    // Use optimistic state for quick UI response.
    s_device_states[device] = action == SMART_HOME_ACTION_ON;

    ESP_LOGI(TAG, "HA command OK: %s %s",
            s_device_names[device],
            action == SMART_HOME_ACTION_ON ? "ON" : "OFF");

    return true;
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

    if (s_backend == SMART_HOME_BACKEND_HOME_ASSISTANT) {
        return ha_fetch_status();
    }

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

    if (s_backend == SMART_HOME_BACKEND_HOME_ASSISTANT) {
        return ha_set_device(device, action);
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
        if (!smart_home_control_set_device((SmartHomeDevice)i, action)) {
            ok = false;
        }
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
