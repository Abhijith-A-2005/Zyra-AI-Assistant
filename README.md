# ZYRA

ZYRA is a custom local smart-home voice assistant built using an **ESP32-S3** and a **Python AI server**.

The ESP32-S3 captures voice using an INMP441 microphone, sends the audio to the local AI server through WebSocket, understands speech using Faster-Whisper, generates intelligent responses through Ollama, converts replies into speech using Kokoro TTS with the `af_heart` voice, and plays them back through a MAX98357 I2S amplifier.

The goal of ZYRA is to become a private Jarvis-style home assistant that can talk naturally, remember useful context, show system states on an OLED display, provide RGB LED feedback, wake through a local hardware wakeword, and intelligently control smart-home devices without depending on cloud assistants.

ZYRA now includes Smart-device control which can control volume levels, media playback, change TV and soundbar inputs, TV app launching, change soundbar sound modes, and tell detailed device status. This allows commands such as changing the TV to HDMI 1, opening Netflix, returning to the Google TV home screen, setting the LG Soundbar to Cinema mode, checking home-theater status, or controlling grouped devices through natural language.

The physical relay-board firmware is maintained in a separate Smart-Switch-Board repository, while this repository contains the ZYRA assistant brain, smart-home intelligence layer, ESP32-S3 voice interface, local wakeword firmware, offline voice command layer, SPIFFS prompt assets, RGB status LED feedback, Home Assistant integration, direct relay fallback, serverless Home Assistant control, serverless relay control, and emergency offline relay fallback support.

---

## Project Structure

```text
ZYRA/
├── README.md
├── .gitignore
│
├── zyra-server/
│   ├── .env.example
│   ├── config.py
│   ├── main.py
│   ├── stt.py
│   ├── llm.py
│   ├── tts.py
│   ├── memory.py
│   ├── device_registry.json
│   ├── device_registry.py
│   ├── registry_intent_router.py
│   ├── ha_service_mapper.py
│   ├── smart_home.py
│   ├── smart_home_backends.py
│   ├── home_assistant_client.py
│   ├── relay_http_client.py
│   ├── requirements.txt
│   ├── test_components.py
│   ├── test_device_registry.py
│   ├── test_registry_intent_router.py
│   ├── test_registry_live.py
│   ├── test_home_assistant.py
│   ├── test_tts_api.py
│   └── test_websocket.py
│
└── zyra-firmware/
    ├── CMakeLists.txt
    ├── partitions.csv
    ├── sdkconfig
    ├── spiffs/
    │   ├── done.wav
    │   ├── failed.wav
    │   ├── status.wav
    │   ├── tv_on.wav
    │   ├── tv_off.wav
    │   ├── soundbar_on.wav
    │   ├── soundbar_off.wav
    │   ├── subwoofer_on.wav
    │   ├── subwoofer_off.wav
    │   ├── rear_on.wav
    │   ├── rear_off.wav
    │   ├── sound_system_on.wav
    │   ├── sound_system_off.wav
    │   ├── all_speakers_on.wav
    │   ├── all_speakers_off.wav
    │   ├── home_theater_on.wav
    │   ├── home_theater_off.wav
    │   └── status_0000.wav ... status_1111.wav
    │
    └── main/
        ├── CMakeLists.txt
        ├── idf_component.yml
        ├── main.c
        ├── audio_pipeline.c
        ├── audio_pipeline.h
        ├── audio_streamer.c
        ├── audio_streamer.h
        ├── websocket_client.c
        ├── websocket_client.h
        ├── display.c
        ├── display.h
        ├── status_led.c
        ├── status_led.h
        ├── wakeword_engine.c
        ├── wakeword_engine.h
        ├── offline_voice.c
        ├── offline_voice.h
        ├── offline_speech.c
        ├── offline_speech.h
        ├── smart_home_control.c
        ├── smart_home_control.h
        └── zyra_config.example.h
```

---

## Folder Overview

### `zyra-server/`

The Python server runs the full AI pipeline.

It handles:

* WebSocket server for ESP32-S3 communication
* Faster-Whisper speech-to-text
* Ollama LLM conversation
* Kokoro TTS using the `af_heart` voice
* Streamed PCM audio delivery to the ESP32-S3
* Memory using ChromaDB and SQLite
* Capability-aware smart-home device registry
* Semantic registry intent routing
* Home Assistant service mapping
* Detailed smart-device control through Home Assistant
* Relay/mains power control through Home Assistant switch entities
* Direct ESP8266 relay-board HTTP fallback
* Smart device status reporting
* Health endpoint for runtime mode decisions
* Smart-home backend health/debug checks
* WebSocket test client and component test scripts

Important files:

