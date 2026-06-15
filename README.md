# ZYRA

ZYRA is a custom local smart home voice assistant built using an **ESP32-S3** and a **Python AI server**.

The ESP32-S3 captures voice using an INMP441 microphone, sends the audio to the local AI server through WebSocket, understands speech using Faster-Whisper, generates intelligent responses through Ollama, converts replies into speech using Kokoro TTS with the af_heart voice, and plays them back through a MAX98357 I2S amplifier.

The goal of ZYRA is to become a private Jarvis-style home assistant that can talk naturally, remember useful context, show system states on an OLED display, provide RGB LED feedback, wake through a local hardware wakeword, and intelligently understand smart-home commands such as turning devices on, turning devices off, checking device status, and controlling grouped home-theater devices without depending on cloud assistants.

The physical relay board firmware is maintained in a separate Smart-Switch-Board repository, while this repository contains the ZYRA assistant brain, smart-home intelligence layer, ESP32-S3 voice interface, local wakeword firmware, offline voice command layer, SPIFFS prompt assets, RGB status LED feedback, and offline relay connectivity support.

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
│   
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
        ├── offline_relay.c
        ├── offline_relay.h
        ├── offline_speech.c
        ├── offline_speech.h
        ├── offline_voice.c
        ├── offline_voice.h
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
| `stt.py`             | Speech-to-text engine using Faster-Whisper with hallucination filtering            |
| `llm.py`             | Ollama LLM engine with warmup and persistent model loading                         |
| `tts.py`             | Kokoro text-to-speech engine                                                        |
| `memory.py`          | ChromaDB and SQLite memory handling                                                |
| `test_components.py` | Tests Ollama, Whisper, Kokoro, and memory                                           |
| `test_tts_api.py`    | Tests Kokoro TTS behavior                                                           |
| `test_websocket.py`  | Tests the WebSocket pipeline                                                       |
| `.env.example`       | Example environment configuration                                                  |
| `requirements.txt`   | Python dependencies                                                                |

---

### `zyra-firmware/`

The ESP32-S3 firmware.

It handles:

* Wi-Fi connection
* Local Jarvis wakeword detection using ESP-SR WakeNet
* WebSocket connection to the ZYRA server
* Runtime detection of server disconnects
* Automatic fallback to offline relay mode
* Offline voice command recognition using ESP-SR MultiNet
* Offline speech prompt playback from SPIFFS
* Offline relay-board HTTP communication
* RGB status LED feedback
* INMP441 microphone input
* MAX98357 speaker output
* OLED display states
* Audio capture and playback
* PSRAM audio buffer allocation

Important files:

| File                         | Purpose                                                                                                  |
| ---------------------------- | -------------------------------------------------------------------------------------------------------- |
| `main/main.c`                | Main ESP32-S3 firmware logic, online pipeline, Wi-Fi handling, wakeword flow, and offline fallback entry |
| `main/audio_pipeline.c`      | I2S microphone and speaker pipeline                                                                      |
| `main/audio_pipeline.h`      | Audio pipeline header                                                                                    |
| `main/wakeword_engine.c`     | ESP-SR WakeNet wakeword engine for Jarvis detection                                                      |
| `main/wakeword_engine.h`     | Wakeword engine API                                                                                      |
| `main/offline_voice.c`       | ESP-SR MultiNet offline command recognizer                                                               |
| `main/offline_voice.h`       | Offline voice command definitions and recognizer API                                                     |
| `main/offline_speech.c`      | SPIFFS WAV prompt loader and offline prompt playback logic                                               |
| `main/offline_speech.h`      | Offline speech prompt API                                                                                |
| `main/offline_relay.c`       | Offline relay HTTP client, relay status sync, toggle, and group control logic                            |
| `main/offline_relay.h`       | Offline relay device/action definitions and API                                                          |
| `main/status_led.c`          | Onboard RGB LED status engine                                                                            |
| `main/status_led.h`          | Status LED mode and state definitions                                                                    |
| `main/websocket_client.c`    | ESP32 WebSocket client with disconnect callback and stop support                                         |
| `main/websocket_client.h`    | WebSocket client header                                                                                  |
| `main/display.c`             | OLED display handling, including wakeword, offline, relay, and speaking states                           |
| `main/display.h`             | Display state definitions                                                                                |
| `main/zyra_config.example.h` | Safe example Wi-Fi, server, and offline relay configuration                                              |
| `spiffs/`                    | Offline WAV prompts used by the firmware                                                                 |
| `partitions.csv`             | ESP32 flash partition layout for app, ESP-SR model partition, and SPIFFS storage                         |
| `sdkconfig`                  | Working ESP-IDF project configuration                                                                    |

---

## Runtime Refinements

