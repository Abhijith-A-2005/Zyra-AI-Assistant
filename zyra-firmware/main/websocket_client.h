#pragma once
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include "esp_err.h"

typedef void (*ws_disconnect_callback_t)(void);

esp_err_t ws_client_init(const char* server_ip,
                          int port);
esp_err_t ws_send_audio(const uint8_t* data,
                         size_t len);
bool ws_response_ready(void);
size_t ws_get_response(uint8_t** data,
                        int* sample_rate);
void ws_free_response(void);
bool ws_is_connected(void);
void ws_client_stop(void);
void ws_set_disconnect_callback(ws_disconnect_callback_t callback);
esp_err_t ws_send_status(const char* status);
bool ws_response_final(void);