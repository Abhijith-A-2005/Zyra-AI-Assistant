import logging
import numpy as np

logger = logging.getLogger(__name__)

KOKORO_VOICE = "af_heart"
KOKORO_LANG_CODE = "a"   # American English
KOKORO_SPEED = 1.12
KOKORO_VOLUME_GAIN = 1.8
KOKORO_LIMIT_PEAK = 0.98
KOKORO_SAMPLE_RATE = 24000
KOKORO_REPO_ID = "hexgrad/Kokoro-82M"


class TTSEngine:
    def __init__(self, name: str = "main", prewarm: bool = True):
        self.name = name
        self.cache = {}
        
        logger.info(
            f"Loading Kokoro TTS voice on CPU [{self.name}]: {KOKORO_VOICE}"
        )

        from kokoro import KPipeline

        # IMPORTANT:
        # Force Kokoro to CPU.
        # Whisper can still use CUDA because this only affects Kokoro's model.
        self.pipeline = KPipeline(
            lang_code=KOKORO_LANG_CODE,
            repo_id=KOKORO_REPO_ID,
            device="cpu"
        )

        self.sample_rate = KOKORO_SAMPLE_RATE

        # Warm-up once.
        if prewarm:
            _ = self.synthesize("Zyra voice online.")

        logger.info(
            f"Kokoro TTS ready — voice={KOKORO_VOICE}, "
            f"device=cpu, sample_rate={self.sample_rate}Hz"
        )

    def synthesize(self, text: str) -> bytes:
        """
        Returns raw 16-bit PCM bytes at 24kHz mono.
        Applies safe loudness boost after Kokoro generation.
        """
        try:
            if not text or not text.strip():
                return b""

            text = text.strip()

            generator = self.pipeline(
                text,
                voice=KOKORO_VOICE,
                speed=KOKORO_SPEED,
                split_pattern=r"\n+",
            )

            audio_chunks = []

            for _, _, audio in generator:
                audio_np = np.asarray(audio, dtype=np.float32)

                if audio_np.size == 0:
                    continue

                audio_chunks.append(audio_np)

            if not audio_chunks:
                return b""

            audio_np = np.concatenate(audio_chunks)

            # Clean invalid samples.
            audio_np = np.nan_to_num(audio_np, nan=0.0, posinf=0.0, neginf=0.0)

            # Remove tiny DC offset.
            audio_np = audio_np - float(np.mean(audio_np))

            # Boost perceived loudness.
            audio_np = audio_np * KOKORO_VOLUME_GAIN

            # Safe limiter: avoid hard clipping/distortion.
            peak = float(np.max(np.abs(audio_np)))

            if peak > KOKORO_LIMIT_PEAK:
                audio_np = audio_np * (KOKORO_LIMIT_PEAK / peak)

            audio_np = np.clip(audio_np, -KOKORO_LIMIT_PEAK, KOKORO_LIMIT_PEAK)

            pcm16 = (audio_np * 32767.0).astype(np.int16)
            raw_pcm = pcm16.tobytes()

            logger.info(
                f"Kokoro synthesized {len(raw_pcm)} bytes "
                f"speed={KOKORO_SPEED} gain={KOKORO_VOLUME_GAIN} "
                f"for: '{text[:60]}'"
            )

            return raw_pcm

        except Exception as e:
            logger.error(f"Kokoro TTS error: {e}", exc_info=True)
            return b""
    
# import logging
# import io
# import wave

# logger = logging.getLogger(__name__)

# MODEL_PATH = r"C:\Users\abhia\Documents\ZYRA\zyra-server\models\en_US-ljspeech-high.onnx"


# class TTSEngine:
#     def __init__(self):
#         logger.info("Loading Piper voice model into memory...")
#         from piper.voice import PiperVoice
#         self.voice = PiperVoice.load(MODEL_PATH)
#         self.sample_rate = self.voice.config.sample_rate
#         logger.info(f"TTS engine ready — sample rate: {self.sample_rate}Hz")

#     def synthesize(self, text: str) -> bytes:
#         """
#         Returns raw 16-bit PCM bytes at self.sample_rate Hz, mono.
#         Uses synthesize_wav() which writes a proper WAV into a BytesIO buffer,
#         then strips the header so the ESP32 gets raw PCM.
#         """
#         try:
#             buf = io.BytesIO()

#             with wave.open(buf, "wb") as wf:
#                 wf.setnchannels(1)
#                 wf.setsampwidth(2)          # 16-bit
#                 wf.setframerate(self.sample_rate)
#                 self.voice.synthesize_wav(text, wf)

#             # Strip WAV header — return raw PCM only
#             buf.seek(0)
#             with wave.open(buf, "rb") as wf:
#                 raw_pcm = wf.readframes(wf.getnframes())

#             logger.info(
#                 f"TTS synthesized {len(raw_pcm)} bytes "
#                 f"for: '{text[:60]}'"
#             )
#             return raw_pcm

#         except Exception as e:
#             logger.error(f"TTS error: {e}", exc_info=True)
#             return b""
