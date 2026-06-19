# ZYRA

ZYRA is a custom local smart-home voice assistant built using an **ESP32-S3** and a **Python AI server**.

The ESP32-S3 captures voice using an INMP441 microphone, sends the audio to the local AI server through WebSocket, understands speech using Faster-Whisper, generates intelligent responses through Ollama, converts replies into speech using Kokoro TTS with the `af_heart` voice, and plays them back through a MAX98357 I2S amplifier.

The goal of ZYRA is to become a private Jarvis-style home assistant that can talk naturally, remember useful context, show system states on an OLED display, provide RGB LED feedback, wake through a local hardware wakeword, and intelligently understand smart-home commands such as turning devices on, turning devices off, checking device status, and controlling grouped home-theater devices without depending on cloud assistants.

The physical relay-board firmware is maintained in a separate Smart-Switch-Board repository, while this repository contains the ZYRA assistant brain, smart-home intelligence layer, ESP32-S3 voice interface, local wakeword firmware, offline voice command layer, SPIFFS prompt assets, RGB status LED feedback, and serverless relay fallback support.

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
│   ├── home_assistant_client.py
│   ├── intent_router.py
│   ├── llm.py
│   ├── main.py
│   ├── memory.py
│   ├── relay_http_client.py
│   ├── requirements.txt
│   ├── smart_home.py
│   ├── smart_home_backends.py
│   ├── stt.py
│   ├── test_components.py
│   ├── test_home_assistant.py
│   ├── test_tts_api.py
│   ├── test_websocket.py
│   └── tts.py
│
└── zyra-firmware/
    ├── .gitignore
    ├── CMakeLists.txt
    ├── partitions.csv
    ├── sdkconfig
    ├── spiffs/
    │   ├── all_speakers_off.wav
    │   ├── all_speakers_on.wav
    │   ├── done.wav
    │   ├── failed.wav
    │   ├── home_theater_off.wav
    │   ├── home_theater_on.wav
    │   ├── rear_off.wav
    │   ├── rear_on.wav
    │   ├── sound_system_off.wav
    │   ├── sound_system_on.wav
    │   ├── soundbar_off.wav
    │   ├── soundbar_on.wav
    │   ├── status.wav
    │   ├── status_0000.wav
    │   ├── status_0001.wav
    │   ├── status_0010.wav
    │   ├── status_0011.wav
    │   ├── status_0100.wav
    │   ├── status_0101.wav
    │   ├── status_0110.wav
    │   ├── status_0111.wav
    │   ├── status_1000.wav
    │   ├── status_1001.wav
    │   ├── status_1010.wav
    │   ├── status_1011.wav
    │   ├── status_1100.wav
    │   ├── status_1101.wav
    │   ├── status_1110.wav
    │   ├── status_1111.wav
    │   ├── subwoofer_off.wav
    │   ├── subwoofer_on.wav
    │   ├── tv_off.wav
    │   └── tv_on.wav
    │
    └── main/
        ├── CMakeLists.txt
        ├── audio_pipeline.c
        ├── audio_pipeline.h
        ├── display.c
        ├── display.h
        ├── idf_component.yml
        ├── main.c
        ├── offline_speech.c
        ├── offline_speech.h
        ├── offline_voice.c
        ├── offline_voice.h
        ├── smart_home_control.c
        ├── smart_home_control.h
        ├── status_led.c
        ├── status_led.h
        ├── wakeword_engine.c
        ├── wakeword_engine.h
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
* Text-to-speech using Kokoro TTS
* Memory using ChromaDB and SQLite
* Intent routing for assistant and smart-home commands
* Home Assistant smart-home control
* ESP8266 relay-board HTTP fallback control
* WebSocket communication with the ESP32-S3
* Fast `/health` server liveness check for firmware
* Separate smart-home health checking for Home Assistant and relay fallback debugging

Important files:

