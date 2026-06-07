#include "display.h"
#include "driver/i2c_master.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <string.h>
#include <stdlib.h>
#include <math.h>

static const char* TAG = "DISPLAY";

#define OLED_I2C_ADDR  0x3C
#define OLED_SDA       15
#define OLED_SCL       16
#define OLED_WIDTH     128
#define OLED_HEIGHT    64

static i2c_master_dev_handle_t s_i2c_dev = NULL;
static uint8_t fb[OLED_WIDTH * OLED_HEIGHT / 8];
static volatile DisplayState current_state
    = DISP_BOOTING;
void display_set_state(DisplayState state) {
    current_state = state;
}

// ── I2C write ─────────────────────────────────────
static void oled_write(uint8_t* data, size_t len) {
    if (!s_i2c_dev) return;
    i2c_master_transmit(s_i2c_dev, data, len,
                        pdMS_TO_TICKS(100));
}

static void oled_cmd(uint8_t cmd) {
    uint8_t buf[2] = {0x00, cmd};
    oled_write(buf, 2);
}

static void oled_data_buf(uint8_t* data, size_t len) {
    uint8_t* buf = malloc(len + 1);
    if (!buf) return;
    buf[0] = 0x40;
    memcpy(buf + 1, data, len);
    oled_write(buf, len + 1);
    free(buf);
}

// ── Framebuffer ops ───────────────────────────────
static void fb_clear(void) {
    memset(fb, 0, sizeof(fb));
}

static void fb_flush(void) {
    oled_cmd(0x21); oled_cmd(0); oled_cmd(127);
    oled_cmd(0x22); oled_cmd(0); oled_cmd(7);
    oled_data_buf(fb, sizeof(fb));
}

static void fb_pixel(int x, int y, int on) {
    if (x < 0 || x >= OLED_WIDTH ||
        y < 0 || y >= OLED_HEIGHT) return;
    int byte = x + (y / 8) * OLED_WIDTH;
    int bit  = y % 8;
    if (on) fb[byte] |=  (1 << bit);
    else    fb[byte] &= ~(1 << bit);
}

static void fb_rect(int x, int y,
                     int w, int h, int fill) {
    for (int i = x; i < x + w; i++)
        for (int j = y; j < y + h; j++)
            fb_pixel(i, j, fill);
}

static void fb_hline(int x, int y, int w) {
    for (int i = x; i < x + w; i++)
        fb_pixel(i, y, 1);
}

// Simple 5x7 font
static const uint8_t font5x7[][5] = {
    {0x00,0x00,0x00,0x00,0x00}, // space
    {0x7C,0x12,0x11,0x12,0x7C}, // A
    {0x7F,0x49,0x49,0x49,0x36}, // B
    {0x3E,0x41,0x41,0x41,0x22}, // C
    {0x7F,0x41,0x41,0x22,0x1C}, // D
    {0x7F,0x49,0x49,0x49,0x41}, // E
    {0x7F,0x09,0x09,0x09,0x01}, // F
    {0x3E,0x41,0x49,0x49,0x7A}, // G
    {0x7F,0x08,0x08,0x08,0x7F}, // H
    {0x00,0x41,0x7F,0x41,0x00}, // I
    {0x20,0x40,0x41,0x3F,0x01}, // J
    {0x7F,0x08,0x14,0x22,0x41}, // K
    {0x7F,0x40,0x40,0x40,0x40}, // L
    {0x7F,0x02,0x0C,0x02,0x7F}, // M
    {0x7F,0x04,0x08,0x10,0x7F}, // N
    {0x3E,0x41,0x41,0x41,0x3E}, // O
    {0x7F,0x09,0x09,0x09,0x06}, // P
    {0x3E,0x41,0x51,0x21,0x5E}, // Q
    {0x7F,0x09,0x19,0x29,0x46}, // R
    {0x46,0x49,0x49,0x49,0x31}, // S
    {0x01,0x01,0x7F,0x01,0x01}, // T
    {0x3F,0x40,0x40,0x40,0x3F}, // U
    {0x1F,0x20,0x40,0x20,0x1F}, // V
    {0x3F,0x40,0x38,0x40,0x3F}, // W
    {0x63,0x14,0x08,0x14,0x63}, // X
    {0x07,0x08,0x70,0x08,0x07}, // Y
    {0x61,0x51,0x49,0x45,0x43}, // Z
    {0x3E,0x51,0x49,0x45,0x3E}, // 0
    {0x00,0x42,0x7F,0x40,0x00}, // 1
    {0x42,0x61,0x51,0x49,0x46}, // 2
    {0x21,0x41,0x45,0x4B,0x31}, // 3
    {0x18,0x14,0x12,0x7F,0x10}, // 4
    {0x27,0x45,0x45,0x45,0x39}, // 5
    {0x3C,0x4A,0x49,0x49,0x30}, // 6
    {0x01,0x71,0x09,0x05,0x03}, // 7
    {0x36,0x49,0x49,0x49,0x36}, // 8
    {0x06,0x49,0x49,0x29,0x1E}, // 9
    {0x00,0x36,0x36,0x00,0x00}, // :
};

static int char_to_idx(char c) {
    if (c == ' ') return 0;
    if (c >= 'A' && c <= 'Z') return 1 + (c - 'A');
    if (c >= 'a' && c <= 'z') return 1 + (c - 'a');
    if (c >= '0' && c <= '9') return 27 + (c - '0');
    if (c == ':') return 37;
    return 0;
}

