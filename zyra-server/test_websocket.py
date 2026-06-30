"""
ZYRA WebSocket Test Client
Simulates the ESP32 — tests the full server pipeline from your PC.

Usage:
    # Text mode (type your query directly, no mic needed)
    python test_websocket.py --text "What is the capital of France?"

    # Mic mode (records from your PC microphone)
    python test_websocket.py --mic

    # Audio file mode (send a WAV file)
    python test_websocket.py --file path/to/audio.wav

    # Interactive text mode (keep chatting)
    python test_websocket.py --interactive

Install deps if needed:
    pip install websockets sounddevice soundfile numpy
"""

import asyncio
import json
import sys
import argparse
import numpy as np
import websockets

SERVER = "ws://localhost:8765/zyra"
SAMPLE_RATE = 16000


# ── Text → fake audio (silence + STT bypass) ──────────────────────────────────
# Instead of going through STT, we inject text directly via a special message.
# This lets you test LLM + TTS without a microphone.

async def test_text(query: str):
    """Send a text query directly, bypassing STT."""
    print(f"\n[TEST] Connecting to {SERVER}")
    async with websockets.connect(SERVER) as ws:
        print(f"[TEST] Connected. Sending: '{query}'")

        # Send as a special text injection message
        await ws.send(json.dumps({
            "type": "text_inject",
            "text": query
        }))

        await _receive_response(ws)


async def test_mic(duration: int = 5):
    """Record from PC microphone and send as audio."""
    try:
        import sounddevice as sd
    except ImportError:
        print("Install sounddevice: pip install sounddevice")
        return

    print(f"\n[TEST] Recording {duration}s from microphone...")
    print("       Speak now!")
    audio = sd.rec(
        int(duration * SAMPLE_RATE),
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype=np.int16
    )
    sd.wait()
    print("[TEST] Recording done.")

    audio_bytes = audio.tobytes()
    await _send_audio(audio_bytes)


async def test_file(filepath: str):
    """Send a WAV file as audio."""
    try:
        import soundfile as sf
    except ImportError:
        print("Install soundfile: pip install soundfile")
        return

    print(f"\n[TEST] Loading audio from {filepath}")
    data, sr = sf.read(filepath, dtype="int16")
    if data.ndim > 1:
        data = data[:, 0]  # mono
    if sr != SAMPLE_RATE:
        print(f"[WARN] File is {sr}Hz, server expects {SAMPLE_RATE}Hz")

    audio_bytes = data.tobytes()
    await _send_audio(audio_bytes)


async def _send_audio(audio_bytes: bytes):
    print(f"\n[TEST] Connecting to {SERVER}")
    async with websockets.connect(SERVER) as ws:
        print(f"[TEST] Sending {len(audio_bytes)} bytes of audio...")
        await ws.send(audio_bytes)
        await _receive_response(ws)


async def _receive_response(ws):
    """Listen for server responses and print/play them."""
    audio_expected = 0
    audio_chunks = []
    sample_rate = 22050

    try:
        async for message in ws:
            if isinstance(message, str):
                data = json.loads(message)
                status = data.get("status", "")
                stage  = data.get("stage",  "")

                if stage == "transcribing":
                    print("[STT ] Transcribing audio...")

                elif stage == "thinking":
                    transcript = data.get("transcript", "")
                    print(f"[STT ] Transcript: '{transcript}'")
                    print("[LLM ] Thinking...")

                elif stage == "command":
                    response = data.get("response", "")
                    ok = data.get("command_success", None)

                    if ok is True:
                        print(f"[CMD ] Success: '{response}'")
                    elif ok is False:
                        print(f"[CMD ] Failed: '{response}'")
                    else:
                        print(f"[CMD ] Response: '{response}'")

                elif stage == "speaking":
                    response = data.get("response", "")
                    print(f"[LLM ] Response: '{response}'")
                    print("[TTS ] Synthesizing audio...")

                elif status == "audio_incoming":
                    audio_expected = data.get("audio_bytes", 0)
                    sample_rate    = data.get("sample_rate", 22050)
                    print(f"[TTS ] Receiving {audio_expected} bytes"
                          f" at {sample_rate}Hz...")

                elif status == "audio_stream_end":
                    print("[TTS ] Stream ended.")
                    break

                elif status == "ready":
                    print(f"[READY] {data.get('message', 'Ready')}")
                    break

                elif status == "error":
                    print(f"[ERR ] {data.get('message', 'Unknown error')}")
                    break

                # For text_inject responses
                elif data.get("type") == "pong":
                    pass

            elif isinstance(message, bytes):
                audio_chunks.append(message)
                received = sum(len(c) for c in audio_chunks)
                print(f"[TTS ] Received {received}/{audio_expected} bytes")

                if received >= audio_expected and audio_expected > 0:
                    print("[TTS ] Audio complete!")
                    audio_data = b"".join(audio_chunks)
                    _play_audio(audio_data, sample_rate)
                    break

    except websockets.exceptions.ConnectionClosed as e:
        print(f"[TEST] Connection closed: code={e.code} reason={e.reason}")