This version includes refinements to make ZYRA feel more stable and appliance-like during real use.

Current refinements include:

* Improved wakeword flow
* Improved offline voice listening flow
* Better offline relay fallback behavior
* RGB LED mode/state feedback
* OLED state feedback for online, serverless, offline, listening, thinking, speaking, and error states
* SPIFFS-based prompt playback for offline relay confirmations
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
| Serverless | Purple         | Server path is unavailable but local fallback behavior is active |
| Offline    | Amber / Orange | Offline relay mode is active                                     |

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
Jarvis, which all devices are on?
```

Typical online smart-home flow:

```text
Wakeword
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

This mode is designed for situations where the ESP32-S3 cannot reach the ZYRA Python server or the WebSocket connection drops during runtime. When that happens, the firmware can stop the WebSocket client and continue local relay control through the relay board.

Offline relay connectivity focuses on:

* Detecting WebSocket/server loss during online mode
* Stopping the WebSocket client cleanly
* Using relay-board HTTP endpoints directly
* Switching to ESP-REMOTE-DIRECT AP when home Wi-Fi is unavailable
* Using relay status from `/status`
* Tracking relay states for TV, soundbar, subwoofer, and rear speakers
* Showing offline and relay status on the OLED display
* Updating RGB LED mode/state feedback

Important note:

```text
Offline relay connectivity does not mean the full AI assistant runs only on the ESP32-S3.

The ZYRA Python server is still required for full speech-to-text, AI response generation, memory, and text-to-speech.

The offline relay firmware module provides local relay-board connectivity and relay-control functions when the server path is unavailable.
```

---

## Offline Voice Commands

ZYRA includes firmware-side offline voice command recognition.

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

The firmware uses SPIFFS WAV prompts for offline confirmations.

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

The firmware also includes pre-generated status prompts:

```text
status_0000.wav
status_0001.wav
...
status_1111.wav
```

These represent all possible relay status combinations for:

```text
TV, Soundbar, Subwoofer, Rear speakers
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
* Onboard RGB LED on ESP32-S3
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
```

---

## Kokoro TTS Setup

Online TTS:
- Generated live by Kokoro in zyra-server/tts.py.

Offline TTS:
- Fixed WAV prompts stored in zyra-firmware/spiffs.
- These files are flashed into the ESP32-S3 storage SPIFFS partition
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
✓ Kokoro TTS working
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

Expected wakeword flow:

```text
Wakeword detected
Listening
Sending audio to server
Playing response
Returning to idle
```

Expected offline fallback output when the server is unavailable:

```text
Server connection failed
Entering offline relay mode
Offline speech ready
Offline voice recognizer started
Offline relay status synced
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

Only commit `zyra-firmware/spiffs/` when it contains required source WAV prompts for offline mode. Do not commit generated `.bin`, `.elf`, `.map`, or build output files.

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
* `RELAY_HOME_BASE_URL` and `RELAY_AP_BASE_URL` are correct
* ESP8266 direct AP IP is reachable
* `/status` works at the relay base URL
* Router/client isolation is not involved when using normal LAN mode

### Offline voice command is not working

Check:

* ESP-SR MultiNet English model is enabled
* `offline_voice.c` is included in the firmware build
* Microphone input level is high enough
* Command phrases are registered in serial monitor
* Probability threshold is not too strict
* Offline relay mode is active

### Offline speech prompt is not playing

Check:

* `offline_speech.c` is included in the firmware build
* SPIFFS is mounted successfully
* Required `.wav` files exist inside `zyra-firmware/spiffs/`
* WAV files are mono 16-bit PCM
* SPIFFS image is flashed into the `storage` partition
* MAX98357 pins match `audio_pipeline.c`


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
* LLM warmup for lower first-request delay
* Kokoro TTS
* Jarvis-style Kokoro voice configuration
* Memory engine
* Intent routing
* Smart-home command understanding
* Local relay-board command forwarding from the server
* ESP32-S3 Wi-Fi connection
* ESP32-S3 WebSocket connection
* Jarvis wakeword support using ESP-SR WakeNet
* ESP-SR MultiNet offline command recognizer
* WebSocket disconnect callback support
* Runtime fallback trigger when server connection is lost
* I2S mic initialization
* I2S amplifier initialization
* PSRAM audio buffer allocation
* Firmware-side offline relay connectivity
* Firmware-side offline relay status sync
* Firmware-side offline speech prompt playback
* SPIFFS asset support for offline mode
* OLED states for wakeword and offline relay mode
* RGB status LED feedback for online, serverless, offline, speaking, success, error, and connection failure states

Not included yet:

* Full offline AI conversation without the ZYRA server
* Full natural conversation directly on the ESP32-S3
* Final UI appearance upgrade
* Smart Extension relay-board firmware inside this repository