| File                        | Purpose                                                                                           |
| --------------------------- | ------------------------------------------------------------------------------------------------- |
| `main.py`                   | Main FastAPI/WebSocket server, AI pipeline, streamed TTS, smart-home routing, and response handling |
| `stt.py`                    | Faster-Whisper speech recognition and hallucination filtering                                     |
| `llm.py`                    | Ollama chat engine                                                                                |
| `tts.py`                    | Kokoro TTS engine using `af_heart`                                                                |
| `device_registry.json`      | Capability-aware smart-home registry containing devices, aliases, groups, surfaces, sources, and sound modes |
| `device_registry.py`        | Loads the device registry and resolves spoken targets/capabilities to the correct control surface |
| `registry_intent_router.py` | Semantic smart-home router that converts natural language into registry-safe commands             |
| `ha_service_mapper.py`      | Converts registry commands into Home Assistant service calls                                      |
| `smart_home.py`             | Smart-home command execution, Home Assistant control, relay fallback, status reporting, and response generation |
| `smart_home_backends.py`    | Backend result models for Home Assistant and relay fallback                                       |
| `home_assistant_client.py`  | Home Assistant REST client, entity state reading, service calls, and verification                 |
| `relay_http_client.py`      | ESP8266 relay-board HTTP client                                                                   |
| `memory.py`                 | ChromaDB and SQLite memory handling                                                               |
| `test_components.py`        | Tests Ollama, Whisper, Kokoro TTS, and memory                                                     |
| `test_device_registry.py`   | Tests registry target resolution, capability mapping, and Home Assistant service mapping          |
| `test_registry_intent_router.py` | Tests semantic smart-home routing and verifies registry-safe command output                 |
| `test_registry_live.py`     | Runs safe live registry control tests against Home Assistant                                      |
| `test_home_assistant.py`    | Tests Home Assistant and relay fallback backend availability                                      |
| `test_tts_api.py`           | Generates a Kokoro TTS test WAV                                                                   |
| `test_websocket.py`         | Simulates the ESP32-S3 WebSocket client from the PC                                               |

Current server-side smart-home modes:

```text
Mode 1 — Online Intelligent HA Mode
ESP32-S3 → ZYRA Server → Registry Router → Device Registry → Home Assistant → MQTT / smart-device integrations

Mode 2 — Online Intelligent Relay Mode
ESP32-S3 → ZYRA Server → Registry Router → ESP8266 relay board home IP
```

Mode 1 is preferred when Home Assistant is available.

Mode 2 is used automatically when the ZYRA server is online but Home Assistant is unavailable.

The server `/health` endpoint is intentionally lightweight. It only confirms that the ZYRA server is alive. Smart-home backend health is handled separately so a relay-board or Home Assistant failure does not incorrectly make the ESP32-S3 think the full server is offline.

### `zyra-firmware/`

The ESP32-S3 firmware for the physical ZYRA assistant device.

It handles:

* Wi-Fi connection
* WebSocket connection to the ZYRA server
* INMP441 microphone input through I2S
* MAX98357A speaker output through I2S
* Streamed PCM audio playback using an internal audio queue
* Wakeword detection using ESP-SR WakeNet
* Limited serverless voice commands using ESP-SR MultiNet
* OLED display feedback
* RGB status LED feedback
* Firmware-side Home Assistant REST control
* Firmware-side ESP8266 relay-board HTTP control
* Serverless fallback when the server path is unavailable
* Emergency relay AP fallback when home Wi-Fi or home relay path is unavailable
* SPIFFS WAV prompts for serverless/offline confirmations

Important files:

| File                    | Purpose                                                                            |
| ----------------------- | ---------------------------------------------------------------------------------- |
| `main.c`                | Main firmware runtime flow, Wi-Fi, online/serverless switching, and UI integration |
| `audio_pipeline.c`      | I2S microphone input and I2S speaker output                                        |
| `audio_streamer.c`      | Queued streamed PCM playback for online TTS audio                                  |
| `websocket_client.c`    | WebSocket client used to communicate with the ZYRA Python server                   |
| `display.c`             | SSD1306 OLED display driver and UI states                                          |
| `smart_home_control.c`  | Firmware-side Home Assistant and relay HTTP control module                         |
| `offline_voice.c`       | ESP-SR MultiNet limited local command recognizer                                   |
| `offline_speech.c`      | SPIFFS WAV prompt playback for serverless/offline confirmations                    |
| `wakeword_engine.c`     | ESP-SR WakeNet wakeword engine                                                     |
| `status_led.c`          | Onboard RGB LED status feedback                                                    |
| `zyra_config.example.h` | Safe template for private Wi-Fi, server, Home Assistant, and relay settings        |

Current firmware-side smart-home modes:

```text
Mode 3 — Serverless HA Mode
ESP32-S3 firmware → Home Assistant REST API → Home Assistant / MQTT → smart devices

Mode 4 — Serverless Wi-Fi Relay Mode
ESP32-S3 firmware → ESP8266 relay board home IP

Mode 5 — Emergency Offline Relay AP Mode
ESP32-S3 firmware → ESP8266 direct AP Wi-Fi → ESP8266 relay board AP IP
```

Mode 3 is used when the Python server is unavailable but Home Assistant is reachable.

Mode 4 is used when the Python server and Home Assistant are unavailable but the ESP8266 relay board is still reachable through the home Wi-Fi network.

Mode 5 is used only when home Wi-Fi or the home relay path is unavailable and the ESP32-S3 must connect directly to the ESP8266 relay-board AP.

---

## Runtime Refinements

This version includes refinements to make ZYRA feel more stable and appliance-like during real use.

Current refinements include:

* Complete 5-mode smart-home runtime architecture
* Capability-aware server-side device registry
* Semantic registry router for natural smart-home commands
* Multi-surface Home Assistant control for TV and soundbar
* Detailed Home Assistant status reporting for TV and soundbar
* Online Intelligent HA Mode using Home Assistant as the preferred backend
* Online Intelligent Relay Mode when Home Assistant is unavailable but the ZYRA server is online
* Serverless HA Mode when the ZYRA server is unavailable but Home Assistant is reachable
* Serverless Wi-Fi Relay Mode when the server and Home Assistant are unavailable but the relay board is reachable on home Wi-Fi
* Emergency Offline Relay AP Mode when home network paths are unavailable
* Firmware-side Home Assistant REST support
* Firmware-side relay control using the ESP8266 home IP and direct AP IP
* Automatic server restore monitoring
* Automatic Home Assistant restore monitoring while in serverless relay mode
* Automatic switch from Serverless HA Mode to Serverless Relay Mode if Home Assistant goes down
* Fast `/health` endpoint that checks only whether the ZYRA server is alive
* RGB LED mode/state feedback
* OLED state feedback for online, serverless, offline, listening, thinking, speaking, and error states
* SPIFFS-based prompt playback for serverless/offline relay confirmations
* Kokoro TTS using the `af_heart` voice
* Streamed online TTS playback using an ESP32-S3 audio queue
* Stable WebSocket transport using `wsproto`
* Better STT rejection for silence, low-level noise, and common Whisper hallucinations
* LLM warmup to reduce first-response cold-start delay