| File                       | Purpose                                                                                      |
| -------------------------- | -------------------------------------------------------------------------------------------- |
| `main.py`                  | Starts the FastAPI WebSocket server and runs the main ZYRA pipeline                          |
| `config.py`                | Server, model, audio, memory, Home Assistant, and relay fallback configuration               |
| `home_assistant_client.py` | Home Assistant REST client used for Online HA Mode                                           |
| `relay_http_client.py`     | ESP8266 relay-board HTTP client used for Online Relay Mode fallback                          |
| `smart_home_backends.py`   | Smart-home backend enums and result objects                                                  |
| `smart_home.py`            | Smart-home engine that chooses Home Assistant first and relay fallback second                |
| `intent_router.py`         | Routes transcripts into assistant chat or smart-home control/status intents                  |
| `stt.py`                   | Speech-to-text engine using Faster-Whisper with silence and hallucination filtering          |
| `llm.py`                   | Ollama LLM engine with warmup and persistent model loading                                   |
| `tts.py`                   | Kokoro text-to-speech engine using the `af_heart` voice                                      |
| `memory.py`                | ChromaDB and SQLite memory handling                                                          |
| `test_components.py`       | Tests Ollama, Whisper, Kokoro TTS, and memory                                                |
| `test_home_assistant.py`   | Tests Home Assistant and relay fallback backend availability                                 |
| `test_tts_api.py`          | Generates a Kokoro TTS test WAV                                                              |
| `test_websocket.py`        | Simulates the ESP32-S3 WebSocket client from the PC                                          |

Current server-side smart-home modes:

```text
Mode 1 — Online HA Mode
ESP32-S3 → ZYRA Server → Home Assistant → MQTT → ESP8266 relay board

Mode 2 — Online Relay Mode
ESP32-S3 → ZYRA Server → ESP8266 relay board home IP
```

Mode 1 is preferred when Home Assistant is available. Mode 2 is used automatically when the ZYRA server is online but Home Assistant is unavailable.

### `zyra-firmware/`

The ESP32-S3 firmware for the ZYRA physical assistant device.

It handles:

* Wi-Fi connection
* WebSocket connection to the ZYRA server
* INMP441 microphone input through I2S
* MAX98357A speaker output through I2S
* Wakeword detection using ESP-SR WakeNet
* Limited serverless voice commands using ESP-SR MultiNet
* OLED display feedback
* RGB status LED feedback
* Serverless relay fallback using the ESP8266 relay-board HTTP endpoints
* Automatic return from serverless relay fallback to online mode when the ZYRA server is restored
* SPIFFS WAV prompts for serverless/offline confirmations

Important files:

| File                   | Purpose                                                                             |
| ---------------------- | ----------------------------------------------------------------------------------- |
| `main.c`               | Main firmware runtime flow, Wi-Fi, online/serverless switching, and UI integration  |
| `audio_pipeline.c`     | I2S microphone input and I2S speaker output                                         |
| `websocket_client.c`   | WebSocket client used to communicate with the ZYRA Python server                    |
| `display.c`            | SSD1306 OLED display driver and UI states                                           |
| `smart_home_control.c` | Firmware-side smart-home relay control module                                      |
| `offline_voice.c`      | ESP-SR MultiNet limited local command recognizer                                    |
| `offline_speech.c`     | SPIFFS WAV prompt playback for serverless/offline confirmations                     |
| `wakeword_engine.c`    | ESP-SR WakeNet wakeword engine                                                      |
| `status_led.c`         | Onboard RGB LED status feedback                                                     |
| `zyra_config.example.h`| Safe template for private Wi-Fi, server, and relay settings                         |

Current firmware-side smart-home fallback:

```text
Serverless Relay Fallback
ESP32-S3 → ESP8266 relay board home IP
```

This mode is used when the ZYRA server path is unavailable. The firmware can continue limited relay control locally and later restore back to online mode when the server returns.

---

## Runtime Refinements

This version includes refinements to make ZYRA feel more stable and appliance-like during real use.

Current refinements include:

