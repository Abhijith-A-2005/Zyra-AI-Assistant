# save as test_tts_api.py in C:\zyra-server
import io
import wave
import sys

MODEL_PATH = r"C:\zyra-server\models\en_US-lessac-high.onnx"
TEXT = "Hello, I am ZYRA."

print("Loading PiperVoice...")
from piper.voice import PiperVoice
voice = PiperVoice.load(MODEL_PATH)
sample_rate = voice.config.sample_rate
print(f"Model loaded. Sample rate: {sample_rate}")
print(f"Available methods: {[m for m in dir(voice) if not m.startswith('_')]}")

# ── Test 1: synthesize(text, wav_file) ────────────────
print("\n[Test 1] synthesize(text, wav_file_object)...")
try:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        voice.synthesize(TEXT, wf)
    buf.seek(0)
    with wave.open(buf, "rb") as wf:
        pcm = wf.readframes(wf.getnframes())
    print(f"  SUCCESS — got {len(pcm)} bytes of PCM")
except Exception as e:
    print(f"  FAILED — {e}")

# ── Test 2: synthesize_wav(text, wav_file) ────────────
print("\n[Test 2] synthesize_wav(text, wav_file_object)...")
try:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        voice.synthesize_wav(TEXT, wf)
    buf.seek(0)
    with wave.open(buf, "rb") as wf:
        pcm = wf.readframes(wf.getnframes())
    print(f"  SUCCESS — got {len(pcm)} bytes of PCM")
except Exception as e:
    print(f"  FAILED — {e}")

# ── Test 3: synthesize as iterator ───────────────────
print("\n[Test 3] synthesize(text) as iterator...")
try:
    chunks = []
    for chunk in voice.synthesize(TEXT):
        if hasattr(chunk, 'audio_int16_bytes'):
            chunks.append(chunk.audio_int16_bytes)
        elif isinstance(chunk, bytes):
            chunks.append(chunk)
        else:
            print(f"  chunk type: {type(chunk)}, attrs: {dir(chunk)}")
            chunks.append(bytes(chunk))
    result = b"".join(chunks)
    print(f"  SUCCESS — got {len(result)} bytes")
except TypeError as e:
    print(f"  Not iterable or wrong args — {e}")
except Exception as e:
    print(f"  FAILED — {e}")

# ── Test 4: phoneme_ids_to_audio directly ────────────
print("\n[Test 4] phonemize → phonemes_to_ids → phoneme_ids_to_audio...")
try:
    phonemes = list(voice.phonemize(TEXT))
    print(f"  Phonemes: {phonemes[:2]}...")
    ids = voice.phonemes_to_ids(phonemes[0] if phonemes else [])
    print(f"  IDs: {ids[:10]}...")
    audio = voice.phoneme_ids_to_audio(ids)
    print(f"  audio type: {type(audio)}, value: {str(audio)[:80]}")
except Exception as e:
    print(f"  FAILED — {e}")

print("\nDone. Share the output above.")