# ZYRA

ZYRA is a custom local smart home voice assistant built using an **ESP32-S3** and a **Python AI server**.

The ESP32-S3 captures voice using an INMP441 microphone, sends the audio to the local AI server through WebSocket, understands speech using Faster-Whisper, generates intelligent responses through Ollama, converts replies into speech using Piper TTS, and plays them back through a MAX98357 I2S amplifier.

The goal of ZYRA is to become a private Jarvis-style home assistant that can talk naturally, remember useful context, show system states on an OLED display, and intelligently understand smart-home commands such as turning devices on, turning devices off, and controlling grouped home-theater devices without depending on cloud assistants.

The physical relay board firmware is maintained in a separate Smart-Switch-Board repository, while this repository contains the ZYRA assistant brain, smart-home intelligence layer, ESP32-S3 voice interface and offline connectivity support.


The project now includes:

* Server-side smart-home intent intelligence
* Local relay-board HTTP control
* ESP32-S3 online voice pipeline
* Firmware-side offline relay fallback
* Firmware-side offline speech connectivity support
* SPIFFS-based offline assets

---

## Project Structure

```text id="uq3brb"
ZYRA/
├── README.md
├── .gitignore
│
├── zyra-server/
│   ├── .env.example
│   ├── audio_utils.py
│   ├── config.py
│   ├── intent_router.py
│   ├── llm.py
│   ├── main.py
│   ├── memory.py
│   ├── requirements.txt
│   ├── smart_home.py
│   ├── stt.py
│   ├── test_components.py
│   ├── test_tts_api.py
│   ├── test_websocket.py
│   ├── tts.py
│   └── models/
│       └── en_US-lessac-high.onnx.json
│
└── zyra-firmware/
    ├── .gitignore
    ├── CMakeLists.txt
    ├── partitions.csv
    ├── sdkconfig
    ├── spiffs/
    │   └── offline speech / command assets
    │
    └── main/
        ├── CMakeLists.txt
        ├── audio_pipeline.c
        ├── audio_pipeline.h
        ├── display.c
        ├── display.h
        ├── idf_component.yml
        ├── main.c
        ├── offline_relay.c
        ├── offline_relay.h
        ├── offline_speech.c
        ├── offline_speech.h
        ├── websocket_client.c
        ├── websocket_client.h
        └── zyra_config.example.h
```

---

## Folder Overview

### `zyra-server/`

The Python backend brain of ZYRA.

It handles:

* Speech-to-text using Faster-Whisper
* AI response generation using Ollama
* Text-to-speech using Piper
* Memory using ChromaDB and SQLite
* Intent routing for assistant and smart-home commands
* Smart-home command understanding
* Local relay-board HTTP command forwarding
* WebSocket communication with the ESP32-S3

Important files:

| File                 | Purpose                                                                            |
| -------------------- | ---------------------------------------------------------------------------------- |
| `main.py`            | Starts the FastAPI WebSocket server and runs the main ZYRA pipeline                |
| `config.py`          | Server, model, audio, memory, and smart-home configuration                         |
| `intent_router.py`   | Routes transcripts into assistant chat or smart-home control intents               |
| `smart_home.py`      | Handles smart-home command execution and forwards HTTP requests to the relay board |
| `stt.py`             | Speech-to-text engine using Faster-Whisper                                         |
| `llm.py`             | Ollama LLM engine for natural assistant responses                                  |
| `tts.py`             | Piper text-to-speech engine                                                        |
| `memory.py`          | ChromaDB and SQLite memory handling                                                |
| `test_components.py` | Tests Ollama, Whisper, Piper, and memory                                           |
| `test_tts_api.py`    | Tests Piper TTS behavior                                                           |
| `test_websocket.py`  | Tests the WebSocket pipeline                                                       |
| `.env.example`       | Example environment configuration                                                  |
| `requirements.txt`   | Python dependencies                                                                |

---

### `zyra-firmware/`

The ESP32-S3 firmware.

It handles:

* Wi-Fi connection
* WebSocket connection to the ZYRA server
* Runtime detection of server disconnects
* Automatic fallback to offline relay AP mode
* Offline relay-board HTTP communication
* Offline speech connectivity support
* SPIFFS asset usage for offline mode
* INMP441 microphone input
* MAX98357 speaker output
* OLED display states
* Audio capture and playback
* PSRAM audio buffer allocation