---

## RGB Status LED Feedback

ZYRA uses the ESP32-S3 onboard RGB LED for quick visual feedback.

Mode colors:

| Mode       | LED Color      | Meaning                                                               |
| ---------- | -------------- | --------------------------------------------------------------------- |
| Online     | Blue           | ESP32-S3 is connected to the ZYRA server                              |
| Serverless | Purple         | Server path is unavailable but Home Assistant or home relay is active |
| Offline    | Amber / Orange | Emergency direct AP relay mode is active                              |

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

ZYRA uses ESP-SR WakeNet from the ESP-SR model partition.

Current wakeword:

```text
Jarvis
```

Wakeword flow:

```text
Idle
↓
WakeNet waits for "Jarvis"
↓
OLED and LED show listening state
↓
Speech is captured from the INMP441 microphone
↓
Audio is sent to the selected runtime path
```

Online mode:

```text
Jarvis
↓
ESP32-S3 captures speech
↓
Audio sent to Python ZYRA server
↓
Whisper STT
↓
Smart-home router or LLM
↓
Kokoro TTS
↓
Streamed PCM sent back to ESP32-S3
↓
Speaker plays response
```

Serverless/offline mode:

```text
Jarvis
↓
ESP32-S3 captures limited command audio
↓
ESP-SR MultiNet detects supported relay command
↓
Firmware executes Home Assistant REST or relay HTTP action
↓
SPIFFS WAV prompt confirms result
```

---

## Smart Home Intelligence

ZYRA can understand smart-home commands in natural language using a capability-aware server-side device registry.

The current model is not:

```text
one device = one Home Assistant entity
```

Instead, ZYRA uses:

```text
Physical device
↓
Multiple control surfaces
↓
Each surface has different capabilities
```

This allows the same spoken device to use different Home Assistant entities depending on the requested action.

Example:

```text
Increase TV volume
→ living_room_tv + volume + volume_up
→ media_player.tv_remote

Change TV to HDMI 1
→ living_room_tv + source + select_source
→ media_player.living_room_tv

Open Netflix on TV
→ living_room_tv + app + launch_app
→ remote.living_room_tv

Go to TV home
→ living_room_tv + app + go_home
→ remote.living_room_tv
→ remote.send_command HOME

Close Netflix
→ living_room_tv + app + go_home
→ remote.living_room_tv
→ remote.send_command HOME

Turn on TV
→ living_room_tv + power + on
→ switch.sony_tv

Wake TV
→ living_room_tv + sleep_wake + wake
→ media_player.living_room_tv
```

### Registry Devices

| Physical device | Registry ID | Type |
| --------------- | ----------- | ---- |
| Sony Bravia 2 MK2 | `living_room_tv` | TV |
| LG S95TR Soundbar | `lg_s95tr_soundbar` | Soundbar |
| Subwoofer | `subwoofer` | Relay switch |
| Rear Speakers | `rear_speakers` | Relay switch |

### Sony Bravia TV Control Surfaces

| Capability | Home Assistant entity |
| ---------- | --------------------- |
| Sleep / wake | `media_player.living_room_tv` |
| Playback | `media_player.living_room_tv` |
| Source / input select | `media_player.living_room_tv` |
| Volume | `media_player.tv_remote` |
| Mute / unmute | `switch.living_room_tv_mute` or supported media surface |
| Streaming / cast media | `media_player.google_tv_cast` |
| App launch | `remote.living_room_tv` |
| Home screen / app exit | `remote.living_room_tv` |
| Relay mains power | `switch.sony_tv` |

Supported TV sources include:

```text
TV
HDMI 1
HDMI 2
Audio system
HDMI 4
AirPlay
HDMICEC 1
HDMICEC 2
HDMICEC 3
HDMICEC 4
HDMICEC 5
HDMICEC 6
HDMICEC 7
HDMICEC 8
HDMICEC 9
HDMICEC 10
HDMICEC 11
HDMICEC 12
Satellite
```

### LG S95TR Soundbar Control Surfaces

| Capability | Home Assistant entity |
| ---------- | --------------------- |
| Sleep / wake | `media_player.lg_soundbar` |
| Playback | `media_player.lg_soundbar` |
| Volume | `media_player.lg_soundbar` |
| Mute / unmute | `media_player.lg_soundbar` |
| Source / input select | `media_player.lg_soundbar` |
| Sound mode select | `media_player.lg_soundbar` |
| Cast playback / streaming | `media_player.lg_speaker_cast` |
| Relay mains power | `switch.soundbar` |

Supported soundbar sources include:

```text
Bluetooth
HDMI
Optical/HDMI ARC
USB2
Wi-Fi
```

Supported soundbar sound modes include:

```text
AI Sound Pro
Bass Boost Plus
Cinema
Dolby Atmos
Game
Music
Sports
Standard
User
```

