import logging
import threading
from collections import OrderedDict

import numpy as np

logger = logging.getLogger(__name__)

KOKORO_VOICE = "af_heart"
KOKORO_LANG_CODE = "a"   # American English
KOKORO_SPEED = 1.12
KOKORO_VOLUME_GAIN = 1.8
KOKORO_LIMIT_PEAK = 0.98
KOKORO_SAMPLE_RATE = 24000
KOKORO_REPO_ID = "hexgrad/Kokoro-82M"
KOKORO_CACHE_MAX_ITEMS = 80
KOKORO_CACHE_MAX_AUDIO_BYTES = 900_000


class TTSEngine:
    def __init__(self, name: str = "main", prewarm: bool = True):
        self.name = name
        self.cache = OrderedDict()
        self.cache_lock = threading.Lock()
        
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

            cache_key = (
                text,
                KOKORO_VOICE,
                KOKORO_SPEED,
                KOKORO_VOLUME_GAIN,
            )

            with self.cache_lock:
                cached = self.cache.get(cache_key)
                if cached is not None:
                    self.cache.move_to_end(cache_key)
                    logger.info(
                        f"Kokoro cache hit [{self.name}] {len(cached)} bytes "
                        f"for: '{text[:60]}'"
                    )
                    return cached

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

            if len(raw_pcm) <= KOKORO_CACHE_MAX_AUDIO_BYTES:
                with self.cache_lock:
                    self.cache[cache_key] = raw_pcm
                    self.cache.move_to_end(cache_key)

                    while len(self.cache) > KOKORO_CACHE_MAX_ITEMS:
                        self.cache.popitem(last=False)

            return raw_pcm

        except Exception as e:
            logger.error(f"Kokoro TTS error: {e}", exc_info=True)
            return b""
    