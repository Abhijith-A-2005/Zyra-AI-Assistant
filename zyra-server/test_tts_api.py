"""
Kokoro TTS API test for ZYRA.

Usage:
    python test_tts_api.py
"""

import wave
from pathlib import Path

from tts import TTSEngine


TEXT = "Hello, I am Zyra. Kokoro voice is working."
OUTPUT_FILE = Path("zyra_kokoro_test.wav")


def main():
    print("Loading Kokoro TTS...")
    tts = TTSEngine(name="api_test", prewarm=False)

    print(f"Sample rate: {tts.sample_rate} Hz")
    print("Synthesizing test voice...")

    pcm = tts.synthesize(TEXT)

    if not pcm:
        raise RuntimeError("Kokoro returned empty audio")

    with wave.open(str(OUTPUT_FILE), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(tts.sample_rate)
        wf.writeframes(pcm)

    print(f"SUCCESS — generated {len(pcm)} bytes")
    print(f"Saved test WAV: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()