* Online HA Mode using Home Assistant as the preferred smart-home backend
* Online Relay Mode when Home Assistant is unavailable but the ZYRA server is still online
* Serverless Relay Fallback when the ZYRA server path is unavailable
* Automatic restore from serverless relay fallback back to online mode when the server returns
* Fast `/health` endpoint that checks only whether the ZYRA server is alive
* Separate smart-home backend health/debug path so unavailable relays do not make the server look offline
* Runtime guard to prevent health checks from forcing fallback during an active voice request
* Runtime guard to prevent online and serverless voice loops from running at the same time
* Improved wakeword flow
* Improved offline/serverless voice listening flow
* Better relay fallback behavior using the cleaned `smart_home_control` firmware module
* RGB LED mode/state feedback
* OLED state feedback for online, serverless, offline, listening, thinking, speaking, and error states
* SPIFFS-based prompt playback for serverless relay confirmations
* Jarvis-style Kokoro voice configuration support
* Better STT rejection for silence, low-level noise, and common Whisper hallucinations
* LLM warmup to reduce first-response cold-start delay

---

## RGB Status LED Feedback

ZYRA uses the ESP32-S3 onboard RGB LED for quick visual feedback.

Mode colors:

| Mode       | LED Color      | Meaning                                                          |
| ---------- | -------------- | ---------------------------------------------------------------- |
| Online     | Blue           | ESP32-S3 is connected to the ZYRA server                         |
| Serverless | Purple         | Server path is unavailable but local relay fallback is active     |
| Offline    | Amber / Orange | Emergency offline relay/AP behavior is active                    |

State colors:

| State                      | LED Behavior                 |
| -------------------------- | ---------------------------- |
| Idle                       | Off                          |
| Listening / active mode    | Solid current mode color     |
| Thinking / processing      | Breathing current mode color |
| Speaking / command success | Green                        |
| Command failed             | Solid red                    |
| Connection failed          | Blinking red                 |

---

## Wakeword Support

ZYRA includes hardware wakeword support on the ESP32-S3.

Current wakeword:

```text
Jarvis
```

Wakeword behavior:

```text
Idle
→ Say "Jarvis"
→ Wakeword detected
→ Display switches to listening state
→ ZYRA captures the command
→ Audio is sent to the Python server when online
→ Response is played through the speaker
→ ZYRA returns to idle
```

The firmware uses ESP-SR WakeNet for wakeword detection. ESP-SR is enabled in the firmware component dependencies, and the project configuration enables the Jarvis WakeNet model.

Important note:

```text
The wakeword is handled on the ESP32-S3.
Full natural-language understanding still happens on the Python server when online.
```

---

## Smart Home Intelligence

ZYRA can identify smart-home commands from natural speech and route them to the correct device action.

Supported smart-home capabilities include:

* Turning individual devices on or off
* Toggling individual devices
* Understanding device aliases
* Handling grouped commands
* Handling mixed commands
* Handling exception commands
* Checking device status
* Controlling home-theater devices through Home Assistant when available
* Controlling home-theater devices directly through the ESP8266 relay board when Home Assistant is unavailable
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

Example online commands:

```text
Jarvis, turn on my TV
Jarvis, switch off the soundbar
Jarvis, turn on the rear speakers
Jarvis, turn off the sound system
Jarvis, turn everything off
Jarvis, power on the home theatre
Jarvis, turn off the TV and turn on everything else
Jarvis, turn on everything except TV
Jarvis, which devices are on?
```

Smart-home backend priority while the server is online:

```text
1. Home Assistant backend
2. ESP8266 relay-board home IP fallback
```

Important behavior:

```text
Home Assistant unavailable does not mean the ESP32 must go offline.
If the ZYRA server is still online, the server bypasses Home Assistant and controls the ESP8266 relay board directly.
```

---

## Offline Relay Connectivity

ZYRA includes firmware-side serverless relay connectivity.

This mode is designed for situations where the ESP32-S3 cannot reach the ZYRA Python server or the WebSocket connection drops during runtime. When that happens, the firmware can stop the WebSocket client and continue limited local relay control through the ESP8266 relay board.

Serverless relay connectivity focuses on:

* Detecting WebSocket/server loss during online mode
* Stopping the WebSocket client cleanly
* Using relay-board HTTP endpoints directly through the home Wi-Fi IP
* Using relay status from `/status`
* Tracking relay states for TV, soundbar, subwoofer, and rear speakers
* Showing serverless/offline relay status on the OLED display
* Updating RGB LED mode/state feedback
* Automatically checking whether the ZYRA server has returned
* Restoring online mode when the ZYRA server becomes available again