def _play_audio(audio_bytes: bytes, sample_rate: int):
    """Play the received PCM audio on PC speakers."""
    try:
        import sounddevice as sd
        audio_np = np.frombuffer(audio_bytes, dtype=np.int16)
        audio_float = audio_np.astype(np.float32) / 32768.0
        print(f"[PLAY] Playing response ({len(audio_np)} samples"
              f" at {sample_rate}Hz)...")
        sd.play(audio_float, samplerate=sample_rate)
        sd.wait()
        print("[PLAY] Done.")
    except ImportError:
        # Save to file if sounddevice not available
        _save_audio(audio_bytes, sample_rate)
    except Exception as e:
        print(f"[PLAY] Playback error: {e} — saving to file instead")
        _save_audio(audio_bytes, sample_rate)


def _save_audio(audio_bytes: bytes, sample_rate: int):
    """Save audio to WAV file as fallback."""
    import wave
    out = "zyra_response.wav"
    with wave.open(out, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(audio_bytes)
    print(f"[SAVE] Audio saved to {out}")


async def interactive_mode():
    """Keep a conversation going — type queries one by one."""
    print("\n[ZYRA] Interactive mode — type your queries (Ctrl+C to quit)")
    print(f"[ZYRA] Connecting to {SERVER}\n")

    async with websockets.connect(SERVER) as ws:
        while True:
            try:
                query = input("You: ").strip()
                if not query:
                    continue

                await ws.send(json.dumps({
                    "type": "text_inject",
                    "text": query
                }))

                # Collect response
                audio_expected = 0
                audio_chunks   = []
                sample_rate    = 22050

                async for message in ws:
                    if isinstance(message, str):
                        data   = json.loads(message)
                        status = data.get("status", "")
                        stage  = data.get("stage",  "")

                        if stage == "speaking":
                            response = data.get("response", "")
                            print(f"ZYRA: {response}")

                        elif status == "audio_incoming":
                            audio_expected = data.get("audio_bytes", 0)
                            sample_rate    = data.get("sample_rate", 22050)

                        elif status == "error":
                            print(f"[ERR] {data.get('message')}")
                            break

                    elif isinstance(message, bytes):
                        audio_chunks.append(message)
                        received = sum(len(c) for c in audio_chunks)
                        if received >= audio_expected and audio_expected > 0:
                            audio_data = b"".join(audio_chunks)
                            _play_audio(audio_data, sample_rate)
                            break

            except KeyboardInterrupt:
                print("\n[ZYRA] Goodbye.")
                break


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="ZYRA WebSocket Test Client"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--text",        type=str,
                       help="Send a text query directly")
    group.add_argument("--mic",         action="store_true",
                       help="Record from microphone")
    group.add_argument("--file",        type=str,
                       help="Send a WAV audio file")
    group.add_argument("--interactive", action="store_true",
                       help="Interactive chat mode")

    global SERVER

    parser.add_argument("--duration", type=int, default=5,
                        help="Mic recording duration in seconds (default 5)")
    parser.add_argument("--server", type=str, default=SERVER,
                        help=f"Server URL (default: {SERVER})")

    args = parser.parse_args()

    SERVER = args.server

    if args.text:
        asyncio.run(test_text(args.text))
    elif args.mic:
        asyncio.run(test_mic(args.duration))
    elif args.file:
        asyncio.run(test_file(args.file))
    elif args.interactive:
        asyncio.run(interactive_mode())


if __name__ == "__main__":
    main()