### Relay-only Devices

| Device | Registry ID | Home Assistant entity |
| ------ | ----------- | --------------------- |
| Subwoofer | `subwoofer` | `switch.subwoofer` |
| Rear Speakers | `rear_speakers` | `switch.rear_speakers` |

### Device Groups

| Spoken phrase | Registry target | Devices controlled |
| ------------- | --------------- | ------------------ |
| Sound system / audio system | `sound_system` | Soundbar + Subwoofer |
| All speakers / speaker system | `all_speakers` | Soundbar + Subwoofer + Rear Speakers |
| Home theater / home theatre / full system / everything | `home_theater` | TV + Soundbar + Subwoofer + Rear Speakers |

Important semantic rule:

```text
Surround system = rear speakers only
Sound system = soundbar + subwoofer
All speakers = soundbar + subwoofer + rear speakers
Home theater = TV + soundbar + subwoofer + rear speakers
```

### Power vs Sleep/Wake

ZYRA separates physical relay power from smart-device sleep/wake.

```text
Turn on TV
→ relay mains power on
→ switch.sony_tv

Wake TV
→ smart TV wake
→ media_player.living_room_tv

Turn off TV
→ relay mains power off
→ switch.sony_tv

Sleep TV
→ smart TV sleep / standby
→ media_player.living_room_tv
```

The same distinction applies to the LG S95TR Soundbar:

```text
Turn on soundbar
→ relay mains power on
→ switch.soundbar

Wake soundbar
→ smart soundbar wake
→ media_player.lg_soundbar

Turn off soundbar
→ relay mains power off
→ switch.soundbar

Sleep soundbar
→ smart soundbar sleep / standby
→ media_player.lg_soundbar
```

### Status Intelligence

ZYRA can answer detailed status questions through Home Assistant.

Examples:

```text
What is the status of my TV?
What input is the TV using?
What is the soundbar volume?
What sound mode is the soundbar using?
What is the status of my home theater?
Which devices are on?
```

Example response:

```text
What is the status of my home theater?

ZYRA response:
Sony Bravia 2 MK2 is on. input is HDMI1. playing Netflix. LG S95TR Soundbar is on. input is E-ARC. sound mode is Cinema. volume is 25 percent. Subwoofer is on. Rear Speakers is on.
```

When Home Assistant is unavailable, ZYRA can still read relay-board status for relay-supported devices and adds a backend note explaining that detailed Home Assistant status is unavailable.

### Supported Online Smart-home Examples

```text
Turn on the TV
Turn off the soundbar
Wake the TV
Sleep the soundbar
Increase TV volume
Increase TV volume by 5
Set soundbar volume to 25
Mute the television
Put the TV on HDMI 1
Change the soundbar input to HDMI ARC
Use cinema sound on the soundbar
Pause whatever is playing on the TV
Open Netflix on TV
Launch Prime Video
Open Jio Hotstar
Start YouTube on TV
Go to home screen
Exit the app
Close the app
Close Netflix
Close Prime Video
Turn off the sound system and turn on the TV
Shut down the home theater except keep the TV on
What is the status of my home theater?
```

The router is designed to reject general conversation as smart-home commands.

Examples that remain general conversation:

```text
What are the best horror movies?
How are you?
Explain how Bluetooth works.
Recommend a good action movie.
```

---

## Zyra Runtime SmartHome Connectivity 

ZYRA has multiple fallback paths for Smart home control.

Full runtime mode hierarchy:

```text
Boot
↓
Connect to home Wi-Fi
↓
Check ZYRA Server
    ↓ available
    Check Home Assistant
        ↓ available
        Mode 1: Online Intelligent HA Mode
        ESP32-S3 → Server → Home Assistant → MQTT → ESP8266

        ↓ unavailable
        Mode 2: Online Intelligent Relay Mode
        ESP32-S3 → Server → ESP8266 home IP

    ↓ unavailable
    Check Home Assistant
        ↓ available
        Mode 3: Serverless HA Mode
        ESP32-S3 → Home Assistant REST API

        ↓ unavailable
        Check ESP8266 home IP
            ↓ available
            Mode 4: Serverless Wi-Fi Relay Mode
            ESP32-S3 → ESP8266 home IP

            ↓ unavailable
            Mode 5: Emergency Offline Relay AP Mode
            ESP32-S3 → ESP8266 direct AP IP
```

---

## Serverless/Offline Voice Commands

Serverless/offline voice commands are handled by ESP-SR MultiNet on the ESP32-S3.

These commands are limited and are intended only for local smart-home control.

Supported offline/serverless command groups include:

```text
TV on / off / toggle
Soundbar on / off / toggle
Subwoofer on / off / toggle
Rear speakers on / off / toggle
Sound system on / off
All speakers on / off
Home theater on / off
Status / device status
```

Example offline/serverless commands:

```text
Jarvis, TV on
Jarvis, turn off soundbar
Jarvis, subwoofer on
Jarvis, rear speakers off
Jarvis, turn on sound system
Jarvis, turn off all speakers
Jarvis, home theater on
Jarvis, status
```

Current limitation:

```text
Full offline AI conversation is not included.
```

The serverless/offline voice layer is only for limited relay and Home Assistant control. Natural conversation, memory, full STT, and LLM reasoning still require the ZYRA Python server.

---

## Offline Speech Prompts

The firmware uses SPIFFS WAV files for local spoken confirmations.

Prompt examples:

```text
done.wav
failed.wav
status.wav
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
status_0000.wav ... status_1111.wav
```