Important note:

```text
Serverless relay connectivity does not mean the full AI assistant runs only on the ESP32-S3.

The ZYRA Python server is still required for full speech-to-text, AI response generation, memory, and Kokoro text-to-speech.

The firmware-side smart_home_control module provides limited local relay-board control when the server path is unavailable.
```

---

## Offline Voice Commands

ZYRA includes firmware-side offline/serverless voice command recognition.

This is a limited local fallback mode using ESP-SR MultiNet. It is meant for relay-control commands when the Python server is unavailable.

Offline voice supports commands for:

* TV on/off/toggle
* Soundbar on/off/toggle
* Subwoofer on/off/toggle
* Rear speakers on/off/toggle
* Sound system on/off
* All speakers on/off
* Home theater on/off
* Device status

Example offline commands:

```text
turn on my tv
turn off tv
toggle tv
turn on soundbar
turn off soundbar
turn on subwoofer
turn off subwoofer
turn on rear speakers
turn off rear speakers
turn on sound system
turn off sound system
turn on all speakers
turn off all speakers
turn on home theater
turn off home theater
check status
which devices are on
```

Current limitation:

```text
Offline voice mode is for limited relay-control commands only.

Full natural conversation, full STT, LLM reasoning, memory, and Kokoro TTS still require the ZYRA Python server.
```

---

## Offline Speech Prompts

The firmware uses SPIFFS WAV prompts for offline/serverless confirmations.

Examples:

```text
tv_on.wav
tv_off.wav
soundbar_on.wav
soundbar_off.wav
subwoofer_on.wav
subwoofer_off.wav
rear_on.wav
rear_off.wav
sound_system_on.wav
sound_system_off.wav
all_speakers_on.wav
all_speakers_off.wav
home_theater_on.wav
home_theater_off.wav
status.wav
done.wav
failed.wav
```

Status prompts are stored as bit-pattern files:

```text
status_0000.wav
status_0001.wav
...
status_1111.wav
```

The bit order is:

```text
TV, Soundbar, Subwoofer, Rear speakers
```

Example:

```text
status_1010.wav
```

means:

```text
TV on, Soundbar off, Subwoofer on, Rear speakers off
```

These prompt files are stored inside:

```text
zyra-firmware/spiffs/
```

---

## Offline Relay Endpoints

The ESP8266 relay board exposes local HTTP endpoints used by the server and firmware.

Expected endpoints:

| Device        | ON Endpoint | OFF Endpoint |
| ------------- | ----------- | ------------ |
| TV            | `/sony/on`  | `/sony/off`  |
| Soundbar      | `/sb/on`    | `/sb/off`    |
| Subwoofer     | `/sub/on`   | `/sub/off`   |
| Rear speakers | `/rear/on`  | `/rear/off`  |

Status endpoint:

```text
/status
```

Expected status response format:

```text
1,0,1,0
```

The order is:

```text
TV, Soundbar, Subwoofer, Rear speakers
```

Ping endpoint:

```text
/ping
```

Expected response:

```text
pong
```

Important note:

```text
/ping is used only to check whether the relay board is reachable.
/status is used to sync actual device states.
```

---

## Hardware Used

Main ZYRA assistant hardware:

| Component                  | Purpose                                      |
| -------------------------- | -------------------------------------------- |
| ESP32-S3 N16R8             | Main ZYRA firmware device                    |
| INMP441 microphone         | Voice input                                  |
| MAX98357A I2S amplifier    | Speaker output                               |
| Passive speaker            | ZYRA voice playback                          |
| 0.96 inch I2C OLED display | Visual state feedback                        |
| Onboard RGB LED            | Mode/state feedback                          |

Relay-board hardware is maintained separately in the Smart-Switch-Board repository.

Current controllable devices through the relay board:

| Relay | Device        |
| ----- | ------------- |
| 1     | TV            |
| 2     | Soundbar      |
| 3     | Subwoofer     |
| 4     | Rear speakers |

