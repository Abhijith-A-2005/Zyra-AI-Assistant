# ZYRA

ZYRA is a local smart home voice assistant built using an **ESP32-S3** and a **Python AI server**.

The ESP32-S3 captures voice using an INMP441 microphone, sends the audio to the PC server through WebSocket, understands speech using Faster-Whisper, generates intelligent responses through Ollama, converts replies into speech using Piper TTS, and plays them back through a MAX98357 I2S amplifier.

The goal of ZYRA is to become a private Jarvis-style home assistant that can talk naturally, control smart home devices, remember useful context, show system states on an OLED display, and act as a real voice interface for a smart home such as turning devices on or off without depending on cloud assistants.

The actual relay board firmware is maintained in a separate Smart-Switch-Board repository, while this repository contains the ZYRA assistant brain and ESP32-S3 voice interface.


## Project Structure

```text
ZYRA/
├── README.md
├── .gitignore
├── zyra-server/
│   ├── .env.example
│   ├── audio_utils.py
│   ├── config.py
│   ├── llm.py
│   ├── main.py
│   ├── memory.py
│   ├── requirements.txt
│   ├── smart_home.py
│   ├── stt.py
│   ├── test_components.py
│   ├── test_smart_home.py
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
* Smart-home command recognition
* Local relay-board HTTP control
* WebSocket communication with the ESP32-S3

Important files:

| File                 | Purpose                                                    |
| -------------------- | ---------------------------------------------------------- |
| `main.py`            | Starts the FastAPI WebSocket server                        |
| `config.py`          | Server, model, audio, memory, and smart-home configuration |
| `stt.py`             | Speech-to-text engine                                      |
| `llm.py`             | Ollama LLM engine                                          |
| `tts.py`             | Piper text-to-speech engine                                |
| `memory.py`          | ChromaDB and SQLite memory handling                        |
| `smart_home.py`      | Smart-home command handling and relay-board HTTP control   |
| `test_components.py` | Tests Ollama, Whisper, Piper, and memory                   |
| `test_smart_home.py` | Tests smart-home command handling                          |
| `test_websocket.py`  | Tests the WebSocket pipeline                               |
| `.env.example`       | Example environment configuration                          |
| `requirements.txt`   | Python dependencies                                        |

---

### `zyra-firmware/`

The ESP32-S3 firmware.

It handles:

* Wi-Fi connection
* WebSocket connection to the ZYRA server
* INMP441 microphone input
* MAX98357 speaker output
* OLED display states
* Audio capture and playback
* PSRAM audio buffer allocation

Important files:

| File                         | Purpose                               |
| ---------------------------- | ------------------------------------- |
| `main/main.c`                | Main firmware logic                   |
| `main/audio_pipeline.c`      | I2S microphone and speaker pipeline   |
| `main/audio_pipeline.h`      | Audio pipeline header                 |
| `main/websocket_client.c`    | ESP32 WebSocket client                |
| `main/websocket_client.h`    | WebSocket client header               |
| `main/display.c`             | OLED display handling                 |
| `main/display.h`             | Display state header                  |
| `main/zyra_config.example.h` | Safe example Wi-Fi/server config      |
| `partitions.csv`             | ESP32 flash partition layout          |
| `sdkconfig`                  | Working ESP-IDF project configuration |

---

## Smart Home Integration

ZYRA can understand smart-home commands and directly control configured devices through the local network.

Supported device categories can include:

* TV
* Soundbar
* Subwoofer
* Rear speakers
* Full home-theater system

Example commands:

```text
Turn on my TV
Switch off the soundbar
Turn on the rear speakers
Power off the home theatre
Turn everything off
```

The relay board itself is handled by a separate Smart-Switch-Board project. This ZYRA repository only contains the assistant-side command intelligence and communication logic.

Typical flow:

```text
User voice
→ ESP32-S3 microphone
→ ZYRA server
→ Speech-to-text
→ Smart-home command detection
→ Local HTTP request to relay board
→ Voice confirmation from ZYRA
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

Create virtual environment:

```powershell
py -3.11 -m venv .venv
```

Activate it:

```powershell
.\.venv\Scripts\Activate.ps1
```

Install PyTorch CUDA:

```powershell
python -m pip install torch==2.5.1 torchvision==0.20.1 torchaudio==2.5.1 --index-url https://download.pytorch.org/whl/cu121
```

Install dependencies:

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

SMART_HOME_BASE_URL=http://192.168.29.97
SMART_HOME_TIMEOUT=2.0
```

Use the IP address of your Smart Extension relay board for `SMART_HOME_BASE_URL`.

---

## Run the Server

Start Ollama first:

```powershell
ollama run llama3.2:3b-instruct-q4_0
```

Then run the server:

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

Test smart-home command handling:

```powershell
python test_smart_home.py
```

Test WebSocket pipeline:

```powershell
python test_websocket.py --text "Turn on my TV"
```

---

## Firmware Setup

Go to firmware folder:

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

Add your Wi-Fi and server details:

```c
#pragma once

#define WIFI_SSID      "YOUR_WIFI_NAME"
#define WIFI_PASSWORD  "YOUR_WIFI_PASSWORD"
#define SERVER_IP      "YOUR_LAPTOP_IPV4"
#define SERVER_PORT    8765
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

Expected firmware output:

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
| SDA  | GPIO 11  |
| SCL  | GPIO 12  |
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

The Smart Extension relay firmware should be committed in its own separate repository.

---

## Troubleshooting

### ESP32 connects to Wi-Fi but not server

Check:

* Server is running
* ESP32 and laptop are on the same Wi-Fi
* `SERVER_IP` is the laptop IPv4 address
* Firewall allows port `8765`

### Smart-home command is understood but device does not respond

Check:

* Smart Extension relay board is powered
* Relay board and ZYRA server are on the same network
* `SMART_HOME_BASE_URL` points to the relay board IP
* Relay board HTTP endpoints are working
* Browser can open the relay board status page

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

Check OLED pins:

```c
#define OLED_SDA 11
#define OLED_SCL 12
```

---

## Current Status

Completed:

* PC-side AI server
* Faster-Whisper STT
* Ollama LLM
* Piper TTS
* Memory engine
* ESP32-S3 Wi-Fi connection
* ESP32-S3 WebSocket connection
* I2S mic initialization
* I2S amplifier initialization
* PSRAM audio buffer allocation
* Smart-home command recognition
* Local relay-board command forwarding

Next:

* End-to-end smart-home voice testing
* OLED display stabilization
* Offline fallback command handling
* Wake word refinement
* Separate Smart Extension repository cleanup