These prompts are used when the ESP32-S3 is running serverless/offline command handling.

The WAV files should be:

```text
Mono
16-bit PCM
Compatible sample rate for the firmware playback path
Stored inside zyra-firmware/spiffs/
```

---

## Smart-Switch-Board Relay Endpoints

The ESP8266 relay board exposes HTTP endpoints.

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

Ping endpoint:

```text
/ping
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
* MAX98357A I2S class-D amplifier
* 0.96 inch SSD1306 I2C OLED display
* Speaker
* PC or laptop for running the ZYRA server
* Home Assistant hub
* Separate ESP8266 Smart-Switch-Board

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

ZYRA uses Kokoro TTS for online speech generation.

Current voice:

```text
af_heart
```

Current Kokoro behavior:

```text
Voice: af_heart
Language: American English
Sample rate: 24000 Hz
Device: CPU
Speed: 1.12
Volume gain: 1.8
```

The server streams Kokoro PCM audio to the ESP32-S3 in WebSocket binary frames.

Stable streamed TTS design:

```text
Kokoro TTS
↓
Small/medium text chunks
↓
One-chunk-ahead generation
↓
Tiny segment merge
↓
Silence trim at chunk boundaries
↓
PCM WebSocket frames
↓
ESP32-S3 audio_streamer queue
↓
I2S speaker playback
```

Test Kokoro TTS:

```powershell
python test_tts_api.py
```

This generates:

```text
zyra_kokoro_test.wav
```

---

## Environment Setup

Create a `.env` file from the example:

```powershell
copy .env.example .env
```

Typical required values:

```env
HOST=0.0.0.0
PORT=8765

OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=llama3.2:3b-instruct-q4_0

WHISPER_MODEL=medium.en
WHISPER_DEVICE=cuda
WHISPER_COMPUTE=float16

HOME_ASSISTANT_URL=http://homeassistant.local:8123
HOME_ASSISTANT_TOKEN=your_home_assistant_long_lived_token

HA_ENTITY_TV=switch.sony_tv
HA_ENTITY_SOUNDBAR=switch.soundbar
HA_ENTITY_SUBWOOFER=switch.subwoofer
HA_ENTITY_REAR=switch.rear_speakers

RELAY_HOME_BASE_URLS=http://192.168.29.97,http://192.168.29.24
RELAY_HTTP_TIMEOUT_SEC=2.5

CHROMA_PATH=./data/chroma
SQLITE_PATH=./data/zyra.db
```
The four `HA_ENTITY_*` values are used for relay/mains power status and fallback compatibility.

Detailed smart-device entities such as TV volume, TV source, TV app launch, soundbar source, and soundbar sound mode are configured in:

```text
zyra-server/device_registry.json
```

Do not commit `.env`.

---

## Test Server Components

Run:

```powershell
cd C:\Users\abhia\Documents\ZYRA\zyra-server
.\.venv\Scripts\Activate.ps1
python test_components.py
```

This tests:

```text
Ollama
Whisper
Kokoro TTS
Memory
```

Test the capability-aware device registry:

```powershell
python test_device_registry.py
```

Test the semantic registry router:

```powershell
python test_registry_intent_router.py
```

Test Home Assistant and relay fallback:

```powershell
python test_home_assistant.py
```

Optional live registry control test:

```powershell
python test_registry_live.py
```

Test Kokoro TTS WAV generation:

```powershell
python test_tts_api.py
```

Test WebSocket from PC:

```powershell
python test_websocket.py --text "What can you do?"
```

Interactive WebSocket test:

```powershell
python test_websocket.py --interactive
```

---

## Run Server

Start Ollama first:

```powershell
ollama serve
```

Make sure the selected model exists:

```powershell
ollama list
```

Run the ZYRA server:

```powershell
cd C:\Users\abhia\Documents\ZYRA\zyra-server
.\.venv\Scripts\Activate.ps1
python main.py
```

Expected server output:

```text
ZYRA server starting
Whisper ready
Kokoro TTS ready
Registry intent router ready
Memory engine ready
Smart home engine ready
```

The server runs the WebSocket endpoint:

```text
ws://<laptop-ip>:8765/zyra
```

Health endpoint:

```text
http://<laptop-ip>:8765/health
```

---

## Firmware Setup

The ZYRA firmware is built using **ESP-IDF**, not the normal Windows PowerShell alone.

On Windows, run firmware commands inside the ESP-IDF terminal.

Recommended terminal:

```text
ESP-IDF 5.3 PowerShell
```

or:

```text
ESP-IDF 5.3 Command Prompt
```

Open it from:

```text
Start Menu
→ ESP-IDF 5.3
→ ESP-IDF 5.3 PowerShell
```

If you run `idf.py` in a normal PowerShell window and it says `idf.py is not recognized`, it means the ESP-IDF environment is not loaded. Open the ESP-IDF terminal instead.

### Go to Firmware Folder

Run these commands inside the ESP-IDF terminal:

```powershell
cd C:\Users\abhia\Documents\ZYRA\zyra-firmware
```

Set the ESP32-S3 target:

```powershell
idf.py set-target esp32s3
```

### Create Private Firmware Config

Copy the safe config template:

```powershell
cd C:\Users\abhia\Documents\ZYRA\zyra-firmware\main
copy zyra_config.example.h zyra_config.h
```

Edit this private file:

```text
zyra-firmware/main/zyra_config.h
```

This file contains private Wi-Fi, server, Home Assistant, and relay settings.

Do not commit `zyra_config.h`.

### Example Firmware Config

```c
#pragma once

