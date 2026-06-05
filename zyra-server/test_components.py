"""
Run this to verify each component works
before starting the full server.
Usage: python test_components.py
"""
import sys
import os

# Force UTF-8 encoding on Windows
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    os.environ["PYTHONIOENCODING"] = "utf-8"


def test_ollama():
    print("\n── Testing Ollama ──────────────────")
    from llm import LLMEngine
    llm = LLMEngine()
    response = llm.chat("Say exactly: ZYRA online.", [])
    print(f"Response: {response}")
    assert len(response) > 0, "LLM returned empty response"
    print("✓ Ollama working")


def test_whisper():
    print("\n── Testing Whisper ─────────────────")
    from stt import STTEngine
    import numpy as np
    stt = STTEngine()
    silence = np.zeros(16000 * 2, dtype=np.int16).tobytes()
    result  = stt.transcribe(silence)
    print(f"Silence transcription: '{result}'")
    print("✓ Whisper working")


def test_tts():
    print("\n── Testing Piper TTS ───────────────")
    from tts import TTSEngine
    tts   = TTSEngine()
    audio = tts.synthesize("ZYRA is online and ready.")
    print(f"Audio bytes generated: {len(audio)}")
    assert len(audio) > 0, "TTS returned no audio"
    print("✓ Piper TTS working")


def test_memory():
    print("\n── Testing Memory ──────────────────")
    from memory import MemoryEngine
    mem = MemoryEngine()
    mem.store_memory("User prefers concise responses")
    results = mem.recall_memory("how should I respond")
    print(f"Memory recall: {results}")
    print("✓ Memory working")


if __name__ == "__main__":
    try:
        test_ollama()
        test_whisper()
        test_tts()
        test_memory()
        print("\n✓✓✓ All components working — ready to start server")
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        sys.exit(1)