Important files:

| File                         | Purpose                                                                                   |
| ---------------------------- | ----------------------------------------------------------------------------------------- |
| `main/main.c`                | Main ESP32-S3 firmware logic, online pipeline, Wi-Fi handling, and offline fallback entry |
| `main/audio_pipeline.c`      | I2S microphone and speaker pipeline                                                       |
| `main/audio_pipeline.h`      | Audio pipeline header                                                                     |
| `main/websocket_client.c`    | ESP32 WebSocket client with disconnect callback and stop support                          |
| `main/websocket_client.h`    | WebSocket client header                                                                   |
| `main/offline_relay.c`       | Offline relay HTTP client and relay state sync logic                                      |
| `main/offline_relay.h`       | Offline relay device/action definitions and API                                           |
| `main/offline_speech.c`      | Firmware-side offline speech / limited command handling logic                             |
| `main/offline_speech.h`      | Offline speech module API                                                                 |
| `main/display.c`             | OLED display handling, including offline and relay status screens                         |
| `main/display.h`             | Display state definitions                                                                 |
| `main/zyra_config.example.h` | Safe example Wi-Fi, server, and offline relay configuration                               |
| `spiffs/`                    | Offline speech / command assets used by firmware                                          |
| `partitions.csv`             | ESP32 flash partition layout including storage space for assets                           |
| `sdkconfig`                  | Working ESP-IDF project configuration                                                     |

---

## Smart Home Intelligence

ZYRA can identify smart-home commands from natural speech and route them to the correct device action.

Supported smart-home capabilities include:

* Turning individual devices on or off
* Understanding device aliases
* Handling grouped commands
* Handling mixed commands
* Handling exception commands
* Checking device status
* Controlling home-theater devices through a local relay board
* Giving voice confirmation after the command is handled
* Keeping smart-home control local through the home network

Current controllable devices:

* TV
* Soundbar
* Subwoofer
* Rear speakers

Supported smart-home groups:

| Group                                     | Devices                                   |
| ----------------------------------------- | ----------------------------------------- |
| Sound system                              | Soundbar + Subwoofer                      |
| All speakers                              | Soundbar + Subwoofer + Rear speakers      |
| Home theatre / Home theater / Full system | TV + Soundbar + Subwoofer + Rear speakers |
| Surround system / Rear system             | Rear speakers                             |

Example commands:

```text id="z35yxr"
Turn on my TV
Switch off the soundbar
Turn on the rear speakers
Turn off the sound system
Turn everything off
Power on the home theatre
Turn off the TV and turn on everything else
Turn on everything except TV
Which all devices are on?
```

Typical smart-home flow:

```text id="o4hlxe"
User voice
→ ESP32-S3 microphone
→ ZYRA server
→ Speech-to-text
→ Intent router
→ Smart-home command handler
→ Local HTTP request to relay board
→ Voice confirmation from ZYRA
```

The relay-board firmware is not stored in this repository. It belongs in the separate Smart Extension / Smart-Switch-Board repository.

---

## Offline Relay Connectivity

ZYRA includes firmware-side offline relay connectivity.

This mode is designed for situations where the ESP32-S3 cannot reach the ZYRA Python server or the WebSocket connection drops during runtime. When that happens, the firmware can stop the WebSocket client, switch from the normal home Wi-Fi connection to the ESP8266 relay board's direct AP, and communicate with the relay board locally through HTTP.

Offline relay connectivity focuses on:

* Detecting WebSocket/server loss during online mode
* Stopping the WebSocket client cleanly
* Switching Wi-Fi from home network to relay-board AP
* Using a static offline IP for more reliable relay AP communication
* Fetching relay status through `/status`
* Tracking relay states for TV, soundbar, subwoofer, and rear speakers
* Showing offline/relay status on the OLED display

Important note:

```text id="cgv8ej"
Offline relay connectivity does not mean the full AI assistant runs only on the ESP32-S3.

The ZYRA Python server is still required for full speech-to-text, AI response generation, memory, and text-to-speech.

The offline relay firmware module provides local relay-board connectivity and relay-control functions when the server path is unavailable.
```

---

## Offline Speech Connectivity

ZYRA now includes firmware-side offline speech connectivity support.