// ── Home Wi-Fi + ZYRA server ─────────────────────
#define WIFI_SSID "YOUR_WIFI_NAME"
#define WIFI_PASSWORD "YOUR_WIFI_PASSWORD"

#define SERVER_IP "192.168.29.77"
#define SERVER_PORT 8765

// ── Home Assistant backend ───────────────────────
#define HOME_ASSISTANT_URL "http://homeassistant.local:8123"
#define HOME_ASSISTANT_TOKEN "YOUR_LONG_LIVED_ACCESS_TOKEN"

#define HA_ENTITY_TV "switch.sony_tv"
#define HA_ENTITY_SOUNDBAR "switch.soundbar"
#define HA_ENTITY_SUBWOOFER "switch.subwoofer"
#define HA_ENTITY_REAR "switch.rear_speakers"

// ── ESP8266 relay board on home Wi-Fi ─────────────
#define RELAY_HOME_BASE_URL "http://192.168.29.24"

// ── Emergency relay AP fallback ───────────────────
#define RELAY_AP_SSID "ESP-REMOTE-DIRECT"
#define RELAY_AP_PASSWORD "12345678"
#define RELAY_AP_BASE_URL "http://192.168.4.1"
```

Use your actual values.

### Find Laptop IP Address

On the laptop running the ZYRA Python server, open PowerShell and run:

```powershell
ipconfig
```

Find the IPv4 address under your Wi-Fi adapter.

Example:

```text
192.168.29.77
```

Use that value as:

```c
#define SERVER_IP "192.168.29.77"
```

### Find ESP32-S3 COM Port

Connect the ESP32-S3 using USB.

In Windows:

```text
Device Manager
→ Ports (COM & LPT)
```

Find the ESP32-S3 port.

Example:

```text
COM12
```

Use your real port in the flash command.

### Build Firmware

Run inside the ESP-IDF terminal:

```powershell
cd C:\Users\abhia\Documents\ZYRA\zyra-firmware
idf.py build
```

The build should generate:

```text
Firmware app
ESP-SR model partition
SPIFFS storage image
```

The SPIFFS image is generated from:

```text
zyra-firmware/spiffs/
```

and includes the offline/serverless WAV prompts.

### Flash Firmware

Run inside the ESP-IDF terminal:

```powershell
idf.py -p COM12 flash monitor
```

Replace `COM12` with your real ESP32-S3 port.

Example:

```powershell
idf.py -p COM11 flash monitor
```

### Exit Serial Monitor

To exit the ESP-IDF serial monitor:

```text
Ctrl + ]
```

### Full Firmware Command Flow

Typical full firmware flow:

```powershell
cd C:\Users\abhia\Documents\ZYRA\zyra-firmware
idf.py set-target esp32s3
idf.py build
idf.py -p COM12 flash monitor
```

Use the ESP-IDF terminal for these commands.

### Expected Online Firmware Output

```text
WiFi connected
Mic initialized on I2S port 1
Amp initialized on I2S port 0
WakeNet ready
Status LED initialized
Connected to ZYRA server
ZYRA online
```

### Expected Wakeword Flow

```text
Wake word detected
Listening
Sending audio to server
Streaming audio incoming
Final streamed audio chunk enqueued
Returning to idle
```

### Expected Serverless HA Fallback Output

```text
Server connection failed
Choosing best serverless mode
Entering serverless HA mode
```

### Expected Emergency Relay AP Output

```text
Home relay unavailable
Trying emergency AP mode
ZYRA switched to direct AP emergency relay mode
```

### Important Firmware Notes

Do not commit:

```text
zyra-firmware/main/zyra_config.h
zyra-firmware/build/
zyra-firmware/managed_components/
zyra-firmware/dependencies.lock
```

Safe to commit:

```text
zyra-firmware/main/zyra_config.example.h
zyra-firmware/sdkconfig
zyra-firmware/partitions.csv
zyra-firmware/spiffs/
```

Only commit `zyra-firmware/spiffs/` when it contains required source WAV prompts for offline/serverless mode.

---

## Home Assistant Hub Setup (Optional)

ZYRA can use Home Assistant as the preferred smart-home backend.

In the full ZYRA runtime, Home Assistant is used in:

```text
Mode 1 — Online Intelligent HA Mode
ESP32-S3 → ZYRA Server → Home Assistant → MQTT → ESP8266 relay board

