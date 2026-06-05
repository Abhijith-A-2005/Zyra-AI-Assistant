#pragma once
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include "esp_err.h"

esp_err_t ws_client_init(const char* server_ip,
                          int port);
esp_err_t ws_send_audio(const uint8_t* data,
                         size_t len);
bool ws_response_ready(void);
size_t ws_get_response(uint8_t** data,
                        int* sample_rate);
void ws_free_response(void);
bool ws_is_connected(void);
esp_err_t ws_send_status(const char* status);