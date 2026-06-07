from faster_whisper import WhisperModel
from config import WHISPER_MODEL, WHISPER_DEVICE, WHISPER_COMPUTE
import numpy as np
import logging
import re
import string

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

    def _normalize_text(self, text: str) -> str:
        text = text.lower().strip()
        text = text.translate(str.maketrans("", "", string.punctuation))
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def _is_hallucination(self, text: str) -> bool:
        normalized = self._normalize_text(text)

        hallucinations = {
            "",
            "you",
            "thank you",
            "thanks",
            "thanks for watching",
            "thank you for watching",
            "see you",
            "see you soon",
            "see you next time",
            "ill see you guys in the next video",
            "i will see you guys in the next video",
            "bye",
            "goodbye",
            "okay bye",
            "bye bye",
            "subscribe",
            "please subscribe",
            "like and subscribe",
            "dont forget to subscribe",
            "if you want to see more videos like this please subscribe",
        }

        if normalized in hallucinations:
            return True

        # Whisper commonly invents YouTube outro lines from noise.
        if "thanks for watching" in normalized:
            return True

        if "thank you for watching" in normalized:
            return True

        if "see you" in normalized and "next video" in normalized:
            return True

        if "more videos like this" in normalized:
            return True

        if "please subscribe" in normalized:
            return True

        if "dont forget to subscribe" in normalized:
            return True

        if "like and subscribe" in normalized:
            return True

        # Strong pattern filter:
        # If transcript contains subscribe plus video/channel/watch,
        # it is almost certainly Whisper outro hallucination.
        subscribe_words = ["subscribe", "subscribed", "subscription"]
        outro_context_words = [
            "video", "videos", "channel", "watching",
            "like", "comment", "share", "bell", "notification"
        ]

        has_subscribe = any(word in normalized for word in subscribe_words)
        has_outro_context = any(word in normalized for word in outro_context_words)

        if has_subscribe and has_outro_context:
            return True

        return False

    def _audio_stats(self, audio_float: np.ndarray):
        if len(audio_float) == 0:
            return 0.0, 0.0, 0.0

        abs_audio = np.abs(audio_float)
        rms = float(np.sqrt(np.mean(audio_float ** 2)))
        peak = float(np.max(abs_audio))

        # Percent of samples that are clearly above background noise
        active_ratio = float(np.mean(abs_audio > 0.015))

        return rms, peak, active_ratio

    def transcribe(self, audio_bytes: bytes) -> str:
        """
        Takes raw 16-bit PCM audio bytes at 16kHz mono.
        Returns transcribed text string.
        """
        try:
            audio_np = np.frombuffer(audio_bytes, dtype=np.int16)

            if audio_np.size == 0:
                logger.info("Rejected empty audio")
                return ""

            duration = audio_np.size / 16000.0
            audio_float = audio_np.astype(np.float32) / 32768.0

            rms, peak, active_ratio = self._audio_stats(audio_float)

            logger.info(
                f"Audio stats: duration={duration:.2f}s "
                f"rms={rms:.5f} peak={peak:.5f} active={active_ratio:.3f}"
            )

            # Hard reject tiny/empty/noisy clips before Whisper.
            # This prevents most 'thanks for watching' hallucinations.
            if duration < 0.60:
                logger.info("Rejected audio: too short")
                return ""

            if peak < 0.035:
                logger.info("Rejected audio: peak too low")
                return ""

            if rms < 0.006:
                logger.info("Rejected audio: RMS too low")
                return ""

            if active_ratio < 0.015:
                logger.info("Rejected audio: too little active speech")
                return ""

            segments, info = self.model.transcribe(
                audio_float,
                beam_size=1,
                language="en",
                vad_filter=True,
                condition_on_previous_text=False,
                no_speech_threshold=0.65,
                log_prob_threshold=-1.0,
                compression_ratio_threshold=2.4,
                vad_parameters=dict(
                    min_silence_duration_ms=500,
                    speech_pad_ms=500,
                    threshold=0.35,
                )
            )

            accepted_parts = []

            for seg in segments:
                seg_text = seg.text.strip()

                if not seg_text:
                    continue

                if self._is_hallucination(seg_text):
                    logger.info(f"Filtered hallucination segment: '{seg_text}'")
                    continue

                # Reject weak Whisper guesses
                if hasattr(seg, "no_speech_prob") and seg.no_speech_prob > 0.65:
                    logger.info(
                        f"Rejected segment due to no_speech_prob="
                        f"{seg.no_speech_prob:.2f}: '{seg_text}'"
                    )
                    continue

                if hasattr(seg, "avg_logprob") and seg.avg_logprob < -1.2:
                    logger.info(
                        f"Rejected segment due to avg_logprob="
                        f"{seg.avg_logprob:.2f}: '{seg_text}'"
                    )
                    continue

                accepted_parts.append(seg_text)

            text = " ".join(accepted_parts).strip()

            if self._is_hallucination(text):
                logger.info(f"Filtered hallucination: '{text}'")
                return ""

            # Reject very tiny single-word junk unless it is useful.
            normalized = self._normalize_text(text)
            allowed_short_words = {
                "yes", "no", "on", "off", "stop", "start", "hello", "zyra", "jarvis"
            }

            if len(normalized.split()) == 1 and normalized not in allowed_short_words:
                logger.info(f"Rejected weak single-word transcript: '{text}'")
                return ""

            if text:
                logger.info(f"Transcribed: '{text}'")
            else:
                logger.info("No speech detected in audio")

            return text

        except Exception as e:
            logger.error(f"STT error: {e}")
            return ""