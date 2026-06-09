# ZYRA

ZYRA is a custom local smart home voice assistant built using an **ESP32-S3** and a **Python AI server**.

The ESP32-S3 captures voice using an INMP441 microphone, sends the audio to the local AI server through WebSocket, understands speech using Faster-Whisper, generates intelligent responses through Ollama, converts replies into speech using Piper TTS, and plays them back through a MAX98357 I2S amplifier.

The goal of ZYRA is to become a private Jarvis-style home assistant that can talk naturally, remember useful context, show system states on an OLED display, and intelligently understand smart-home commands such as turning devices on, turning devices off, and controlling grouped home-theater devices without depending on cloud assistants.

The physical relay board firmware is maintained in a separate Smart-Switch-Board repository, while this repository contains the ZYRA assistant brain, smart-home intelligence layer, ESP32-S3 voice interface and offline connectivity support.


---

## Project Structure

```text
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
* INMP441 microphone input
* MAX98357 speaker output
* OLED display states
* Audio capture and playback
* PSRAM audio buffer allocation
* Local HTTP relay status fetching in offline mode

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
| `main/display.c`             | OLED display handling, including offline and relay status screens                         |
| `main/display.h`             | Display state definitions                                                                 |
| `main/zyra_config.example.h` | Safe example Wi-Fi, server, and offline relay configuration                               |
| `partitions.csv`             | ESP32 flash partition layout                                                              |
| `sdkconfig`                  | Working ESP-IDF project configuration                                                     |

---

## Smart Home Intelligence

ZYRA can identify smart-home commands from natural speech and route them to the correct device action.

Supported smart-home capabilities include:

* Turning individual devices on or off
* Understanding device aliases
* Handling grouped commands
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

```text
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

```text
User voice
→ ESP32-S3 microphone
→ ZYRA server
→ Speech-to-text
→ Intent router
→ Smart-home command handler
→ Local HTTP request to relay board
→ Voice confirmation from ZYRA
```

The relay-board firmware is not stored in this repository. It belongs in the separate Smart-Switch-Board repository.

---

## Offline Relay Connectivity

ZYRA now includes firmware-side offline relay connectivity.

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

```text
Offline relay connectivity does not mean the full AI assistant runs only on the ESP32-S3.

The ZYRA Python server is still required for speech-to-text, AI response generation, memory, and text-to-speech.

The offline relay firmware module provides local relay-board connectivity and relay-control functions, but full offline voice command understanding on the ESP32-S3 is not completed yet.
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

```text
/status
```

Expected status payload format:

```text
TV,SOUNDBAR,SUBWOOFER,REAR
```

Example:

```text
1,0,1,0
```

Meaning:

```text
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

```powershell
cd C:\Users\abhia\Documents\ZYRA\zyra-server
```

Create a virtual environment:

```powershell
py -3.11 -m venv .venv
```

Activate it:

```powershell
.\.venv\Scripts\Activate.ps1
```

Upgrade pip tools:

```powershell
python -m pip install --upgrade pip setuptools wheel
```

Install PyTorch CUDA:

```powershell
python -m pip install torch==2.5.1 torchvision==0.20.1 torchaudio==2.5.1 --index-url https://download.pytorch.org/whl/cu121
```

Install project dependencies:

```powershell
python -m pip install -r requirements.txt
python -m pip install piper-tts
```

---

## Piper Model Setup

The Piper `.onnx` voice model is not included in GitHub because it is a large file.

Required local files:

```text
zyra-server/models/en_US-lessac-high.onnx
zyra-server/models/en_US-lessac-high.onnx.json
```

Only the `.json` config file is committed.

Copy the `.onnx` model manually into:

```text
zyra-server/models/
```

---

## Environment Setup

Copy:

```text
zyra-server/.env.example
```

to:

```text
zyra-server/.env
```

Example:

```env
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

```env
SMART_HOME_BASE_URLS=http://192.168.29.97,http://192.168.31.156
```

Do not commit `.env`.

---

## Run the Server

Start Ollama first:

```powershell
ollama run llama3.2:3b-instruct-q4_0
```

Then run the ZYRA server:

```powershell
cd C:\Users\abhia\Documents\ZYRA\zyra-server
.\.venv\Scripts\Activate.ps1
python main.py
```

Expected output:

```text
Whisper ready
LLM loaded into GPU
TTS engine ready
Memory engine ready
All engines ready
Uvicorn running on http://0.0.0.0:8765
```

Health check:

```text
http://localhost:8765/health
```

---

## Test Server Components

Run the main component test:

```powershell
python test_components.py
```

Expected:

```text
✓ Ollama working
✓ Whisper working
✓ Piper TTS working
✓ Memory working
✓✓✓ All components working
```

Test the WebSocket pipeline:

```powershell
python test_websocket.py --text "Turn on my TV"
```

Interactive test mode:

```powershell
python test_websocket.py --interactive
```

---

## Firmware Setup

Go to the firmware folder:

```powershell
cd C:\Users\abhia\Documents\ZYRA\zyra-firmware
```

Set ESP32-S3 target:

```powershell
idf.py set-target esp32s3
```

Create this private file:

```text
zyra-firmware/main/zyra_config.h
```

Add your Wi-Fi, server, and offline relay details:

```c
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

```text
zyra-firmware/main/zyra_config.example.h
```

---

## Find Laptop IP Address

Run:

```powershell
ipconfig
```

Use the IPv4 address under your Wi-Fi adapter.

Example:

```text
192.168.29.77
```

This IP should be used as `SERVER_IP` in `zyra_config.h`.

---

## Allow Server Through Firewall

Run PowerShell as Administrator:

```powershell
New-NetFirewallRule -DisplayName "ZYRA Server 8765" -Direction Inbound -Protocol TCP -LocalPort 8765 -Action Allow
```

---

## Build and Flash Firmware

```powershell
cd C:\Users\abhia\Documents\ZYRA\zyra-firmware
idf.py build
idf.py -p COM11 flash monitor
```

Replace `COM11` with your actual ESP32-S3 port.

Expected online firmware output:

```text
WiFi connected
Mic initialized on I2S port 1
Amp initialized on I2S port 0
Connected to ZYRA server
ZYRA ready — listening for speech
Capture buffer allocated successfully
ZYRA online
```

Expected server output:

```text
WebSocket /zyra [accepted]
Client connected
connection open
```

Expected offline fallback output when the server is unavailable:

```text
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

```text
zyra-server/.venv/
zyra-server/.env
zyra-server/memory/
zyra-server/models/*.onnx
zyra-firmware/build/
zyra-firmware/main/zyra_config.h
```

Safe to commit:

```text
zyra-server/.env.example
zyra-server/models/en_US-lessac-high.onnx.json
zyra-firmware/main/zyra_config.example.h
zyra-firmware/sdkconfig
```

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

### Piper model not found

Make sure this file exists:

```text
zyra-server/models/en_US-lessac-high.onnx
```

### Capture buffer allocation failed

Enable PSRAM in ESP-IDF menuconfig.

Expected healthy log:

```text
Free PSRAM before alloc: ...
Capture buffer allocated successfully
```

### OLED I2C timeout

Check OLED pins in `display.c`:

```c
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
* OLED states for offline relay mode

Not included yet:

* Full offline AI execution without the ZYRA server
* Full offline voice command understanding directly on the ESP32-S3
* Wake word refinement
* Smart Extension relay-board firmware inside this repository

Next:

* End-to-end smart-home voice testing
* Offline relay command execution testing
* OLED display stabilization
* Wake word refinement
* Separate Smart-Switch-Board repository cleanup
