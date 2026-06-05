# ZYRA

ZYRA is a local smart home voice assistant built using an **ESP32-S3** and a **Python AI server**.

The ESP32-S3 captures voice using an INMP441 microphone, sends the audio to the PC server through WebSocket, gets an AI-generated voice response, and plays it through a MAX98357 I2S amplifier.

---

## Project Structure

```text
ZYRA/
├── README.md
├── .gitignore
├── zyra-server/
└── zyra-firmware/
```

### `zyra-server`

Python backend that handles:

* Speech-to-text using Faster-Whisper
* AI response generation using Ollama
* Text-to-speech using Piper
* Memory using ChromaDB and SQLite
* WebSocket communication with ESP32-S3

### `zyra-firmware`

ESP32-S3 firmware that handles:

* Wi-Fi connection
* WebSocket connection to server
* INMP441 microphone input
* MAX98357 speaker output
* OLED display state
* Audio capture and playback

---

## Hardware Used

* ESP32-S3 N16R8
* INMP441 I2S microphone
* MAX98357A I2S amplifier
* 0.96 inch SSD1306 OLED display
* Speaker
* PC or laptop for running the AI server

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

A safe template file should be committed as:

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
```

---

## Troubleshooting

### ESP32 connects to Wi-Fi but not server

Check:

* Server is running
* ESP32 and laptop are on the same Wi-Fi
* `SERVER_IP` is the laptop IPv4 address
* Firewall allows port `8765`

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

Next:

* End-to-end voice testing
* OLED display stabilization
* Smart home relay command integration
* Wake word refinement
