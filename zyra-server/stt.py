from faster_whisper import WhisperModel
from config import WHISPER_MODEL, WHISPER_DEVICE, WHISPER_COMPUTE
import numpy as np
import io
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class STTEngine:
    def __init__(self):
        logger.info("Loading Whisper model...")
        self.model = WhisperModel(
            WHISPER_MODEL,
            device=WHISPER_DEVICE,
            compute_type=WHISPER_COMPUTE
        )
        logger.info("Whisper ready")

    def transcribe(self, audio_bytes: bytes) -> str:
        """
        Takes raw 16-bit PCM audio bytes at 16kHz mono
        Returns transcribed text string
        """
        try:
            # Convert bytes to float32 numpy array
            audio_np = np.frombuffer(audio_bytes, dtype=np.int16)
            audio_float = audio_np.astype(np.float32) / 32768.0

            # Transcribe
            segments, info = self.model.transcribe(
                audio_float,
                beam_size=5,
                language="en",
                vad_filter=True,  # Disabled built-in VAD to keep all audio
                vad_parameters=dict(
                    min_silence_duration_ms=400,    # was 500 — detect shorter silences
                    speech_pad_ms=200,              # add padding around speech segments
                    threshold=0.3,                  # lower threshold = keep more audio
                )
            )

            # Collect segments
            text = " ".join([seg.text.strip() for seg in segments])
            text = text.strip()

            # Filter out Whisper hallucinations on near-silence
            hallucinations = {
                "thank you", "thanks for watching",
                "I'll see you guys in the next video.",
                "thanks", "bye", "goodbye",
                "you", ".", ""
            }
            if text.lower().strip(".").strip() in hallucinations:
                logger.info(f"Filtered hallucination: '{text}'")
                return ""

            if text:
                logger.info(f"Transcribed: '{text}'")
            else:
                logger.info("No speech detected in audio")

            return text

        except Exception as e:
            logger.error(f"STT error: {e}")
            return ""