static void fb_text(int x, int y,
                     const char* text) {
    while (*text) {
        int idx = char_to_idx(*text);
        for (int col = 0; col < 5; col++) {
            uint8_t bits = font5x7[idx][col];
            for (int row = 0; row < 7; row++)
                fb_pixel(x + col, y + row,
                          (bits >> row) & 1);
        }
        x += 6;
        text++;
    }
}

// ── Display init ──────────────────────────────────
esp_err_t display_init(void) {
    i2c_master_bus_config_t bus_cfg = {
        .i2c_port    = I2C_NUM_0,
        .sda_io_num  = OLED_SDA,
        .scl_io_num  = OLED_SCL,
        .clk_source  = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,
    };
    i2c_master_bus_handle_t bus_handle;
    ESP_ERROR_CHECK(i2c_new_master_bus(
                      &bus_cfg, &bus_handle));

    i2c_device_config_t dev_cfg = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address  = OLED_I2C_ADDR,
        .scl_speed_hz    = 400000,
    };
    ESP_ERROR_CHECK(i2c_master_bus_add_device(
                      bus_handle,
                      &dev_cfg,
                      &s_i2c_dev));

    vTaskDelay(pdMS_TO_TICKS(100));

    // SSD1306 init sequence
    oled_cmd(0xAE);
    oled_cmd(0xD5); oled_cmd(0x80);
    oled_cmd(0xA8); oled_cmd(0x3F);
    oled_cmd(0xD3); oled_cmd(0x00);
    oled_cmd(0x40);
    oled_cmd(0x8D); oled_cmd(0x14);
    oled_cmd(0x20); oled_cmd(0x00);
    oled_cmd(0xA1);
    oled_cmd(0xC8);
    oled_cmd(0xDA); oled_cmd(0x12);
    oled_cmd(0x81); oled_cmd(0xCF);
    oled_cmd(0xD9); oled_cmd(0xF1);
    oled_cmd(0xDB); oled_cmd(0x40);
    oled_cmd(0xA4);
    oled_cmd(0xA6);
    oled_cmd(0xAF);

    fb_clear();
    fb_flush();

    ESP_LOGI(TAG, "Display initialized");
    return ESP_OK;
}

// ── Display update ────────────────────────────────
void display_update(DisplayState state) {
    current_state = state;
    fb_clear();

    switch (state) {
        case DISP_BOOTING:
            fb_text(34, 10, "ZYRA");
            fb_hline(0, 22, 128);
            fb_text(14, 30, "INITIALIZING");
            break;

        case DISP_CONNECTING:
            fb_text(22, 10, "CONNECTING");
            fb_hline(0, 22, 128);
            fb_text(28, 34, "WIFI");
            {
                uint32_t t = xTaskGetTickCount()
                    / (250 / portTICK_PERIOD_MS);
                for (int i = 0; i < (int)(t % 4); i++)
                    fb_text(52 + i * 6, 34, ".");
            }
            break;

        case DISP_IDLE:
            fb_text(22, 2, "LISTENING");
            fb_hline(0, 14, 128);
            {
                uint32_t t = xTaskGetTickCount()
                    / (500 / portTICK_PERIOD_MS);
                int r = (t % 2 == 0) ? 8 : 5;
                for (int a = 0; a < 360; a += 15) {
                    float rad = a * 3.14159f / 180.0f;
                    int x = 64 + (int)(r * cosf(rad));
                    int y = 38 + (int)(r * sinf(rad));
                    fb_pixel(x, y, 1);
                }
            }
            break;

        case DISP_WAKE_DETECTED:
            fb_text(34, 8, "ZYRA");
            fb_hline(0, 20, 128);
            fb_text(22, 30, "LISTENING");
            fb_text(28, 46, "...");
            break;

        case DISP_LISTENING:
            fb_text(22, 2, "LISTENING");
            fb_hline(0, 14, 128);
            {
                uint32_t t = xTaskGetTickCount()
                    / (150 / portTICK_PERIOD_MS);
                int heights[] = {8,14,20,16,10,18,12,6};
                for (int i = 0; i < 8; i++) {
                    int h = heights[(t + i) % 8];
                    fb_rect(16 + i * 14, 50 - h,
                             8, h, 1);
                }
            }
            break;

        case DISP_PROCESSING:
            fb_text(22, 8, "THINKING");
            fb_hline(0, 20, 128);
            {
                uint32_t t = xTaskGetTickCount()
                    / (300 / portTICK_PERIOD_MS);
                fb_text(34, 34, "ZYRA");
                for (int i = 0; i < (int)(t % 4); i++)
                    fb_pixel(74 + i * 4, 38, 1);
            }
            break;

        case DISP_SPEAKING:
            fb_text(34, 2, "ZYRA");
            fb_hline(0, 14, 128);
            fb_text(22, 20, "SPEAKING");
            {
                uint32_t t = xTaskGetTickCount()
                    / (150 / portTICK_PERIOD_MS);
                int heights[] = {6,14,20,14,6};
                for (int i = 0; i < 5; i++) {
                    int h = heights[(t + i) % 5];
                    fb_rect(28 + i * 16, 56 - h,
                             10, h, 1);
                }
            }
            break;

        case DISP_ERROR:
            fb_text(34, 10, "ERROR");
            fb_hline(0, 22, 128);
            fb_text(10, 34, "CHECK SERVER");
            break;
    }

    fb_flush();
}

void display_task(void* param) {
    while (true) {
        display_update(current_state);
        vTaskDelay(pdMS_TO_TICKS(100));
    }
}