This layer is meant to provide a limited local fallback path for relay-related commands when the full ZYRA server pipeline is unavailable. It works together with:

* `offline_speech.c`
* `offline_speech.h`
* `offline_relay.c`
* `offline_relay.h`
* `zyra-firmware/spiffs/`

The goal of this mode is not to replace the full Python AI server. Instead, it provides a lightweight offline path for basic local control behavior.

Offline speech connectivity focuses on:

* Limited firmware-side command handling
* Local relay-control fallback
* SPIFFS-based offline assets
* OLED feedback for offline status
* Reduced dependency on the Python server for basic relay fallback behavior

Current limitation:

```text id="cprq84"
Full offline AI conversation is not included yet.

Offline speech connectivity is only a fallback layer for limited local control behavior.
Natural conversation, memory, full STT, LLM reasoning, and Piper TTS still require the ZYRA Python server.
```

---

## Offline Relay Endpoints

The firmware offline relay module uses these relay-board endpoints:

| Device        | ON endpoint | OFF endpoint |
| ------------- | ----------- | ------------ |
| TV            | `/sony/on`  | `/sony/off`  |
| Soundbar      | `/sb/on`    | `/sb/off`    |
| Subwoofer     | `/sub/on`   | `/sub/off`   |
| Rear speakers | `/rear/on`  | `/rear/off`  |

Status endpoint:

```text id="1tke2r"
/status
```

Expected status payload format:

```text id="axzhgd"
TV,SOUNDBAR,SUBWOOFER,REAR
```

Example:

```text id="l51wwt"
1,0,1,0
```

Meaning:

```text id="lyp9rr"
TV ON
Soundbar OFF
Subwoofer ON
Rear speakers OFF
```

---

## Hardware Used

* ESP32-S3 N16R8
* INMP441 I2S microphone
* MAX98357A I2S amplifier
* 0.96 inch SSD1306 OLED display
* Speaker
* PC or laptop for running the ZYRA server
* Separate ESP8266 Smart Extension relay board

---

## Server Setup

Go to the server folder:

```powershell id="2e51a9"
cd C:\Users\abhia\Documents\ZYRA\zyra-server
```

Create a virtual environment:

```powershell id="34gkc1"
py -3.11 -m venv .venv
```

Activate it:

```powershell id="aa955h"
.\.venv\Scripts\Activate.ps1
```

Upgrade pip tools:

```powershell id="8a2y25"
python -m pip install --upgrade pip setuptools wheel
```

Install PyTorch CUDA:

```powershell id="qsju0c"
python -m pip install torch==2.5.1 torchvision==0.20.1 torchaudio==2.5.1 --index-url https://download.pytorch.org/whl/cu121
```

Install project dependencies:

```powershell id="dg2h5b"
python -m pip install -r requirements.txt
python -m pip install piper-tts
```

---

## Piper Model Setup

The Piper `.onnx` voice model is not included in GitHub because it is a large file.

Required local files:

```text id="8fqmc4"
zyra-server/models/en_US-lessac-high.onnx
zyra-server/models/en_US-lessac-high.onnx.json
```

Only the `.json` config file is committed.

Copy the `.onnx` model manually into:

```text id="v0i2qx"
zyra-server/models/
```

---

## Environment Setup

Copy:

```text id="8uoway"
zyra-server/.env.example
```

to:

```text id="tfz9v5"
zyra-server/.env
```

Example:

```env id="etowq3"
PYTHONIOENCODING=utf-8

OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=llama3.2:3b-instruct-q4_0

HOST=0.0.0.0
PORT=8765

WHISPER_MODEL=small.en
WHISPER_DEVICE=cuda
WHISPER_COMPUTE=float16

SMART_HOME_BASE_URLS=http://192.168.29.97
SMART_HOME_TIMEOUT_SEC=2.0
```

Use the IP address of your Smart Extension relay board for `SMART_HOME_BASE_URLS`.

Multiple relay-board URLs can be given as a comma-separated list:

```env id="5evqgx"
SMART_HOME_BASE_URLS=http://192.168.29.97,http://192.168.31.156
```

Do not commit `.env`.

---

## Run the Server

Start Ollama first:

```powershell id="cq3wzm"
ollama run llama3.2:3b-instruct-q4_0
```

Then run the ZYRA server:

