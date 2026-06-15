#include "status_led.h"

#include "led_strip.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char* TAG = "STATUS_LED";

// Most ESP32-S3 N16R8 boards use GPIO48 for onboard RGB LED.
#define ZYRA_RGB_LED_GPIO 48
#define ZYRA_RGB_LED_COUNT 1

static led_strip_handle_t s_strip = NULL;
static SemaphoreHandle_t s_led_mutex = NULL;

static volatile StatusLedMode s_led_mode = STATUS_LED_MODE_ONLINE;
static volatile StatusLedState s_led_state = STATUS_LED_IDLE;

typedef struct {
    uint8_t r;
    uint8_t g;
    uint8_t b;
} RgbColor;

static RgbColor get_mode_color(void) {
    switch (s_led_mode) {
        case STATUS_LED_MODE_ONLINE:
            // Blue
            return (RgbColor){0, 0, 35};

        case STATUS_LED_MODE_SERVERLESS:
            // Purple / violet
            return (RgbColor){22, 0, 35};

        case STATUS_LED_MODE_OFFLINE:
            // Amber / orange
            return (RgbColor){35, 12, 0};

        default:
            return (RgbColor){0, 0, 0};
    }
}

static RgbColor scale_color(RgbColor c, uint8_t brightness) {
    RgbColor out = {
        .r = (uint8_t)((c.r * brightness) / 45),
        .g = (uint8_t)((c.g * brightness) / 45),
        .b = (uint8_t)((c.b * brightness) / 45),
    };

    return out;
}

static void led_set_rgb(uint8_t r, uint8_t g, uint8_t b) {
    if (!s_strip || !s_led_mutex) return;

    if (xSemaphoreTake(s_led_mutex, pdMS_TO_TICKS(100)) == pdTRUE) {
        led_strip_set_pixel(s_strip, 0, r, g, b);
        led_strip_refresh(s_strip);
        xSemaphoreGive(s_led_mutex);
    }
}

static void led_set_color(RgbColor c) {
    led_set_rgb(c.r, c.g, c.b);
}

static void led_off(void) {
    if (!s_strip || !s_led_mutex) return;

    if (xSemaphoreTake(s_led_mutex, pdMS_TO_TICKS(100)) == pdTRUE) {
        led_strip_clear(s_strip);
        xSemaphoreGive(s_led_mutex);
    }
}

void status_led_set_mode(StatusLedMode mode) {
    s_led_mode = mode;
}

void status_led_set_state(StatusLedState state) {
    s_led_state = state;

    switch (state) {
        case STATUS_LED_IDLE:
            led_off();
            break;

        case STATUS_LED_MODE_SOLID:
            led_set_color(get_mode_color());
            break;

        case STATUS_LED_SPEAKING:
        case STATUS_LED_COMMAND_SUCCESS:
            // Universal green
            led_set_rgb(0, 35, 0);
            break;

        case STATUS_LED_COMMAND_FAILED:
            // Solid red
            led_set_rgb(45, 0, 0);
            break;

        case STATUS_LED_MODE_BREATHING:
        case STATUS_LED_CONNECTION_FAILED:
            // Animated by task
            break;

        default:
            led_off();
            break;
    }
}

static void status_led_task(void* arg) {
    int breath = 0;
    int direction = 1;
    bool blink_on = false;

    while (true) {
        switch (s_led_state) {
            case STATUS_LED_IDLE:
                led_off();
                vTaskDelay(pdMS_TO_TICKS(250));
                break;

            case STATUS_LED_MODE_SOLID:
                led_set_color(get_mode_color());
                vTaskDelay(pdMS_TO_TICKS(250));
                break;

            case STATUS_LED_MODE_BREATHING: {
                // Breathing current mode colour
                breath += direction * 3;

                if (breath >= 45) {
                    breath = 45;
                    direction = -1;
                } else if (breath <= 4) {
                    breath = 4;
                    direction = 1;
                }

                RgbColor c = scale_color(get_mode_color(), (uint8_t)breath);
                led_set_color(c);

                vTaskDelay(pdMS_TO_TICKS(45));
                break;
            }

            case STATUS_LED_SPEAKING:
            case STATUS_LED_COMMAND_SUCCESS:
                led_set_rgb(0, 35, 0);
                vTaskDelay(pdMS_TO_TICKS(250));
                break;

            case STATUS_LED_COMMAND_FAILED:
                led_set_rgb(45, 0, 0);
                vTaskDelay(pdMS_TO_TICKS(250));
                break;

            case STATUS_LED_CONNECTION_FAILED:
                blink_on = !blink_on;

                if (blink_on) {
                    led_set_rgb(45, 0, 0);
                } else {
                    led_off();
                }

                vTaskDelay(pdMS_TO_TICKS(250));
                break;

            default:
                led_off();
                vTaskDelay(pdMS_TO_TICKS(250));
                break;
        }
    }
}

esp_err_t status_led_init(void) {
    led_strip_config_t strip_config = {
        .strip_gpio_num = ZYRA_RGB_LED_GPIO,
        .max_leds = ZYRA_RGB_LED_COUNT,
        .led_pixel_format = LED_PIXEL_FORMAT_GRB,
        .led_model = LED_MODEL_WS2812,
        .flags.invert_out = false,
    };

    led_strip_rmt_config_t rmt_config = {
        .clk_src = RMT_CLK_SRC_DEFAULT,
        .resolution_hz = 10 * 1000 * 1000,
        .mem_block_symbols = 64,
        .flags.with_dma = false,
    };

    esp_err_t err = led_strip_new_rmt_device(
        &strip_config,
        &rmt_config,
        &s_strip
    );

    if (err != ESP_OK) {
        ESP_LOGE(TAG, "LED strip init failed: %s", esp_err_to_name(err));
        return err;
    }

    s_led_mutex = xSemaphoreCreateMutex();
    if (!s_led_mutex) {
        ESP_LOGE(TAG, "LED mutex create failed");
        return ESP_ERR_NO_MEM;
    }

    led_off();

    BaseType_t ok = xTaskCreatePinnedToCore(
        status_led_task,
        "StatusLED",
        3072,
        NULL,
        2,
        NULL,
        1
    );

    if (ok != pdPASS) {
        ESP_LOGE(TAG, "Failed to create LED task");
        return ESP_FAIL;
    }

    ESP_LOGI(TAG, "Status LED initialized on GPIO%d", ZYRA_RGB_LED_GPIO);
    return ESP_OK;
}