Mode 3 — Serverless HA Mode
ESP32-S3 firmware → Home Assistant REST API → Home Assistant / MQTT → smart devices
```

Home Assistant is optional for basic relay fallback, but it is recommended for the best smart-home experience, Control various external smart devices like TV media, Lights, Fan, AC, Thermostat and other.

### Recommended Home Assistant Hub

Use a dedicated device for Home Assistant.

Recommended options:

```text
Old laptop / mini PC / Intel NUC / dedicated x86-64 machine
```

For this project, a dedicated old laptop is a good option.

Important:

```text
Installing Home Assistant OS on a laptop or PC will turn that device into a Home Assistant hub.
It is not meant to be used like a normal Windows/Linux laptop after installation.
Back up important files before installing Home Assistant OS.
```

### Basic Installation Flow

1. Download the Home Assistant OS image for Generic x86-64.
2. Flash the Home Assistant OS image to the target drive.
3. Boot the laptop/PC from the Home Assistant drive.
4. Make sure BIOS/UEFI settings are correct.
5. Complete the Home Assistant onboarding from a browser.

Required BIOS/UEFI settings usually include:

```text
UEFI boot: Enabled
Secure Boot: Disabled
```

After booting, open Home Assistant from another device on the same network:

```text
http://homeassistant.local:8123
```

If that does not open, check the router device list and use the Home Assistant hub IP address:

```text
http://<home-assistant-ip>:8123
```

Example:

```text
http://192.168.29.50:8123
```

### Create Home Assistant Account

During first boot, Home Assistant will ask you to create an owner account.

Create the account and complete the onboarding steps.

After setup, keep the Home Assistant hub powered on and connected to the same network as:

```text
ZYRA server laptop
ESP32-S3 ZYRA device
ESP8266 Smart-Switch-Board
```

### Install MQTT Broker

The ESP8266 Smart-Switch-Board can connect to Home Assistant through MQTT.

Install the Mosquitto broker inside Home Assistant:

```text
Settings
→ Add-ons
→ Add-on Store
→ Mosquitto broker
→ Install
→ Start
→ Enable Start on boot
```

Then add or confirm the MQTT integration:

```text
Settings
→ Devices & services
→ Integrations
→ MQTT
```

If MQTT is discovered automatically, open it and finish the setup.

### Create MQTT User

Create a dedicated MQTT user for the ESP8266 relay board.

Example:

```text
Username: zyra_mqtt
Password: your_secure_mqtt_password
```

Use this MQTT username and password in the separate Smart-Switch-Board firmware.

Do not commit real MQTT credentials to GitHub.

### Create or Confirm Relay Switch Entities

ZYRA expects Home Assistant switch entities for the four relay devices.

Recommended entity IDs:

```text
switch.sony_tv
switch.soundbar
switch.subwoofer
switch.rear_speakers
```

These should map to:

```text
TV
Soundbar
Subwoofer
Rear speakers
```

If your Home Assistant entity IDs are different, update both:

```text
zyra-server/.env
zyra-firmware/main/zyra_config.h
```

### Example ZYRA Server Home Assistant Config

Inside `zyra-server/.env`:

```env
HOME_ASSISTANT_URL=http://homeassistant.local:8123
HOME_ASSISTANT_TOKEN=your_home_assistant_long_lived_token

HA_ENTITY_TV=switch.sony_tv
HA_ENTITY_SOUNDBAR=switch.soundbar
HA_ENTITY_SUBWOOFER=switch.subwoofer
HA_ENTITY_REAR=switch.rear_speakers
```

If `homeassistant.local` does not work, use the Home Assistant hub IP:

```env
HOME_ASSISTANT_URL=http://192.168.29.50:8123
```

Do not commit `.env`.

### Example ESP32-S3 Firmware Home Assistant Config

Inside `zyra-firmware/main/zyra_config.h`:

```c
#define HOME_ASSISTANT_URL "http://homeassistant.local:8123"
#define HOME_ASSISTANT_TOKEN "YOUR_LONG_LIVED_ACCESS_TOKEN"