---

## Server Setup

Go to the server folder:

```powershell
cd C:\Users\abhia\Documents\ZYRA\zyra-server
```

Create virtual environment:

```powershell
python -m venv .venv
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
```

---

## Kokoro TTS Setup

Online TTS:

* Generated live by Kokoro in `zyra-server/tts.py`
* Uses the `af_heart` voice
* Runs on CPU so Whisper can continue using CUDA
* Outputs raw PCM audio streamed back to the ESP32-S3

Offline TTS:

* Fixed WAV prompts stored in `zyra-firmware/spiffs/`
* These files are flashed into the ESP32-S3 storage SPIFFS partition

```text
zyra-firmware/spiffs/
```

They are included in the repository and are flashed automatically with the ESP32 SPIFFS image.

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

# Ollama
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=llama3.2:3b-instruct-q4_0

# Server
HOST=0.0.0.0
PORT=8765

# Whisper
WHISPER_MODEL=small.en
WHISPER_DEVICE=cuda
WHISPER_COMPUTE=float16

# Home Assistant — Mode 1: Online HA Mode
HOME_ASSISTANT_URL=http://192.168.XXX.XXX:8123
HOME_ASSISTANT_TOKEN=paste_your_long_lived_access_token_here
HOME_ASSISTANT_TIMEOUT_SEC=5.0

# Existing Home Assistant MQTT switch entity IDs
HA_ENTITY_TV=switch.your_tv_entity
HA_ENTITY_SOUNDBAR=switch.your_soundbar_entity
HA_ENTITY_SUBWOOFER=switch.your_subwoofer_entity
HA_ENTITY_REAR=switch.your_rear_speakers_entity

# ESP8266 relay-board Home IP fallback — Mode 2: Online Relay Mode
RELAY_HOME_BASE_URLS=http://192.168.XXX.XXX
RELAY_HTTP_TIMEOUT_SEC=2.5
```

Use the IP address of your Smart Extension relay board for `RELAY_HOME_BASE_URLS`.

Multiple relay-board URLs can be given as a comma-separated list:

```env
RELAY_HOME_BASE_URLS=http://192.168.29.97,http://192.168.31.156
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
Kokoro TTS ready
Memory engine ready
Smart-home engine ready
All engines ready
Uvicorn running on http://0.0.0.0:8765
```

Fast server health check:

```text
http://localhost:8765/health
```

This endpoint should return quickly and must not depend on Home Assistant or the ESP8266 relay board.

Smart-home backend health/debug endpoint:

```text
http://localhost:8765/smart-home/health
```

This endpoint may report Home Assistant or relay availability and is intended for debugging.

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
✓ Kokoro TTS working
✓ Memory working
✓✓✓ All components working
```

Test the smart-home backend selection:

```powershell
python test_home_assistant.py
```

Expected when Home Assistant is available:

```text
preferred_mode: online_intelligent_ha
active_backend: home_assistant
```

Expected when Home Assistant is unavailable but the relay board is reachable:

```text
preferred_mode: online_intelligent_relay
active_backend: relay_home
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

Add your Wi-Fi, server, and relay details:

```c
#pragma once

// ── Home Wi-Fi + ZYRA server ─────────────────────
#define WIFI_SSID      "YOUR_WIFI_NAME"
#define WIFI_PASSWORD  "YOUR_WIFI_PASSWORD"

#define SERVER_IP      "YOUR_LAPTOP_IPV4"
#define SERVER_PORT    8765

// ── Relay fallback ───────────────────────────────
// ESP8266 smart extension direct AP.
#define RELAY_AP_SSID      "ESP-REMOTE-DIRECT"
#define RELAY_AP_PASSWORD  "12345678"

// ESP8266 home Wi-Fi IP.
// This is used when Zyra is still connected to home Wi-Fi.
#define RELAY_HOME_BASE_URL "http://192.168.29.97"