```powershell id="zc5tna"
cd C:\Users\abhia\Documents\ZYRA\zyra-server
.\.venv\Scripts\Activate.ps1
python main.py
```

Expected output:

```text id="przi0b"
Whisper ready
LLM loaded into GPU
TTS engine ready
Memory engine ready
All engines ready
Uvicorn running on http://0.0.0.0:8765
```

Health check:

```text id="vqvzwv"
http://localhost:8765/health
```

---

## Test Server Components

Run the main component test:

```powershell id="r0lh72"
python test_components.py
```

Expected:

```text id="o5sd5e"
✓ Ollama working
✓ Whisper working
✓ Piper TTS working
✓ Memory working
✓✓✓ All components working
```

Test the WebSocket pipeline:

```powershell id="jj89i1"
python test_websocket.py --text "Turn on my TV"
```

Interactive test mode:

```powershell id="9ry437"
python test_websocket.py --interactive
```

---

## Firmware Setup

Go to the firmware folder:

```powershell id="qfb1un"
cd C:\Users\abhia\Documents\ZYRA\zyra-firmware
```

Set ESP32-S3 target:

```powershell id="i2ylab"
idf.py set-target esp32s3
```

Create this private file:

```text id="jtz1gr"
zyra-firmware/main/zyra_config.h
```

Add your Wi-Fi, server, and offline relay details:

```c id="3tqrg1"
#pragma once

// ── Home Wi-Fi + ZYRA server ─────────────────────
#define WIFI_SSID      "YOUR_WIFI_NAME"
#define WIFI_PASSWORD  "YOUR_WIFI_PASSWORD"

#define SERVER_IP      "YOUR_LAPTOP_IPV4"
#define SERVER_PORT    8765

// ── Offline relay fallback ───────────────────────
// ESP8266 smart extension direct AP.
#define RELAY_AP_SSID      "ESP-REMOTE-DIRECT"
#define RELAY_AP_PASSWORD  "12345678"

// ESP8266 direct AP IP.
#define RELAY_BASE_URL     "http://192.168.4.1"
```

Do not commit `zyra_config.h`.

A safe template file is included as:

```text id="ipct9g"
zyra-firmware/main/zyra_config.example.h
```

---

## SPIFFS Asset Setup

The firmware uses the `zyra-firmware/spiffs/` folder for offline mode assets.

Make sure the required offline speech or command assets are placed inside:

```text id="vx6w2q"
zyra-firmware/spiffs/
```

The SPIFFS storage partition is configured through:

```text id="1ujza9"
zyra-firmware/partitions.csv
```

Build output files such as `.bin`, `.elf`, and generated storage images should not be committed unless intentionally required. Source assets inside `spiffs/` can be committed if the firmware needs them at runtime.

---

## Find Laptop IP Address

Run:

```powershell id="r2t5ds"
ipconfig
```

Use the IPv4 address under your Wi-Fi adapter.

Example:

```text id="xc30xf"
192.168.29.77
```

This IP should be used as `SERVER_IP` in `zyra_config.h`.

---

## Allow Server Through Firewall

Run PowerShell as Administrator:

```powershell id="nvup0h"
New-NetFirewallRule -DisplayName "ZYRA Server 8765" -Direction Inbound -Protocol TCP -LocalPort 8765 -Action Allow
```

---

## Build and Flash Firmware

```powershell id="1k644l"
cd C:\Users\abhia\Documents\ZYRA\zyra-firmware
idf.py build
idf.py -p COM11 flash monitor
```

Replace `COM11` with your actual ESP32-S3 port.

Expected online firmware output:

```text id="tlmlwu"
WiFi connected
Mic initialized on I2S port 1
Amp initialized on I2S port 0
Connected to ZYRA server
ZYRA ready — listening for speech
Capture buffer allocated successfully
ZYRA online
```

Expected server output:

```text id="ycz9ey"
WebSocket /zyra [accepted]
Client connected
connection open
```

Expected offline fallback output when the server is unavailable:

```text id="xef6rm"
Server connection failed
Entering offline relay mode
Switching to offline relay AP
Connected to offline relay AP
Offline relay module ready
Offline relay status synced
ZYRA offline relay mode active
```

---

## Current Pin Configuration

### INMP441 Microphone