#define HA_ENTITY_TV "switch.sony_tv"
#define HA_ENTITY_SOUNDBAR "switch.soundbar"
#define HA_ENTITY_SUBWOOFER "switch.subwoofer"
#define HA_ENTITY_REAR "switch.rear_speakers"
```

If `homeassistant.local` does not resolve from the ESP32-S3, use the Home Assistant hub IP:

```c
#define HOME_ASSISTANT_URL "http://192.168.29.50:8123"
```

Do not commit `zyra_config.h`.

### Create Long-Lived Access Token

ZYRA needs a Home Assistant Long-Lived Access Token for REST API control.

In Home Assistant:

```text
Click your profile icon
→ Security
→ Long-lived access tokens
→ Create token
```

Name it:

```text
ZYRA
```

Copy the token immediately.

Paste it into:

```text
zyra-server/.env
zyra-firmware/main/zyra_config.h
```

Important:

```text
The token is private.
Do not share it.
Do not commit it.
Do not paste it into README.md.
```

### Test Home Assistant Backend

From the ZYRA server folder:

```powershell
cd C:\Users\abhia\Documents\ZYRA\zyra-server
.\.venv\Scripts\Activate.ps1
python test_home_assistant.py
```

This test checks:

```text
Home Assistant backend
ESP8266 relay-home fallback backend
Selected smart-home backend
```

Expected result:

```text
SUCCESS — at least one smart-home backend is reachable.
```

### Home Assistant Role in ZYRA

Home Assistant gives ZYRA a proper smart-home hub layer.

With Home Assistant available:

```text
ZYRA can control devices through Home Assistant
ZYRA can verify entity states
ZYRA can detect unavailable entities
ZYRA can fall back to relay direct control if Home Assistant is down
ESP32-S3 can use Serverless HA Mode if the Python server is unavailable
```

If Home Assistant is unavailable, ZYRA can still fall back to direct ESP8266 relay control.

---

## Pin Configuration

### INMP441 Microphone

| INMP441 | ESP32-S3 |
| ------- | -------- |
| SCK     | GPIO 1   |
| WS      | GPIO 2   |
| SD      | GPIO 3   |
| VCC     | 3.3V     |
| GND     | GND      |

### MAX98357A Amplifier

| MAX98357A | ESP32-S3             |
| --------- | -------------------- |
| BCLK      | GPIO 4               |
| LRC       | GPIO 5               |
| DIN       | GPIO 21              |
| VIN       | 5V / suitable supply |
| GND       | GND                  |

### OLED Display

| OLED | ESP32-S3               |
| ---- | ---------------------- |
| SDA  | GPIO 15                |
| SCL  | GPIO 16                |
| VCC  | 3.3V                   |
| GND  | GND                    |


## Important Notes

* Do not commit `.env`.
* Do not commit `zyra_config.h`.
* Do not commit private Wi-Fi passwords.
* Do not commit Home Assistant long-lived access tokens.
* Do not commit generated build folders.
* Do not commit virtual environments.
* The ESP8266 Smart-Switch-Board / relay-board firmware is a separate project.
* Full online AI conversation requires the Python ZYRA server.
* Serverless/offline firmware control is intentionally limited to supported smart-home commands.


---

## Troubleshooting

### Server does not start

Check the virtual environment:

```powershell
cd C:\Users\abhia\Documents\ZYRA\zyra-server
.\.venv\Scripts\Activate.ps1
python main.py
```

Check dependencies:

```powershell
python -m pip install -r requirements.txt
```

### Ollama not responding

Start Ollama:

```powershell
ollama serve
```

Check model list:

```powershell
ollama list
```

Make sure the configured model is installed.

### Whisper CUDA not working

Run:

```powershell
python -c "import torch; print(torch.cuda.is_available())"
```

If it prints `False`, reinstall the CUDA PyTorch build.

### WebSocket disconnects during speaking

Check that the server uses:

```python
ws="wsproto"
```

in `uvicorn.run`.

Check that ESP32 WebSocket ping is disabled:

```c
.ping_interval_sec = 0
```

### ESP32 cannot connect to server

Check:

* Laptop IP address
* `SERVER_IP` in `zyra_config.h`
* Windows firewall rule for port `8765`
* ESP32 and laptop are on the same Wi-Fi
* Server is running before ESP32 tries to connect

### Home Assistant commands fail

Check:

* Home Assistant URL
* Long-lived access token
* Entity IDs
* MQTT switch configuration in Home Assistant
* ESP8266 relay board availability
* `python test_home_assistant.py`

Expected entity IDs:

```text
switch.sony_tv
switch.soundbar
switch.subwoofer
switch.rear_speakers
```

### Relay fallback does not work

Check ESP8266 endpoints:

```text
/ping
/status
/sony/on
/sony/off
/sb/on
/sb/off
/sub/on
/sub/off
/rear/on
/rear/off
```

Check relay base URLs:

```text
RELAY_HOME_BASE_URL
RELAY_AP_BASE_URL
```

### Offline/serverless voice does not detect commands

Check:

* ESP-SR model partition is flashed
* MultiNet English model is enabled
* Microphone wiring is stable
* INMP441 is directly wired
* Voice command is one of the registered supported phrases

### Offline speech prompt missing

Check:

* `offline_speech.c` is included in the firmware build
* SPIFFS is mounted successfully
* Required `.wav` files exist inside `zyra-firmware/spiffs/`
* WAV files are mono 16-bit PCM
* SPIFFS image is flashed into the `storage` partition
* MAX98357A pins match `audio_pipeline.c`

### Capture buffer allocation failed

Enable PSRAM in ESP-IDF menuconfig.

Expected healthy log:

```text
Free PSRAM before alloc: ...
Capture buffer allocated successfully
```

### OLED I2C timeout

Check OLED pins in `display.c`.

If your OLED test sketch uses different pins, update `display.c` to match your real wiring.

Also check:

* SDA/SCL wiring
* 3.3V power
* GND
* OLED address `0x3C`
* Pull-up stability
* Avoid loose jumper/breadboard connections

---

## Current Status

Completed:

* PC-side AI server
* FastAPI WebSocket server
* `wsproto` WebSocket backend for stable streamed audio
* Faster-Whisper STT
* Whisper silence/noise/hallucination filtering
* Ollama LLM
* LLM warmup for lower first-request delay
* Kokoro TTS using `af_heart`
* Kokoro sample rate at 24 kHz
* Streamed online TTS playback
* ESP32-S3 audio streamer queue
* PCM WebSocket frame streaming
* Tiny TTS segment merge
* TTS silence trimming
* Single-worker one-chunk-ahead Kokoro streaming
* Memory engine using ChromaDB and SQLite
* Smart-home intent routing
* Deterministic smart-home parser for safer physical control
* Multi-command smart-home handling
* Smart-home status queries
* Home Assistant REST backend on the server
* Direct ESP8266 relay HTTP fallback on the server
* Home Assistant entity unavailable handling
* Smart-home command success/failure metadata
* ESP32-S3 Wi-Fi connection
* ESP32-S3 WebSocket connection
* ESP32-S3 streamed audio reception
* Jarvis wakeword support using ESP-SR WakeNet
* ESP-SR MultiNet serverless/offline command recognizer
* Firmware-side Home Assistant REST control
* Firmware-side ESP8266 relay home-IP control
* Firmware-side ESP8266 direct AP relay control
* Complete 5-mode runtime fallback hierarchy
* Runtime fallback trigger when server connection is lost
* Runtime mode guards to avoid online/serverless conflicts
* Home Assistant restore monitoring
* Server restore monitoring
* I2S mic initialization
* I2S amplifier initialization
* PSRAM audio buffer allocation
* Firmware-side relay status sync
* Firmware-side offline/serverless speech prompt playback
* SPIFFS asset support
* OLED states for wakeword, online, serverless, offline, speaking, success, and error states
* RGB status LED feedback for online, serverless, offline, speaking, success, error, and connection failure states

Not included yet:

* Full offline AI conversation without the ZYRA server
* Full natural conversation directly on the ESP32-S3
* Final polished UI animation upgrade
* Wakeword changed from Jarvis to custom “Hey Zyra”
* Smart-Switch-Board / relay-board firmware inside this repository