// ESP8266 direct AP IP.
// This is used only when home Wi-Fi itself fails.
#define RELAY_AP_BASE_URL   "http://192.168.4.1"
```

Do not commit `zyra_config.h`.

A safe template file is included as:

```text
zyra-firmware/main/zyra_config.example.h
```

---

## ESP-SR Model Setup

Wakeword and offline command recognition use ESP-SR models.

The firmware configuration uses:

```text
Jarvis WakeNet
English MultiNet
ESP-SR model partition
```

The partition table includes a dedicated model partition and a SPIFFS storage partition:

```text
model
storage
```

Make sure the ESP-SR model partition is generated and flashed during the firmware build process.

---

## SPIFFS Asset Setup

The firmware uses the `zyra-firmware/spiffs/` folder for offline WAV prompts.

The SPIFFS image is generated from:

```text
zyra-firmware/spiffs/
```

and flashed into the `storage` partition.

The component CMake configuration includes:

```cmake
spiffs_create_partition_image(storage ../spiffs FLASH_IN_PROJECT)
```

So a normal flash command should flash the SPIFFS image along with the firmware.

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
Wakeword engine ready
Status LED initialized
Say Jarvis
Connected to ZYRA server
ZYRA online
```

Expected online wakeword flow:

```text
Wakeword detected
Listening
Sending audio to server
Playing response
Returning to idle
```

Expected Online HA Mode behavior:

```text
ESP32-S3 connected to ZYRA server
ZYRA server uses Home Assistant backend
Home Assistant controls MQTT relay switches
```

Expected Online Relay Mode behavior when Home Assistant is unavailable:

```text
ESP32-S3 connected to ZYRA server
ZYRA server detects Home Assistant unavailable
ZYRA server uses ESP8266 relay home IP fallback
```

Expected serverless relay fallback output when the server is unavailable:

```text
Server connection failed
Entering serverless mode
Smart-home control ready
Offline speech ready
Offline voice recognizer started
Relay status synced
```

Expected restore output when the server returns:

```text
Checking if ZYRA server is back
Connected to ZYRA server
Server restored
Switching back to online mode
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

### Onboard RGB LED

| LED     | ESP32-S3 |
| ------- | -------- |
| RGB LED | GPIO 48  |

---

## Important Notes

Do not commit:

```text
zyra-server/.venv/
zyra-server/.env
zyra-server/memory/
zyra-firmware/build/
zyra-firmware/main/zyra_config.h
zyra-repomix-output.xml
```

Safe to commit:

```text
zyra-server/.env.example
zyra-firmware/main/zyra_config.example.h
zyra-firmware/sdkconfig
zyra-firmware/spiffs/
```

Only commit `zyra-firmware/spiffs/` when it contains required source WAV prompts for offline/serverless mode. Do not commit generated `.bin`, `.elf`, `.map`, or build output files.

The Smart Extension relay-board firmware should be committed in its own separate repository.

---

## Troubleshooting

### Wakeword is not detected

Check:

* ESP-SR dependency is enabled in `idf_component.yml`
* `sdkconfig` has Jarvis WakeNet enabled
* ESP-SR model partition is flashed
* Microphone pins match `audio_pipeline.c`
* INMP441 is powered from 3.3V
* Serial monitor shows the wakeword engine initialized
* Room noise is not too high
* Speak clearly near the microphone

### Build fails after enabling wakeword

Check:

* `esp-sr` is listed in `idf_component.yml`
* `esp-sr` is listed under `REQUIRES` in `main/CMakeLists.txt`
* `wakeword_engine.c` is included under `SRCS`
* ESP-IDF Component Manager has downloaded ESP-SR
* Run `idf.py reconfigure` after dependency changes

### RGB LED is not working

Check:

* Your ESP32-S3 board has an onboard addressable RGB LED
* The RGB LED GPIO is GPIO 48
* `led_strip` dependency is available
* `status_led.c` is included in the firmware build
* The LED is not disabled by board hardware configuration

### ESP32 connects to Wi-Fi but not server

Check:

* Server is running
* ESP32-S3 and laptop are on the same Wi-Fi
* `SERVER_IP` is the laptop IPv4 address
* Firewall allows port `8765`
* WebSocket path is `/zyra`
* `http://SERVER_IP:8765/health` returns quickly with HTTP 200