| INMP441 | ESP32-S3 |
| ------- | -------- |
| SCK     | GPIO 1   |
| WS      | GPIO 2   |
| SD      | GPIO 3   |
| VCC     | 3.3V     |
| GND     | GND      |

### MAX98357A Amplifier

| MAX98357A | ESP32-S3 |
| --------- | -------- |
| BCLK      | GPIO 4   |
| LRC       | GPIO 5   |
| DIN       | GPIO 21  |
| VIN       | 5V       |
| GND       | GND      |

### OLED Display

| OLED | ESP32-S3 |
| ---- | -------- |
| SDA  | GPIO 15  |
| SCL  | GPIO 16  |
| VCC  | 3.3V     |
| GND  | GND      |

---

## Important Notes

Do not commit:

```text id="vg2j99"
zyra-server/.venv/
zyra-server/.env
zyra-server/memory/
zyra-server/models/*.onnx
zyra-firmware/build/
zyra-firmware/main/zyra_config.h
zyra-repomix-output.xml
```

Safe to commit:

```text id="s0bdt7"
zyra-server/.env.example
zyra-server/models/en_US-lessac-high.onnx.json
zyra-firmware/main/zyra_config.example.h
zyra-firmware/sdkconfig
zyra-firmware/spiffs/
```

Only commit `zyra-firmware/spiffs/` if it contains required source assets for offline mode. Do not commit generated binary build outputs.

The Smart Extension relay-board firmware should be committed in its own separate repository.

---

## Troubleshooting

### ESP32 connects to Wi-Fi but not server

Check:

* Server is running
* ESP32-S3 and laptop are on the same Wi-Fi
* `SERVER_IP` is the laptop IPv4 address
* Firewall allows port `8765`
* WebSocket path is `/zyra`

### Smart-home command is understood but device does not respond

Check:

* Smart Extension relay board is powered
* Relay board and ZYRA server are on the same network
* `SMART_HOME_BASE_URLS` points to the relay board IP
* Relay board HTTP endpoints are working
* Browser can open the relay board status page
* `/status` returns four comma-separated values

### Offline relay mode is not working

Check:

* ESP8266 relay board direct AP is enabled
* `RELAY_AP_SSID` matches the ESP8266 AP name
* `RELAY_AP_PASSWORD` is correct
* `RELAY_BASE_URL` is correct
* ESP8266 direct AP IP is reachable
* `/status` works at the relay base URL
* Router/client isolation is not involved when using normal LAN mode

### Offline speech connectivity is not working

Check:

* `offline_speech.c` and `offline_speech.h` are included in the firmware build
* `zyra-firmware/main/CMakeLists.txt` includes the offline speech source file
* Required assets exist inside `zyra-firmware/spiffs/`
* `partitions.csv` has enough storage space for SPIFFS assets
* The SPIFFS image is being generated and flashed correctly
* Offline relay mode is successfully connecting to the relay-board AP

### Piper model not found

Make sure this file exists:

```text id="lccl8y"
zyra-server/models/en_US-lessac-high.onnx
```

### Capture buffer allocation failed

Enable PSRAM in ESP-IDF menuconfig.

Expected healthy log:

```text id="awfzuj"
Free PSRAM before alloc: ...
Capture buffer allocated successfully
```

### OLED I2C timeout

Check OLED pins in `display.c`:

```c id="746pah"
#define OLED_SDA 15
#define OLED_SCL 16
```

If your OLED test sketch uses different pins, update `display.c` to match your real wiring.

---

## Current Status

Completed:

* PC-side AI server
* Faster-Whisper STT
* Ollama LLM
* Piper TTS
* Memory engine
* Intent routing
* Smart-home command understanding
* Local relay-board command forwarding from the server
* ESP32-S3 Wi-Fi connection
* ESP32-S3 WebSocket connection
* WebSocket disconnect callback support
* Runtime fallback trigger when server connection is lost
* I2S mic initialization
* I2S amplifier initialization
* PSRAM audio buffer allocation
* Firmware-side offline relay AP connectivity
* Firmware-side offline relay status sync
* Firmware-side offline speech connectivity support
* SPIFFS asset support for offline mode
* OLED states for offline relay mode

Not included yet:

* Full offline AI execution without the ZYRA server
* Full natural conversation directly on the ESP32-S3
* Wake word refinement
* Smart Extension relay-board firmware inside this repository