### Smart-home command is understood but device does not respond

Check:

* Home Assistant is running if using Online HA Mode
* Home Assistant long-lived access token is valid
* `.env` has correct `HA_ENTITY_TV`, `HA_ENTITY_SOUNDBAR`, `HA_ENTITY_SUBWOOFER`, and `HA_ENTITY_REAR`
* Home Assistant MQTT switches are controlling the ESP8266 relay board correctly
* If Home Assistant is unavailable, the ESP8266 relay board home IP is reachable
* `RELAY_HOME_BASE_URLS` points to the relay board IP
* Browser can open `http://RELAY_IP/ping`
* Browser can open `http://RELAY_IP/status`

### Offline relay mode is not working

Check:

* `RELAY_HOME_BASE_URL` in `zyra_config.h` includes `http://`
* ESP8266 relay board is powered
* ESP8266 relay board is connected to home Wi-Fi
* ESP32-S3 and ESP8266 are on the same network
* `/ping` returns `pong`
* `/status` returns four comma-separated values
* `smart_home_control.c` is included in the firmware build
* `main.c` includes `smart_home_control.h`

### Offline voice command is not working

Check:

* ESP-SR MultiNet English model is enabled
* Model partition is flashed
* Serial monitor shows offline voice command registration
* Wakeword is detected before the offline command
* Speak one of the supported fixed offline command phrases
* Offline voice is expected to handle only limited relay commands

### Offline speech prompt is not playing

Check:

* `zyra-firmware/spiffs/` contains the WAV files
* SPIFFS partition is flashed
* `offline_speech_init()` succeeds
* WAV files are mono 16-bit PCM
* MAX98357A wiring matches `audio_pipeline.c`

### Capture buffer allocation failed

Check:

* ESP32-S3 board has PSRAM
* PSRAM is enabled in `sdkconfig`
* Board target is set to `esp32s3`
* Use `idf.py fullclean` after sdkconfig or partition changes

### OLED I2C timeout

Check:

* OLED SDA/SCL pins match `display.c`
* OLED address is `0x3C`
* OLED is powered from 3.3V
* GND is common with ESP32-S3
* Wires are short and stable
* Avoid loose breadboard/jumper connections
* Add external pull-up resistors if the OLED is unstable under full firmware load

---

## Current Status

Completed:

* PC-side AI server
* Fast server-only `/health` endpoint for firmware liveness checks
* Separate smart-home backend health/debug endpoint
* Faster-Whisper STT
* Ollama LLM
* LLM warmup for lower first-request delay
* Kokoro TTS with `af_heart` voice
* Memory engine
* Intent routing
* Smart-home command understanding
* Home Assistant backend for Online HA Mode
* ESP8266 relay home-IP fallback backend for Online Relay Mode
* Server-side backend priority: Home Assistant first, relay fallback second
* ESP32-S3 Wi-Fi connection
* ESP32-S3 WebSocket connection
* Jarvis wakeword support using ESP-SR WakeNet
* ESP-SR MultiNet offline command recognizer
* WebSocket disconnect callback support
* Runtime fallback trigger when server connection is lost
* Runtime guard to avoid fallback during an active online request
* Runtime guard to avoid online and serverless voice loops running together
* Serverless Relay Fallback using firmware-side `smart_home_control`
* Automatic restore from serverless relay fallback back to online mode
* I2S mic initialization
* I2S amplifier initialization
* PSRAM audio buffer allocation
* Firmware-side smart-home relay status sync
* Firmware-side offline speech prompt playback
* SPIFFS asset support for offline/serverless mode
* OLED states for wakeword, online, serverless relay, speaking, success, and error states
* RGB status LED feedback for online, serverless, offline, speaking, success, error, and connection failure states

Not included yet:

* Full offline AI conversation without the ZYRA server
* Full natural conversation directly on the ESP32-S3
* Firmware-side Serverless HA Mode using ESP32-S3 → Home Assistant REST without the ZYRA server
* Fully finalized standalone `runtime_mode.c` state-machine module
* Final UI appearance upgrade
* Smart Extension relay-board firmware inside this repository
